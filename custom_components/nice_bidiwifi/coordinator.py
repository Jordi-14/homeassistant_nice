"""Coordinator for Nice BiDi-WiFi."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import json
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    NiceBidiAuthError,
    NiceBidiClient,
    NiceBidiConnectionError,
    NiceBidiCredentials,
    NiceBidiDeviceInfo,
    NiceBidiError,
    NiceBidiStatus,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_STOPPED,
)
from .const import (
    CONF_SOURCE_ID,
    CONF_DEVICE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DEFAULT_DEVICE_ID,
    DEFAULT_PORT,
    DEFAULT_T4_TIMEOUT_MS,
    DOMAIN,
    ERROR_UPDATE_INTERVAL,
    IDLE_UPDATE_INTERVAL,
    MOVING_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CONNECTION_STATE_AUTH_FAILED = "auth_failed"
CONNECTION_STATE_CONNECTED = "connected"
CONNECTION_STATE_FAILED = "failed"
CONNECTION_STATE_RECONNECTING = "reconnecting"
CONNECTION_STATE_UNKNOWN = "unknown"

POSITION_TARGET_POLL_SECONDS = 0.5
POSITION_TARGET_TOLERANCE = 1.0
POST_COMMAND_REFRESH_DELAY_SECONDS = 2.0

CALIBRATION_STORAGE_VERSION = 1
CALIBRATION_TARGETS = (20, 40, 60, 80)
CALIBRATION_SETTLE_SECONDS = 2.0
CALIBRATION_COMMAND_PAUSE_SECONDS = 0.5
CALIBRATION_SETTLE_TIMEOUT_SECONDS = 8.0
CALIBRATION_MOVEMENT_TIMEOUT_SECONDS = 90.0
CALIBRATION_ENDPOINT_TOLERANCE = 1.0
CALIBRATION_MAX_ATTEMPTS = 5
CALIBRATION_STABILITY_ATTEMPTS = 2
CALIBRATION_TARGET_TOLERANCE_PERCENT = 2.0
CALIBRATION_OUTLIER_ERROR_PERCENT = 15.0
CALIBRATION_REPORT_ATTRIBUTE_EVENT_LIMIT = 5
CALIBRATION_REPORT_LOG_CHUNK_SIZE = 6000

CALIBRATION_STATE_CALIBRATED = "calibrated"
CALIBRATION_STATE_CANCELLED = "cancelled"
CALIBRATION_STATE_FAILED = "failed"
CALIBRATION_STATE_NOT_CALIBRATED = "not_calibrated"
CALIBRATION_STATE_RUNNING = "running"


class NiceBidiDataUpdateCoordinator(DataUpdateCoordinator[NiceBidiStatus]):
    """DataUpdateCoordinator for one Nice BiDi-WiFi interface."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = entry
        self.connection_state = CONNECTION_STATE_UNKNOWN
        self.device_info: NiceBidiDeviceInfo | None = None
        self.last_command: str | None = None
        self.last_command_latency_ms: int | None = None
        self.last_error: str | None = None
        self.last_successful_update: datetime | None = None
        self._position_target_task: asyncio.Task[None] | None = None
        self._post_command_refresh_task: asyncio.Task[None] | None = None
        self._calibration_task: asyncio.Task[None] | None = None
        self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
        self.calibration_last_error: str | None = None
        self.calibration_updated_at: datetime | None = None
        self.calibration_profile: dict[str, Any] | None = None
        self.calibration_report: dict[str, Any] | None = None
        self._calibration_events: list[dict[str, Any]] = []
        self._calibration_store = Store[dict[str, Any]](
            hass,
            CALIBRATION_STORAGE_VERSION,
            f"{DOMAIN}.calibration.{entry.entry_id}",
        )
        data = entry.data
        credentials = NiceBidiCredentials(
            username=data[CONF_USERNAME],
            password_hex=data[CONF_PASSWORD],
            target_mac=data[CONF_TARGET_MAC],
            source_id=data.get(CONF_SOURCE_ID) or None,
        )
        self.client = NiceBidiClient(
            host=data[CONF_HOST],
            port=data.get(CONF_PORT, DEFAULT_PORT),
            credentials=credentials,
            device_id=data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            t4_timeout_ms=data.get(CONF_T4_TIMEOUT_MS, DEFAULT_T4_TIMEOUT_MS),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=IDLE_UPDATE_INTERVAL,
            config_entry=entry,
        )

    async def async_load_calibration(self) -> None:
        """Load stored position calibration data."""
        try:
            stored_profile = await self._calibration_store.async_load()
        except Exception as err:
            self.calibration_profile = None
            self.calibration_report = None
            self.calibration_updated_at = None
            self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
            self.calibration_last_error = f"Stored calibration could not be loaded: {err}"
            _LOGGER.warning("Nice BiDi-WiFi stored calibration could not be loaded: %s", err)
            return

        if not isinstance(stored_profile, dict):
            self.calibration_profile = None
            self.calibration_report = None
            self.calibration_updated_at = None
            self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
            self.calibration_last_error = None
            return

        self.calibration_profile = stored_profile
        self.calibration_state = CALIBRATION_STATE_CALIBRATED
        self.calibration_last_error = None
        self.calibration_report = self._build_calibration_report(stored_profile)
        updated_at = self.calibration_profile.get("updated_at")
        if isinstance(updated_at, str):
            with suppress(ValueError):
                self.calibration_updated_at = datetime.fromisoformat(updated_at)

    async def _async_update_data(self) -> NiceBidiStatus:
        """Fetch state from the BiDi."""
        try:
            if self.connection_state == CONNECTION_STATE_FAILED:
                self.connection_state = CONNECTION_STATE_RECONNECTING
            status = await self.hass.async_add_executor_job(self._read_status_and_maybe_info)
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            raise UpdateFailed(str(err)) from err

        self._store_successful_status(status)
        return status

    def _read_status_and_maybe_info(self) -> NiceBidiStatus:
        """Read dynamic status and cache static device info."""
        status = self.client.read_status()
        if self.device_info is None:
            try:
                self.device_info = self.client.read_info()
            except NiceBidiError as err:
                _LOGGER.debug("Could not read Nice BiDi-WiFi INFO metadata: %s", err)
        return status

    def _store_successful_status(self, status: NiceBidiStatus) -> None:
        """Store successful status read metadata."""
        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_error = None
        self.last_successful_update = datetime.now(UTC)
        self.update_interval = MOVING_UPDATE_INTERVAL if status.is_moving else IDLE_UPDATE_INTERVAL

    async def _async_cancel_position_target(self) -> None:
        """Cancel a pending target-position watcher."""
        task = self._position_target_task
        if task is None:
            return
        self._position_target_task = None
        if task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_cancel_post_command_refresh(self) -> None:
        """Cancel a pending delayed post-command refresh."""
        task = self._post_command_refresh_task
        if task is None:
            return
        self._post_command_refresh_task = None
        if task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_cancel_calibration(self, *, stop: bool = True) -> None:
        """Cancel a running calibration task."""
        task = self._calibration_task
        if task is None:
            return
        if task.done():
            self._calibration_task = None
            return
        if task is asyncio.current_task():
            return

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if stop:
            with suppress(HomeAssistantError):
                await self._async_send_action("stop")

    async def async_send_action(self, action: str) -> None:
        """Send an open, close, or stop command."""
        await self._async_cancel_position_target()
        await self._async_cancel_calibration(stop=action != "stop")
        await self._async_send_action(action)

    async def _async_send_action(self, action: str, *, refresh: bool = True) -> None:
        """Send an open, close, or stop command without touching target watchers."""
        started = time.monotonic()
        try:
            await self.hass.async_add_executor_job(self.client.send_action, action)
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            raise HomeAssistantError(f"Nice BiDi-WiFi authentication failed: {err}") from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            raise HomeAssistantError(f"Nice BiDi-WiFi command failed: {err}") from err

        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_command = action
        self.last_command_latency_ms = round((time.monotonic() - started) * 1000)
        self.last_error = None
        self.update_interval = MOVING_UPDATE_INTERVAL if action in {"open", "close"} else IDLE_UPDATE_INTERVAL
        if refresh:
            self._schedule_post_command_refresh()
            await self.async_request_refresh()

    def _schedule_post_command_refresh(self) -> None:
        """Schedule a delayed refresh so motor state is not missed after commands."""
        task = self._post_command_refresh_task
        if task is not None and not task.done():
            task.cancel()
        self._post_command_refresh_task = self.hass.async_create_task(
            self._async_post_command_refresh(),
            name=f"{DOMAIN} post-command refresh",
        )

    async def _async_post_command_refresh(self) -> None:
        """Refresh after the gate controller has had time to enter its new state."""
        task = asyncio.current_task()
        try:
            await asyncio.sleep(POST_COMMAND_REFRESH_DELAY_SECONDS)
            await self.async_request_refresh()
        except asyncio.CancelledError:
            raise
        finally:
            if self._post_command_refresh_task is task:
                self._post_command_refresh_task = None

    async def async_set_position(self, target_position: int) -> None:
        """Move toward a target percentage and stop after the target is reached."""
        await self._async_cancel_calibration()
        await self._async_cancel_post_command_refresh()
        target = max(0, min(100, target_position))
        status = self.data
        if status is None or status.position is None:
            await self.async_request_refresh()
            status = self.data
        if status is None or status.position is None:
            raise HomeAssistantError("Nice BiDi-WiFi position is not available")

        current = status.position
        if target <= 0:
            await self.async_send_action("close")
            return
        if target >= 100:
            await self.async_send_action("open")
            return
        if abs(current - target) <= POSITION_TARGET_TOLERANCE:
            if status.is_moving:
                await self.async_send_action("stop")
            return

        action = "open" if target > current else "close"
        stop_raw = self._calibrated_stop_raw(target, action, status)
        await self._async_cancel_position_target()
        await self._async_send_action(action, refresh=False)
        self._position_target_task = self.hass.async_create_task(
            self._async_stop_at_position(target, action, stop_raw),
            name=f"{DOMAIN} stop at {target}%",
        )

    async def _async_stop_at_position(self, target: int, action: str, stop_raw: int | None = None) -> None:
        """Poll live position and stop after crossing the target."""
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        terminal_states = {STATE_OPEN, STATE_CLOSED, STATE_STOPPED}
        task = asyncio.current_task()
        started_moving = False
        movement_start_deadline = time.monotonic() + 8.0
        try:
            while True:
                await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
                status = await self.hass.async_add_executor_job(self.client.read_status)
                self._store_successful_status(status)
                self.async_set_updated_data(status)

                position = status.position
                if position is None:
                    continue
                if stop_raw is not None and status.current_position is not None:
                    if self._raw_reached(action, status, status.current_position, stop_raw):
                        await self._async_send_action("stop")
                        return
                else:
                    if action == "open" and position >= target:
                        await self._async_send_action("stop")
                        return
                    if action == "close" and position <= target:
                        await self._async_send_action("stop")
                        return
                if status.state == moving_state:
                    started_moving = True
                    continue
                if started_moving and (status.state in terminal_states or status.state != moving_state):
                    return
                if not started_moving and time.monotonic() > movement_start_deadline:
                    return
        except asyncio.CancelledError:
            raise
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            _LOGGER.warning("Nice BiDi-WiFi target-position authentication failed: %s", err)
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            _LOGGER.warning("Nice BiDi-WiFi target-position tracking failed: %s", err)
        finally:
            if self._position_target_task is task:
                self._position_target_task = None

    async def async_start_position_calibration(self) -> None:
        """Start a background position calibration run."""
        task = self._calibration_task
        if task is not None and not task.done():
            raise HomeAssistantError("Nice BiDi-WiFi position calibration is already running")

        await self._async_cancel_position_target()
        await self._async_cancel_post_command_refresh()
        self._calibration_events = []
        self.calibration_state = CALIBRATION_STATE_RUNNING
        self.calibration_last_error = None
        self.calibration_report = self._build_live_calibration_report("Calibration started")
        self._add_calibration_event(
            "run",
            "Position calibration requested",
            targets=list(CALIBRATION_TARGETS),
            max_attempts=CALIBRATION_MAX_ATTEMPTS,
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
            tolerance_percent=CALIBRATION_TARGET_TOLERANCE_PERCENT,
            poll_seconds=POSITION_TARGET_POLL_SECONDS,
            command_pause_seconds=CALIBRATION_COMMAND_PAUSE_SECONDS,
        )
        self.async_update_listeners()
        self._calibration_task = self.hass.async_create_task(
            self._async_run_position_calibration(),
            name=f"{DOMAIN} position calibration",
        )

    async def _async_run_position_calibration(self) -> None:
        """Run a predefined movement pattern and store learned stop thresholds."""
        task = asyncio.current_task()
        try:
            profile = await self._async_build_position_calibration()
            self._add_calibration_event("run", "Position calibration completed")
            profile["events"] = list(self._calibration_events)
            self.calibration_profile = profile
            self.calibration_state = CALIBRATION_STATE_CALIBRATED
            self.calibration_last_error = None
            self.calibration_report = self._build_calibration_report(profile)
            updated_at = profile.get("updated_at")
            if isinstance(updated_at, str):
                self.calibration_updated_at = datetime.fromisoformat(updated_at)
            await self._calibration_store.async_save(profile)
            self._log_calibration_report(self.calibration_report, "completed")
        except asyncio.CancelledError:
            self.calibration_state = CALIBRATION_STATE_CANCELLED
            self.calibration_last_error = "cancelled"
            self._add_calibration_event("run", "Position calibration cancelled")
            self.calibration_report = self._build_live_calibration_report("Calibration cancelled")
            raise
        except (NiceBidiAuthError, NiceBidiConnectionError, OSError, HomeAssistantError) as err:
            self.calibration_state = CALIBRATION_STATE_FAILED
            self.calibration_last_error = str(err)
            self._add_calibration_event("run", "Position calibration failed", error=str(err))
            self.calibration_report = self._build_live_calibration_report("Calibration failed")
            self._log_calibration_report(self.calibration_report, "failed")
            _LOGGER.warning("Nice BiDi-WiFi position calibration failed: %s", err)
        finally:
            if self._calibration_task is task:
                self._calibration_task = None
            self.async_update_listeners()

    async def _async_build_position_calibration(self) -> dict[str, Any]:
        """Build a direction-specific calibration profile."""
        started_at = datetime.now(UTC)
        self._add_calibration_event("run", "Reading initial status")
        start_status = await self._async_read_motion_status()
        self._validate_position_bounds(start_status)
        self._add_calibration_event(
            "run",
            "Initial encoder bounds read",
            current_raw=start_status.current_position,
            current_percent=start_status.position,
            closed_raw=start_status.closed_position,
            open_raw=start_status.open_position,
            state=start_status.state,
        )

        opening_samples = []
        for target in CALIBRATION_TARGETS:
            self._add_calibration_event("target", f"Opening-side calibration for {target}% started")
            opening_samples.append(await self._async_calibrate_target_from_endpoint(target, "open", "close"))

        self._add_calibration_event("endpoint", "Moving fully open before closing-side calibration")
        open_status = await self._async_move_to_end("open")
        closing_samples = []
        for target in reversed(CALIBRATION_TARGETS):
            self._add_calibration_event("target", f"Closing-side calibration for {target}% started")
            closing_samples.append(await self._async_calibrate_target_from_endpoint(target, "close", "open"))

        self._add_calibration_event("endpoint", "Final close started")
        final_status = await self._async_move_to_end("close")
        self._add_calibration_event(
            "endpoint",
            "Final close completed",
            current_raw=final_status.current_position,
            current_percent=final_status.position,
            state=final_status.state,
        )
        updated_at = datetime.now(UTC)

        return {
            "version": 4,
            "created_at": started_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "poll_seconds": POSITION_TARGET_POLL_SECONDS,
            "settle_seconds": CALIBRATION_SETTLE_SECONDS,
            "command_pause_seconds": CALIBRATION_COMMAND_PAUSE_SECONDS,
            "max_attempts": CALIBRATION_MAX_ATTEMPTS,
            "stability_attempts": CALIBRATION_STABILITY_ATTEMPTS,
            "target_tolerance_percent": CALIBRATION_TARGET_TOLERANCE_PERCENT,
            "targets": list(CALIBRATION_TARGETS),
            "bounds": {
                "initial_closed_raw": start_status.closed_position,
                "initial_open_raw": start_status.open_position,
                "open_run_closed_raw": open_status.closed_position,
                "open_run_open_raw": open_status.open_position,
                "final_closed_raw": final_status.closed_position,
                "final_open_raw": final_status.open_position,
                "final_state": final_status.state,
            },
            "samples": {
                "open": sorted(opening_samples, key=lambda sample: sample["target_percent"]),
                "close": sorted(closing_samples, key=lambda sample: sample["target_percent"]),
            },
        }

    async def _async_calibrate_target_from_endpoint(
        self,
        target: int,
        action: str,
        endpoint_action: str,
    ) -> dict[str, Any]:
        """Calibrate one target by retrying from a known endpoint."""
        attempts = []
        stop_raw: int | None = None

        for attempt in range(1, CALIBRATION_MAX_ATTEMPTS + 1):
            self._add_calibration_event(
                "attempt",
                f"{action} {target}% attempt {attempt}: moving to known {endpoint_action} endpoint",
                action=action,
                target_percent=target,
                attempt=attempt,
                endpoint_action=endpoint_action,
                requested_stop_raw=stop_raw,
            )
            endpoint_status = await self._async_move_to_end(endpoint_action)
            if stop_raw is None:
                stop_raw = self._raw_for_percent(endpoint_status, target)
                self._add_calibration_event(
                    "attempt",
                    f"{action} {target}% attempt {attempt}: using nominal first stop threshold",
                    action=action,
                    target_percent=target,
                    attempt=attempt,
                    requested_stop_raw=stop_raw,
                    requested_stop_percent=round(self._percent_for_raw(endpoint_status, stop_raw), 2),
                )

            sample = await self._async_calibrate_target(target, action, endpoint_action, attempt, stop_raw)
            attempts.append(sample)
            selection_so_far = self._select_calibration_sample(attempts)
            stop_raw = selection_so_far["sample"]["corrected_stop_raw"]
            attempt_successful = self._calibration_attempt_successful(sample)
            event_details = {
                "action": action,
                "target_percent": target,
                "attempt": attempt,
                "valid": self._calibration_attempt_valid(sample),
                "successful": attempt_successful,
                "stable_success": self._calibration_attempts_stable(attempts),
                "successful_attempts": self._calibration_success_count(attempts),
                "final_percent": sample["final_percent"],
                "error_percent": sample["error_percent"],
                "requested_stop_percent": sample["requested_stop_percent"],
                "corrected_stop_percent": sample["corrected_stop_percent"],
                "stop_command_latency_ms": sample["stop_command_latency_ms"],
                "move_duration_ms": sample["move_duration_ms"],
            }
            failure_reason = sample.get("failure_reason")
            if failure_reason is not None:
                event_details["failure_reason"] = failure_reason
            if self._calibration_attempt_valid(sample):
                event_message = f"{action} {target}% attempt {attempt}: settled with {sample['error_percent']}% error"
            else:
                event_message = f"{action} {target}% attempt {attempt}: invalid after {failure_reason}"
            self._add_calibration_event("attempt", event_message, **event_details)

            await self._async_move_to_end(endpoint_action)

        selection = self._select_calibration_sample(attempts)
        selected_sample = selection["sample"]
        successful = selection["strategy"] == "stable_window"
        self._add_calibration_event(
            "target",
            f"{action} {target}% calibration finished",
            action=action,
            target_percent=target,
            successful=successful,
            successful_attempts=self._calibration_success_count(attempts),
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
            attempts_used=len(attempts),
            selection_strategy=selection["strategy"],
            selected_attempt=selection["selected_attempt"],
            selected_attempts=selection["selected_attempts"],
            selected_abs_error_percent=selection["selected_abs_error_percent"],
            ignored_outlier_attempts=selection["ignored_outlier_attempts"],
            ignored_invalid_attempts=selection["ignored_invalid_attempts"],
            final_error_percent=selected_sample["error_percent"],
            corrected_stop_percent=selected_sample["corrected_stop_percent"],
        )
        return {
            **selected_sample,
            "successful": successful,
            "successful_attempts": self._calibration_success_count(attempts),
            "stability_attempts": CALIBRATION_STABILITY_ATTEMPTS,
            "attempts_used": len(attempts),
            "selection_strategy": selection["strategy"],
            "selected_attempt": selection["selected_attempt"],
            "selected_attempts": selection["selected_attempts"],
            "selected_window_avg_abs_error_percent": selection["selected_window_avg_abs_error_percent"],
            "selected_abs_error_percent": selection["selected_abs_error_percent"],
            "ignored_outlier_attempts": selection["ignored_outlier_attempts"],
            "ignored_invalid_attempts": selection["ignored_invalid_attempts"],
            "outlier_error_percent": CALIBRATION_OUTLIER_ERROR_PERCENT,
            "last_attempt": attempts[-1],
            "attempts": attempts,
        }

    @staticmethod
    def _calibration_attempt_valid(attempt: dict[str, Any]) -> bool:
        """Return true if an attempt should be used for learned stop thresholds."""
        return attempt.get("valid", True) is not False

    @staticmethod
    def _calibration_attempt_abs_error(attempt: dict[str, Any]) -> float:
        """Return an attempt's absolute percentage error."""
        if not NiceBidiDataUpdateCoordinator._calibration_attempt_valid(attempt):
            return 1000.0
        try:
            return abs(float(attempt.get("error_percent", 1000.0)))
        except (TypeError, ValueError):
            return 1000.0

    @staticmethod
    def _calibration_attempt_successful(attempt: dict[str, Any]) -> bool:
        """Return true if an attempt finished inside the target tolerance."""
        return (
            NiceBidiDataUpdateCoordinator._calibration_attempt_valid(attempt)
            and NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error(attempt)
            <= CALIBRATION_TARGET_TOLERANCE_PERCENT
        )

    @staticmethod
    def _calibration_success_count(attempts: list[dict[str, Any]]) -> int:
        """Return how many attempts finished inside the calibration tolerance."""
        return sum(
            1
            for attempt in attempts
            if NiceBidiDataUpdateCoordinator._calibration_attempt_successful(attempt)
        )

    @staticmethod
    def _calibration_attempts_stable(attempts: list[dict[str, Any]]) -> bool:
        """Return true when any consecutive attempts show repeatable accuracy."""
        if len(attempts) < CALIBRATION_STABILITY_ATTEMPTS:
            return False
        for start in range(0, len(attempts) - CALIBRATION_STABILITY_ATTEMPTS + 1):
            window = attempts[start : start + CALIBRATION_STABILITY_ATTEMPTS]
            if (
                NiceBidiDataUpdateCoordinator._calibration_success_count(window)
                == CALIBRATION_STABILITY_ATTEMPTS
            ):
                return True
        return False

    @staticmethod
    def _select_calibration_sample(attempts: list[dict[str, Any]]) -> dict[str, Any]:
        """Choose the calibration result that should be stored for one target."""
        if not attempts:
            raise HomeAssistantError("Position calibration produced no attempts")

        ignored_invalid_attempts = [
            int(attempt["attempt"])
            for attempt in attempts
            if not NiceBidiDataUpdateCoordinator._calibration_attempt_valid(attempt)
            and isinstance(attempt.get("attempt"), int)
        ]
        valid_attempts = [
            attempt
            for attempt in attempts
            if NiceBidiDataUpdateCoordinator._calibration_attempt_valid(attempt)
        ]
        ignored_outliers = [
            int(attempt["attempt"])
            for attempt in valid_attempts
            if NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error(attempt)
            > CALIBRATION_OUTLIER_ERROR_PERCENT
            and isinstance(attempt.get("attempt"), int)
        ]
        stable_windows: list[tuple[float, int, list[dict[str, Any]]]] = []
        for start in range(0, len(attempts) - CALIBRATION_STABILITY_ATTEMPTS + 1):
            window = attempts[start : start + CALIBRATION_STABILITY_ATTEMPTS]
            if (
                NiceBidiDataUpdateCoordinator._calibration_success_count(window)
                != CALIBRATION_STABILITY_ATTEMPTS
            ):
                continue
            avg_abs_error = sum(
                NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error(attempt)
                for attempt in window
            ) / len(window)
            stable_windows.append((avg_abs_error, start, window))

        if stable_windows:
            avg_abs_error, _, selected_window = min(
                stable_windows,
                key=lambda item: (item[0], -item[1]),
            )
            selected_sample = min(
                selected_window,
                key=NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error,
            )
            return {
                "sample": selected_sample,
                "strategy": "stable_window",
                "selected_attempt": selected_sample.get("attempt"),
                "selected_attempts": [attempt.get("attempt") for attempt in selected_window],
                "selected_window_avg_abs_error_percent": round(avg_abs_error, 2),
                "selected_abs_error_percent": round(
                    NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error(selected_sample), 2
                ),
                "ignored_outlier_attempts": ignored_outliers,
                "ignored_invalid_attempts": ignored_invalid_attempts,
            }

        non_outlier_attempts = [
            attempt
            for attempt in valid_attempts
            if NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error(attempt)
            <= CALIBRATION_OUTLIER_ERROR_PERCENT
        ]
        candidates = non_outlier_attempts or valid_attempts
        if not candidates:
            selected_sample = attempts[-1]
            return {
                "sample": selected_sample,
                "strategy": "no_valid_attempt",
                "selected_attempt": selected_sample.get("attempt"),
                "selected_attempts": [],
                "selected_window_avg_abs_error_percent": None,
                "selected_abs_error_percent": None,
                "ignored_outlier_attempts": ignored_outliers,
                "ignored_invalid_attempts": ignored_invalid_attempts,
            }
        selected_sample = min(
            candidates,
            key=NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error,
        )
        return {
            "sample": selected_sample,
            "strategy": "best_non_outlier_attempt" if non_outlier_attempts else "best_attempt",
            "selected_attempt": selected_sample.get("attempt"),
            "selected_attempts": [selected_sample.get("attempt")],
            "selected_window_avg_abs_error_percent": None,
            "selected_abs_error_percent": round(
                NiceBidiDataUpdateCoordinator._calibration_attempt_abs_error(selected_sample), 2
            ),
            "ignored_outlier_attempts": ignored_outliers,
            "ignored_invalid_attempts": ignored_invalid_attempts,
        }

    async def _async_calibrate_target(
        self,
        target: int,
        action: str,
        endpoint_action: str,
        attempt: int,
        requested_stop_raw: int,
    ) -> dict[str, Any]:
        """Measure one target approach and return its corrected stop threshold."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if status.current_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi current encoder position is not available")

        start_raw = status.current_position
        target_raw = self._raw_for_percent(status, target)
        if self._raw_reached(action, status, start_raw, target_raw):
            raise HomeAssistantError(f"Position calibration already crossed {target}% while preparing to {action}")

        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: movement command sent",
            action=action,
            target_percent=target,
            attempt=attempt,
            start_raw=start_raw,
            start_percent=round(self._percent_for_raw(status, start_raw), 2),
            target_raw=target_raw,
            requested_stop_raw=requested_stop_raw,
            requested_stop_percent=round(self._percent_for_raw(status, requested_stop_raw), 2),
        )
        move_started = time.monotonic()
        await self._async_send_action(action, refresh=False)
        stop_command_raw: int | None = None
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = move_started + 8.0

        while time.monotonic() - move_started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            current_raw = status.current_position
            if current_raw is not None and self._raw_reached(action, status, current_raw, requested_stop_raw):
                stop_command_raw = current_raw
                self._add_calibration_event(
                    "attempt",
                    f"{action} {target}% attempt {attempt}: stop threshold reached",
                    action=action,
                    target_percent=target,
                    attempt=attempt,
                    current_raw=current_raw,
                    current_percent=round(self._percent_for_raw(status, current_raw), 2),
                    requested_stop_raw=requested_stop_raw,
                    requested_stop_percent=round(self._percent_for_raw(status, requested_stop_raw), 2),
                    state=status.state,
                )
                await self._async_send_action("stop", refresh=False)
                break
            if status.state == moving_state:
                started_moving = True
            if status.state in {STATE_OPEN, STATE_CLOSED, STATE_STOPPED} and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                raise HomeAssistantError(f"Position calibration stopped before reaching {target}%")

        if stop_command_raw is None:
            raise HomeAssistantError(f"Timed out calibrating {target}% while moving {action}")

        settled, settle_timed_out = await self._async_wait_for_settle(
            action=action,
            target=target,
            attempt=attempt,
        )
        if settled.current_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi final encoder position is not available")

        if settle_timed_out:
            return self._invalid_calibration_sample(
                action=action,
                endpoint_action=endpoint_action,
                attempt=attempt,
                target=target,
                status=status,
                settled=settled,
                start_raw=start_raw,
                target_raw=target_raw,
                requested_stop_raw=requested_stop_raw,
                stop_command_raw=stop_command_raw,
                move_started=move_started,
                failure_reason="settle_timeout",
            )

        final_raw = settled.current_position
        error_raw = final_raw - target_raw
        corrected_stop_raw = self._clamp_raw(status, stop_command_raw - error_raw)
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        travel_raw = stop_command_raw - start_raw
        speed_raw_per_second = (travel_raw / move_duration_ms) * 1000 if move_duration_ms > 0 else None
        final_percent = round(self._percent_for_raw(settled, final_raw), 2)
        error_percent = round(final_percent - target, 2)
        stop_command_percent = round(self._percent_for_raw(status, stop_command_raw), 2)
        corrected_stop_percent = round(self._percent_for_raw(status, corrected_stop_raw), 2)
        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: settled position read",
            action=action,
            target_percent=target,
            attempt=attempt,
            final_raw=final_raw,
            final_percent=final_percent,
            error_raw=error_raw,
            error_percent=error_percent,
            stop_command_raw=stop_command_raw,
            stop_command_percent=stop_command_percent,
            corrected_stop_raw=corrected_stop_raw,
            corrected_stop_percent=corrected_stop_percent,
            move_duration_ms=move_duration_ms,
            stop_command_latency_ms=self.last_command_latency_ms,
            speed_raw_per_second=round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
        )

        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "valid": True,
            "failure_reason": None,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(self._percent_for_raw(status, start_raw), 2),
            "target_raw": target_raw,
            "requested_stop_raw": requested_stop_raw,
            "requested_stop_percent": round(self._percent_for_raw(status, requested_stop_raw), 2),
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": stop_command_percent,
            "corrected_stop_raw": corrected_stop_raw,
            "corrected_stop_percent": corrected_stop_percent,
            "final_raw": final_raw,
            "final_percent": final_percent,
            "error_raw": error_raw,
            "error_percent": error_percent,
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    def _invalid_calibration_sample(
        self,
        *,
        action: str,
        endpoint_action: str,
        attempt: int,
        target: int,
        status: NiceBidiStatus,
        settled: NiceBidiStatus,
        start_raw: int,
        target_raw: int,
        requested_stop_raw: int,
        stop_command_raw: int,
        move_started: float,
        failure_reason: str,
    ) -> dict[str, Any]:
        """Return an invalid calibration attempt that is excluded from learning."""
        final_raw = settled.current_position
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        travel_raw = stop_command_raw - start_raw
        speed_raw_per_second = (travel_raw / move_duration_ms) * 1000 if move_duration_ms > 0 else None
        final_percent = round(self._percent_for_raw(settled, final_raw), 2) if final_raw is not None else None
        stop_command_percent = round(self._percent_for_raw(status, stop_command_raw), 2)
        requested_stop_percent = round(self._percent_for_raw(status, requested_stop_raw), 2)
        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: marked invalid",
            action=action,
            target_percent=target,
            attempt=attempt,
            failure_reason=failure_reason,
            final_raw=final_raw,
            final_percent=final_percent,
            requested_stop_raw=requested_stop_raw,
            requested_stop_percent=requested_stop_percent,
            stop_command_raw=stop_command_raw,
            stop_command_percent=stop_command_percent,
            move_duration_ms=move_duration_ms,
            stop_command_latency_ms=self.last_command_latency_ms,
            speed_raw_per_second=round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            state=settled.state,
        )
        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "valid": False,
            "failure_reason": failure_reason,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(self._percent_for_raw(status, start_raw), 2),
            "target_raw": target_raw,
            "requested_stop_raw": requested_stop_raw,
            "requested_stop_percent": requested_stop_percent,
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": stop_command_percent,
            "corrected_stop_raw": requested_stop_raw,
            "corrected_stop_percent": requested_stop_percent,
            "final_raw": final_raw,
            "final_percent": final_percent,
            "error_raw": None,
            "error_percent": None,
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    async def _async_move_to_end(self, action: str) -> NiceBidiStatus:
        """Move fully open or closed for a known calibration starting point."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if self._is_at_endpoint(status, action):
            self._add_calibration_event(
                "endpoint",
                f"Already at {action} endpoint",
                action=action,
                current_raw=status.current_position,
                current_percent=status.position,
                state=status.state,
            )
            return status

        self._add_calibration_event(
            "endpoint",
            f"Moving to {action} endpoint",
            action=action,
            current_raw=status.current_position,
            current_percent=status.position,
            state=status.state,
        )
        await self._async_send_action(action, refresh=False)
        started = time.monotonic()
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + 8.0
        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                self._add_calibration_event(
                    "endpoint",
                    f"Reached {action} endpoint",
                    action=action,
                    current_raw=status.current_position,
                    current_percent=status.position,
                    state=status.state,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                await self._async_pause_before_next_calibration_command()
                return status
            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (started_moving or time.monotonic() > movement_start_deadline):
                raise HomeAssistantError(f"Position calibration stopped before reaching {action} endpoint")

        raise HomeAssistantError(f"Timed out moving to {action} endpoint during position calibration")

    async def _async_wait_for_settle(
        self,
        *,
        action: str,
        target: int,
        attempt: int,
    ) -> tuple[NiceBidiStatus, bool]:
        """Wait after a stop command and return whether settling timed out."""
        await asyncio.sleep(CALIBRATION_SETTLE_SECONDS)
        status = await self._async_read_motion_status()
        started = time.monotonic()
        while status.is_moving and time.monotonic() - started < CALIBRATION_SETTLE_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
        settle_timed_out = status.is_moving
        if settle_timed_out:
            self._add_calibration_event(
                "attempt",
                f"{action} {target}% attempt {attempt}: still moving after settle timeout",
                action=action,
                target_percent=target,
                attempt=attempt,
                current_raw=status.current_position,
                current_percent=status.position,
                state=status.state,
                settle_timeout_seconds=CALIBRATION_SETTLE_TIMEOUT_SECONDS,
            )
            await self._async_send_action("stop", refresh=False)
            await asyncio.sleep(CALIBRATION_COMMAND_PAUSE_SECONDS)
            status = await self._async_read_motion_status()
        await self._async_pause_before_next_calibration_command()
        return status, settle_timed_out

    async def _async_pause_before_next_calibration_command(self) -> None:
        """Give the gate controller a small quiet period before the next command."""
        if CALIBRATION_COMMAND_PAUSE_SECONDS > 0:
            await asyncio.sleep(CALIBRATION_COMMAND_PAUSE_SECONDS)

    async def _async_read_motion_status(self) -> NiceBidiStatus:
        """Read status during motion-sensitive operations."""
        try:
            status = await self.hass.async_add_executor_job(self.client.read_status)
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            raise
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            raise

        self._store_successful_status(status)
        self.async_set_updated_data(status)
        return status

    def _calibrated_stop_raw(self, target: int, action: str, status: NiceBidiStatus) -> int | None:
        """Return an interpolated calibrated stop threshold for a target."""
        profile = self.calibration_profile
        if profile is None:
            return None

        profile_samples = profile.get("samples")
        if not isinstance(profile_samples, dict):
            return None
        samples = profile_samples.get(action)
        if not isinstance(samples, list):
            return None

        points: list[tuple[float, float]] = [(0.0, 0.0), (100.0, 100.0)]
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            if sample.get("valid", True) is False or sample.get("selection_strategy") == "no_valid_attempt":
                continue
            target_percent = sample.get("target_percent")
            corrected_stop_percent = sample.get("corrected_stop_percent")
            selected_abs_error = sample.get(
                "selected_abs_error_percent",
                sample.get("error_percent"),
            )
            if not isinstance(selected_abs_error, (int, float)):
                continue
            if (
                abs(float(selected_abs_error)) > CALIBRATION_OUTLIER_ERROR_PERCENT
            ):
                continue
            if isinstance(target_percent, (int, float)) and isinstance(
                corrected_stop_percent,
                (int, float),
            ):
                points.append((float(target_percent), float(corrected_stop_percent)))
        if len(points) <= 2:
            return None

        points.sort(key=lambda item: item[0])
        stop_percent = self._interpolate_stop_percent(float(target), points)
        return self._raw_for_percent(status, stop_percent)

    @staticmethod
    def _interpolate_stop_percent(target: float, points: list[tuple[float, float]]) -> float:
        """Interpolate a corrected stop percentage from calibration points."""
        previous_target, previous_stop = points[0]
        for next_target, next_stop in points[1:]:
            if target <= next_target:
                if next_target == previous_target:
                    return max(0.0, min(100.0, next_stop))
                ratio = (target - previous_target) / (next_target - previous_target)
                stop = previous_stop + ((next_stop - previous_stop) * ratio)
                return max(0.0, min(100.0, stop))
            previous_target, previous_stop = next_target, next_stop
        return max(0.0, min(100.0, points[-1][1]))

    @staticmethod
    def _validate_position_bounds(status: NiceBidiStatus) -> None:
        """Validate that encoder endpoints are available."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are not available")
        if status.closed_position == status.open_position:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are identical")

    @staticmethod
    def _raw_for_percent(status: NiceBidiStatus, percent: float) -> int:
        """Convert percentage to raw encoder position."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are not available")
        span = status.open_position - status.closed_position
        raw = status.closed_position + (span * (percent / 100.0))
        return round(raw)

    @staticmethod
    def _percent_for_raw(status: NiceBidiStatus, raw: int) -> float:
        """Convert raw encoder position to percentage."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are not available")
        span = status.open_position - status.closed_position
        if span == 0:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are identical")
        return max(0.0, min(100.0, ((raw - status.closed_position) / span) * 100.0))

    @staticmethod
    def _raw_reached(action: str, status: NiceBidiStatus, current_raw: int, target_raw: int) -> bool:
        """Return true if raw position crossed target in the requested direction."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are not available")
        increasing = status.open_position > status.closed_position
        if action == "open":
            return current_raw >= target_raw if increasing else current_raw <= target_raw
        return current_raw <= target_raw if increasing else current_raw >= target_raw

    @staticmethod
    def _clamp_raw(status: NiceBidiStatus, raw: int) -> int:
        """Clamp raw position to known encoder bounds."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi encoder endpoints are not available")
        lower = min(status.closed_position, status.open_position)
        upper = max(status.closed_position, status.open_position)
        return max(lower, min(upper, raw))

    @staticmethod
    def _is_at_endpoint(status: NiceBidiStatus, action: str) -> bool:
        """Return true when status is at the requested endpoint."""
        if action == "open":
            open_by_position = (
                not status.is_moving
                and status.position is not None
                and status.position >= 100.0 - CALIBRATION_ENDPOINT_TOLERANCE
            )
            return status.state == STATE_OPEN or open_by_position
        closed_by_position = (
            not status.is_moving
            and status.position is not None
            and status.position <= CALIBRATION_ENDPOINT_TOLERANCE
        )
        return status.state == STATE_CLOSED or closed_by_position

    @property
    def calibration_quality(self) -> str | None:
        """Return the latest calibration quality grade."""
        if self.calibration_report is None:
            return None
        quality = self.calibration_report.get("quality")
        return str(quality) if quality is not None else None

    @property
    def calibration_report_summary(self) -> str | None:
        """Return a compact calibration report summary."""
        if self.calibration_report is None:
            return None
        summary = self.calibration_report.get("summary")
        return str(summary)[:255] if summary is not None else None

    @property
    def calibration_report_attributes(self) -> dict[str, Any]:
        """Return recorder-safe calibration report attributes."""
        if self.calibration_report is None:
            return {}

        report = self.calibration_report
        events = report.get("events")
        points = report.get("points")
        return {
            "state": report.get("state"),
            "quality": report.get("quality"),
            "summary": report.get("summary"),
            "updated_at": report.get("updated_at"),
            "profile_version": report.get("profile_version"),
            "tolerance_percent": report.get("tolerance_percent"),
            "poll_seconds": report.get("poll_seconds"),
            "settle_seconds": report.get("settle_seconds"),
            "command_pause_seconds": report.get("command_pause_seconds"),
            "max_attempts": report.get("max_attempts"),
            "stability_attempts": report.get("stability_attempts"),
            "point_count": report.get("point_count"),
            "successful_points": report.get("successful_points"),
            "invalid_points": report.get("invalid_points"),
            "failed_points": self._compact_calibration_failed_points(report.get("failed_points")),
            "total_attempts": report.get("total_attempts"),
            "max_attempts_used": report.get("max_attempts_used"),
            "max_abs_error_percent": report.get("max_abs_error_percent"),
            "avg_abs_error_percent": report.get("avg_abs_error_percent"),
            "bounds": report.get("bounds"),
            "points": self._compact_calibration_points(points),
            "event_count": len(events) if isinstance(events, list) else None,
            "last_events": self._compact_calibration_events(events),
            "full_report_log_prefix": "Nice BiDi-WiFi calibration report",
            "full_report_note": (
                "Full report is written to Home Assistant logs in chunks when calibration finishes or fails."
            ),
        }

    def _add_calibration_event(self, stage: str, message: str, **details: Any) -> None:
        """Record one detailed calibration event and write it to HA logs."""
        event = {
            "index": len(self._calibration_events) + 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "stage": stage,
            "message": message,
            "details": details,
        }
        self._calibration_events.append(event)
        if details:
            _LOGGER.info("Nice BiDi-WiFi calibration: %s | %s", message, json.dumps(details, sort_keys=True))
        else:
            _LOGGER.info("Nice BiDi-WiFi calibration: %s", message)

        if self.calibration_state == CALIBRATION_STATE_RUNNING:
            self.calibration_report = self._build_live_calibration_report(message)
            self.async_update_listeners()

    @staticmethod
    def _compact_calibration_failed_points(failed_points: Any) -> list[dict[str, Any]]:
        """Return a small failed-point list suitable for state attributes."""
        if not isinstance(failed_points, list):
            return []
        compact_points = []
        for point in failed_points:
            if not isinstance(point, dict):
                continue
            compact_points.append(
                {
                    "direction": point.get("direction"),
                    "target_percent": point.get("target_percent"),
                    "final_error_percent": point.get("final_error_percent"),
                    "attempts_used": point.get("attempts_used"),
                    "selection_strategy": point.get("selection_strategy"),
                    "selected_attempt": point.get("selected_attempt"),
                    "selected_abs_error_percent": point.get("selected_abs_error_percent"),
                    "ignored_invalid_attempts": point.get("ignored_invalid_attempts"),
                    "valid": point.get("valid"),
                    "failure_reason": point.get("failure_reason"),
                }
            )
        return compact_points

    @staticmethod
    def _compact_calibration_points(points: Any) -> list[dict[str, Any]]:
        """Return point summaries without per-attempt details."""
        if not isinstance(points, list):
            return []
        compact_points = []
        for point in points:
            if not isinstance(point, dict):
                continue
            compact_points.append(
                {
                    "direction": point.get("direction"),
                    "valid": point.get("valid"),
                    "failure_reason": point.get("failure_reason"),
                    "target_percent": point.get("target_percent"),
                    "successful": point.get("successful"),
                    "successful_attempts": point.get("successful_attempts"),
                    "attempts_used": point.get("attempts_used"),
                    "selection_strategy": point.get("selection_strategy"),
                    "selected_attempt": point.get("selected_attempt"),
                    "selected_attempts": point.get("selected_attempts"),
                    "selected_abs_error_percent": point.get("selected_abs_error_percent"),
                    "selected_window_avg_abs_error_percent": point.get("selected_window_avg_abs_error_percent"),
                    "ignored_outlier_attempts": point.get("ignored_outlier_attempts"),
                    "ignored_invalid_attempts": point.get("ignored_invalid_attempts"),
                    "final_percent": point.get("final_percent"),
                    "final_error_percent": point.get("final_error_percent"),
                    "corrected_stop_percent": point.get("corrected_stop_percent"),
                }
            )
        return compact_points

    @staticmethod
    def _compact_calibration_events(events: Any) -> list[dict[str, Any]]:
        """Return the last calibration events without bulky details."""
        if not isinstance(events, list):
            return []
        compact_events = []
        for event in events[-CALIBRATION_REPORT_ATTRIBUTE_EVENT_LIMIT:]:
            if not isinstance(event, dict):
                continue
            compact_events.append(
                {
                    "index": event.get("index"),
                    "timestamp": event.get("timestamp"),
                    "stage": event.get("stage"),
                    "message": event.get("message"),
                }
            )
        return compact_events

    def _log_calibration_report(self, report: dict[str, Any], reason: str) -> None:
        """Write the full calibration report to HA logs in bounded chunks."""
        report_text = self._format_calibration_report(report)
        total_chunks = max(
            1,
            (len(report_text) + CALIBRATION_REPORT_LOG_CHUNK_SIZE - 1) // CALIBRATION_REPORT_LOG_CHUNK_SIZE,
        )
        _LOGGER.warning(
            "Nice BiDi-WiFi calibration report %s: writing %s bytes in %s chunks",
            reason,
            len(report_text),
            total_chunks,
        )
        for chunk_index in range(total_chunks):
            start = chunk_index * CALIBRATION_REPORT_LOG_CHUNK_SIZE
            chunk = report_text[start : start + CALIBRATION_REPORT_LOG_CHUNK_SIZE]
            _LOGGER.warning(
                "Nice BiDi-WiFi calibration report %s chunk %s/%s:\n%s",
                reason,
                chunk_index + 1,
                total_chunks,
                chunk,
            )

    def _build_live_calibration_report(self, summary: str) -> dict[str, Any]:
        """Build a report while calibration is still running or failed."""
        report = {
            "state": self.calibration_state,
            "quality": self.calibration_state,
            "summary": summary,
            "updated_at": datetime.now(UTC).isoformat(),
            "tolerance_percent": CALIBRATION_TARGET_TOLERANCE_PERCENT,
            "poll_seconds": POSITION_TARGET_POLL_SECONDS,
            "settle_seconds": CALIBRATION_SETTLE_SECONDS,
            "command_pause_seconds": CALIBRATION_COMMAND_PAUSE_SECONDS,
            "max_attempts": CALIBRATION_MAX_ATTEMPTS,
            "stability_attempts": CALIBRATION_STABILITY_ATTEMPTS,
            "events": list(self._calibration_events),
        }
        return report

    def _build_calibration_report(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Build quality metrics and a copyable report from a calibration profile."""
        points = self._calibration_points(profile)
        events = profile.get("events", [])
        tolerance = float(profile.get("target_tolerance_percent") or CALIBRATION_TARGET_TOLERANCE_PERCENT)

        if not points:
            report = {
                "state": self.calibration_state,
                "quality": "unknown",
                "summary": "No calibration points found in the stored profile",
                "profile": profile,
                "points": [],
                "events": events if isinstance(events, list) else [],
            }
            return report

        abs_errors = [
            abs(float(error))
            for point in points
            if isinstance((error := point.get("final_error_percent")), (int, float))
        ]
        invalid_points = sum(1 for point in points if point.get("valid", True) is False)
        attempts_used = [int(point["attempts_used"]) for point in points]
        success_count = sum(1 for point in points if point["successful"])
        total_points = len(points)
        max_abs_error = round(max(abs_errors), 2) if abs_errors else None
        avg_abs_error = round(sum(abs_errors) / len(abs_errors), 2) if abs_errors else None
        total_attempts = sum(attempts_used)
        max_attempts_used = max(attempts_used)
        failed_points = [
            {
                "direction": point["direction"],
                "target_percent": point["target_percent"],
                "final_error_percent": point["final_error_percent"],
                "attempts_used": point["attempts_used"],
                "selection_strategy": point.get("selection_strategy"),
                "selected_attempt": point.get("selected_attempt"),
                "selected_abs_error_percent": point.get("selected_abs_error_percent"),
                "ignored_invalid_attempts": point.get("ignored_invalid_attempts"),
                "valid": point.get("valid"),
                "failure_reason": point.get("failure_reason"),
            }
            for point in points
            if not point["successful"]
        ]

        quality = self._calibration_quality(
            success_count,
            total_points,
            max_abs_error if max_abs_error is not None else 1000.0,
            avg_abs_error if avg_abs_error is not None else 1000.0,
            max_attempts_used,
        )
        max_error_text = f"{max_abs_error:.2f}%" if max_abs_error is not None else "unknown"
        avg_error_text = f"{avg_abs_error:.2f}%" if avg_abs_error is not None else "unknown"
        summary = (
            f"{quality}: {success_count}/{total_points} repeatable targets within {tolerance:g}%"
            f"; max error {max_error_text}; avg error {avg_error_text}"
            f"; attempts {total_attempts}; events {len(events) if isinstance(events, list) else 0}"
        )
        if invalid_points:
            summary = f"{summary}; invalid points {invalid_points}"
        report = {
            "state": self.calibration_state,
            "quality": quality,
            "summary": summary,
            "updated_at": profile.get("updated_at"),
            "profile_version": profile.get("version"),
            "tolerance_percent": tolerance,
            "poll_seconds": profile.get("poll_seconds"),
            "settle_seconds": profile.get("settle_seconds"),
            "command_pause_seconds": profile.get("command_pause_seconds"),
            "max_attempts": profile.get("max_attempts"),
            "stability_attempts": profile.get("stability_attempts"),
            "point_count": total_points,
            "successful_points": success_count,
            "invalid_points": invalid_points,
            "failed_points": failed_points,
            "total_attempts": total_attempts,
            "max_attempts_used": max_attempts_used,
            "max_abs_error_percent": max_abs_error,
            "avg_abs_error_percent": avg_abs_error,
            "bounds": profile.get("bounds", {}),
            "points": points,
            "events": events if isinstance(events, list) else [],
        }
        return report

    @staticmethod
    def _calibration_quality(
        success_count: int,
        total_points: int,
        max_abs_error: float,
        avg_abs_error: float,
        max_attempts_used: int,
    ) -> str:
        """Return a simple quality grade for a calibration result."""
        if success_count < total_points:
            return "needs_review"
        if max_abs_error <= 0.5 and avg_abs_error <= 0.35 and max_attempts_used <= 2:
            return "excellent"
        if max_abs_error <= 1.0:
            return "good"
        if max_abs_error <= 2.0:
            return "usable"
        return "needs_review"

    @staticmethod
    def _calibration_points(profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten stored calibration samples into report points."""
        samples_by_direction = profile.get("samples")
        if not isinstance(samples_by_direction, dict):
            return []

        points: list[dict[str, Any]] = []
        for direction in ("open", "close"):
            samples = samples_by_direction.get(direction, [])
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                attempts = sample.get("attempts", [])
                points.append(
                    {
                        "direction": direction,
                        "valid": sample.get("valid", True),
                        "failure_reason": sample.get("failure_reason"),
                        "target_percent": sample.get("target_percent"),
                        "successful": bool(sample.get("successful")),
                        "successful_attempts": sample.get("successful_attempts"),
                        "stability_attempts": sample.get("stability_attempts"),
                        "attempts_used": int(sample.get("attempts_used") or 0),
                        "selection_strategy": sample.get("selection_strategy"),
                        "selected_attempt": sample.get("selected_attempt"),
                        "selected_attempts": sample.get("selected_attempts"),
                        "selected_window_avg_abs_error_percent": sample.get(
                            "selected_window_avg_abs_error_percent"
                        ),
                        "selected_abs_error_percent": sample.get("selected_abs_error_percent"),
                        "ignored_outlier_attempts": sample.get("ignored_outlier_attempts"),
                        "ignored_invalid_attempts": sample.get("ignored_invalid_attempts"),
                        "outlier_error_percent": sample.get("outlier_error_percent"),
                        "final_percent": sample.get("final_percent"),
                        "final_error_percent": sample.get("error_percent"),
                        "final_error_raw": sample.get("error_raw"),
                        "corrected_stop_percent": sample.get("corrected_stop_percent"),
                        "corrected_stop_raw": sample.get("corrected_stop_raw"),
                        "final_attempt": {
                            "valid": sample.get("valid", True),
                            "failure_reason": sample.get("failure_reason"),
                            "attempt": sample.get("attempt"),
                            "requested_stop_percent": sample.get("requested_stop_percent"),
                            "stop_command_percent": sample.get("stop_command_percent"),
                            "final_percent": sample.get("final_percent"),
                            "error_percent": sample.get("error_percent"),
                            "move_duration_ms": sample.get("move_duration_ms"),
                            "stop_command_latency_ms": sample.get("stop_command_latency_ms"),
                            "speed_raw_per_second": sample.get("speed_raw_per_second"),
                        },
                        "last_attempt": sample.get("last_attempt"),
                        "attempts": attempts if isinstance(attempts, list) else [],
                    }
                )
        return points

    @staticmethod
    def _format_calibration_report(report: dict[str, Any]) -> str:
        """Format a calibration report as copyable plain text."""
        lines = [
            "Nice BiDi-WiFi position calibration report",
            f"State: {report.get('state')}",
            f"Quality: {report.get('quality')}",
            f"Summary: {report.get('summary')}",
            f"Updated at: {report.get('updated_at')}",
            f"Tolerance: {report.get('tolerance_percent')}%",
            f"Poll seconds: {report.get('poll_seconds')}",
            f"Settle seconds: {report.get('settle_seconds')}",
            f"Command pause seconds: {report.get('command_pause_seconds')}",
            f"Stability attempts: {report.get('stability_attempts')}",
        ]

        bounds = report.get("bounds")
        if isinstance(bounds, dict) and bounds:
            lines.append("")
            lines.append("Bounds:")
            for key, value in sorted(bounds.items()):
                lines.append(f"- {key}: {value}")

        points = report.get("points")
        if isinstance(points, list) and points:
            lines.append("")
            lines.append("Calibration points:")
            for point in points:
                lines.append(
                    "- "
                    f"{point.get('direction')} {point.get('target_percent')}% "
                    f"valid={point.get('valid')} "
                    f"failure={point.get('failure_reason')} "
                    f"success={point.get('successful')} "
                    f"selection={point.get('selection_strategy')} "
                    f"selected_attempt={point.get('selected_attempt')} "
                    f"selected_attempts={point.get('selected_attempts')} "
                    f"selected_abs_error={point.get('selected_abs_error_percent')}% "
                    f"selected_window_avg_abs_error={point.get('selected_window_avg_abs_error_percent')}% "
                    f"outliers={point.get('ignored_outlier_attempts')} "
                    f"invalid_attempts={point.get('ignored_invalid_attempts')} "
                    f"successful_attempts={point.get('successful_attempts')} "
                    f"attempts={point.get('attempts_used')} "
                    f"selected_final={point.get('final_percent')}% "
                    f"selected_error={point.get('final_error_percent')}% "
                    f"corrected_stop={point.get('corrected_stop_percent')}%"
                )
                attempts = point.get("attempts")
                if isinstance(attempts, list):
                    selected_attempts = point.get("selected_attempts")
                    if not isinstance(selected_attempts, list):
                        selected_attempts = []
                    ignored_outliers = point.get("ignored_outlier_attempts")
                    if not isinstance(ignored_outliers, list):
                        ignored_outliers = []
                    for attempt in attempts:
                        if not isinstance(attempt, dict):
                            continue
                        markers = []
                        if attempt.get("attempt") in selected_attempts:
                            markers.append("selected")
                        if attempt.get("attempt") in ignored_outliers:
                            markers.append("outlier")
                        if attempt.get("valid", True) is False:
                            markers.append("invalid")
                        lines.append(
                            "  "
                            f"attempt {attempt.get('attempt')}: "
                            f"markers={markers} "
                            f"failure={attempt.get('failure_reason')} "
                            f"requested_stop={attempt.get('requested_stop_percent')}% "
                            f"stop_sent={attempt.get('stop_command_percent')}% "
                            f"final={attempt.get('final_percent')}% "
                            f"error={attempt.get('error_percent')}% "
                            f"latency={attempt.get('stop_command_latency_ms')}ms "
                            f"duration={attempt.get('move_duration_ms')}ms "
                            f"speed_raw_per_second={attempt.get('speed_raw_per_second')}"
                        )

        events = report.get("events")
        if isinstance(events, list) and events:
            lines.append("")
            lines.append("Event log:")
            for event in events:
                if not isinstance(event, dict):
                    continue
                details = event.get("details")
                detail_text = ""
                if isinstance(details, dict) and details:
                    detail_text = f" {json.dumps(details, sort_keys=True)}"
                lines.append(
                    f"[{event.get('index')}] {event.get('timestamp')} "
                    f"{event.get('stage')}: {event.get('message')}{detail_text}"
                )

        return "\n".join(lines)

    async def async_reconnect(self) -> None:
        """Force the current NHK/TLS session to be recreated."""
        await self._async_cancel_position_target()
        await self._async_cancel_post_command_refresh()
        await self._async_cancel_calibration()
        await self._async_cancel_post_command_refresh()
        self.connection_state = CONNECTION_STATE_RECONNECTING
        await self.hass.async_add_executor_job(self.client.close)
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the persistent connection."""
        await self._async_cancel_position_target()
        await self._async_cancel_post_command_refresh()
        await self._async_cancel_calibration()
        await self._async_cancel_post_command_refresh()
        await self.hass.async_add_executor_job(self.client.close)
