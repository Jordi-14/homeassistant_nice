"""Coordinator for Nice BiDi-WiFi."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
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

POSITION_TARGET_POLL_SECONDS = 0.75
POSITION_TARGET_TOLERANCE = 1.0

CALIBRATION_STORAGE_VERSION = 1
CALIBRATION_TARGETS = (20, 40, 60, 80)
CALIBRATION_SETTLE_SECONDS = 2.0
CALIBRATION_SETTLE_TIMEOUT_SECONDS = 8.0
CALIBRATION_MOVEMENT_TIMEOUT_SECONDS = 90.0
CALIBRATION_ENDPOINT_TOLERANCE = 1.0
CALIBRATION_MAX_ATTEMPTS = 5
CALIBRATION_TARGET_TOLERANCE_PERCENT = 1.0

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
        self._calibration_task: asyncio.Task[None] | None = None
        self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
        self.calibration_last_error: str | None = None
        self.calibration_updated_at: datetime | None = None
        self.calibration_profile: dict[str, Any] | None = None
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
        stored_profile = await self._calibration_store.async_load()
        if not isinstance(stored_profile, dict):
            self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
            return

        self.calibration_profile = stored_profile
        self.calibration_state = CALIBRATION_STATE_CALIBRATED
        self.calibration_last_error = None
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
            await self.async_request_refresh()

    async def async_set_position(self, target_position: int) -> None:
        """Move toward a target percentage and stop after the target is reached."""
        await self._async_cancel_calibration()
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
        self.calibration_state = CALIBRATION_STATE_RUNNING
        self.calibration_last_error = None
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
            self.calibration_profile = profile
            self.calibration_state = CALIBRATION_STATE_CALIBRATED
            self.calibration_last_error = None
            updated_at = profile.get("updated_at")
            if isinstance(updated_at, str):
                self.calibration_updated_at = datetime.fromisoformat(updated_at)
            await self._calibration_store.async_save(profile)
        except asyncio.CancelledError:
            self.calibration_state = CALIBRATION_STATE_CANCELLED
            self.calibration_last_error = "cancelled"
            raise
        except (NiceBidiAuthError, NiceBidiConnectionError, OSError, HomeAssistantError) as err:
            self.calibration_state = CALIBRATION_STATE_FAILED
            self.calibration_last_error = str(err)
            _LOGGER.warning("Nice BiDi-WiFi position calibration failed: %s", err)
        finally:
            if self._calibration_task is task:
                self._calibration_task = None
            self.async_update_listeners()

    async def _async_build_position_calibration(self) -> dict[str, Any]:
        """Build a direction-specific calibration profile."""
        started_at = datetime.now(UTC)
        start_status = await self._async_read_motion_status()
        self._validate_position_bounds(start_status)

        opening_samples = []
        for target in CALIBRATION_TARGETS:
            opening_samples.append(await self._async_calibrate_target_from_endpoint(target, "open", "close"))

        open_status = await self._async_move_to_end("open")
        closing_samples = []
        for target in reversed(CALIBRATION_TARGETS):
            closing_samples.append(await self._async_calibrate_target_from_endpoint(target, "close", "open"))

        final_status = await self._async_move_to_end("close")
        updated_at = datetime.now(UTC)

        return {
            "version": 2,
            "created_at": started_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "poll_seconds": POSITION_TARGET_POLL_SECONDS,
            "settle_seconds": CALIBRATION_SETTLE_SECONDS,
            "max_attempts": CALIBRATION_MAX_ATTEMPTS,
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
        successful = False

        for attempt in range(1, CALIBRATION_MAX_ATTEMPTS + 1):
            endpoint_status = await self._async_move_to_end(endpoint_action)
            if stop_raw is None:
                stop_raw = self._raw_for_percent(endpoint_status, target)

            sample = await self._async_calibrate_target(target, action, endpoint_action, attempt, stop_raw)
            attempts.append(sample)
            stop_raw = sample["corrected_stop_raw"]
            successful = abs(sample["error_percent"]) <= CALIBRATION_TARGET_TOLERANCE_PERCENT

            await self._async_move_to_end(endpoint_action)
            if successful:
                break

        final_sample = attempts[-1]
        return {
            **final_sample,
            "successful": successful,
            "attempts_used": len(attempts),
            "attempts": attempts,
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
            if current_raw is None:
                continue
            if self._raw_reached(action, status, current_raw, requested_stop_raw):
                stop_command_raw = current_raw
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

        settled = await self._async_wait_for_settle()
        if settled.current_position is None:
            raise HomeAssistantError("Nice BiDi-WiFi final encoder position is not available")

        final_raw = settled.current_position
        error_raw = final_raw - target_raw
        corrected_stop_raw = self._clamp_raw(status, stop_command_raw - error_raw)
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        travel_raw = stop_command_raw - start_raw
        speed_raw_per_second = (travel_raw / move_duration_ms) * 1000 if move_duration_ms > 0 else None

        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(self._percent_for_raw(status, start_raw), 2),
            "target_raw": target_raw,
            "requested_stop_raw": requested_stop_raw,
            "requested_stop_percent": round(self._percent_for_raw(status, requested_stop_raw), 2),
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": round(self._percent_for_raw(status, stop_command_raw), 2),
            "corrected_stop_raw": corrected_stop_raw,
            "corrected_stop_percent": round(self._percent_for_raw(status, corrected_stop_raw), 2),
            "final_raw": final_raw,
            "final_percent": round(self._percent_for_raw(settled, final_raw), 2),
            "error_raw": error_raw,
            "error_percent": round(self._percent_for_raw(settled, final_raw) - target, 2),
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    async def _async_move_to_end(self, action: str) -> NiceBidiStatus:
        """Move fully open or closed for a known calibration starting point."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if self._is_at_endpoint(status, action):
            return status

        await self._async_send_action(action, refresh=False)
        started = time.monotonic()
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + 8.0
        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                return status
            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (started_moving or time.monotonic() > movement_start_deadline):
                raise HomeAssistantError(f"Position calibration stopped before reaching {action} endpoint")

        raise HomeAssistantError(f"Timed out moving to {action} endpoint during position calibration")

    async def _async_wait_for_settle(self) -> NiceBidiStatus:
        """Wait after a stop command, then return the settled status."""
        await asyncio.sleep(CALIBRATION_SETTLE_SECONDS)
        status = await self._async_read_motion_status()
        started = time.monotonic()
        while status.is_moving and time.monotonic() - started < CALIBRATION_SETTLE_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
        return status

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
            target_percent = sample.get("target_percent")
            corrected_stop_percent = sample.get("corrected_stop_percent")
            if isinstance(target_percent, (int, float)) and isinstance(corrected_stop_percent, (int, float)):
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
            return status.state == STATE_OPEN or (
                status.position is not None and status.position >= 100.0 - CALIBRATION_ENDPOINT_TOLERANCE
            )
        return status.state == STATE_CLOSED or (
            status.position is not None and status.position <= CALIBRATION_ENDPOINT_TOLERANCE
        )

    async def async_reconnect(self) -> None:
        """Force the current NHK/TLS session to be recreated."""
        await self._async_cancel_position_target()
        await self._async_cancel_calibration()
        self.connection_state = CONNECTION_STATE_RECONNECTING
        await self.hass.async_add_executor_job(self.client.close)
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the persistent connection."""
        await self._async_cancel_position_target()
        await self._async_cancel_calibration()
        await self.hass.async_add_executor_job(self.client.close)
