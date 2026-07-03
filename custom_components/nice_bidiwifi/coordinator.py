"""Coordinator for Nice."""

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

from .calibration_report import (
    build_calibration_report,
    build_live_calibration_report,
    calibration_quality as report_calibration_quality,
    calibration_report_attributes as report_calibration_report_attributes,
    calibration_report_summary as report_calibration_report_summary,
    format_calibration_report,
)
from .calibration_types import CalibrationEvent, CalibrationProfile, CalibrationReport
from .client import (
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
    NiceBidiAuthError,
    NiceBidiClient,
    NiceBidiConnectionError,
    NiceBidiCredentials,
    NiceBidiDeviceInfo,
    NiceBidiError,
    nice_bidi_error_code,
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
POSITION_SIMULATION_TICK_SECONDS = 1.0
POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND = 1.0
POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR = 0.8
POSITION_SIMULATION_START_GRACE_SECONDS = 8.0
POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS = 30.0

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
CALIBRATION_REPORT_LOG_CHUNK_SIZE = 6000

CALIBRATION_STATE_CALIBRATED = "calibrated"
CALIBRATION_STATE_CANCELLED = "cancelled"
CALIBRATION_STATE_FAILED = "failed"
CALIBRATION_STATE_NOT_CALIBRATED = "not_calibrated"
CALIBRATION_STATE_RUNNING = "running"

DEP_MOVEMENT_ACTIONS = {
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
}


def _unknown_status() -> NiceBidiStatus:
    """Return an empty status for devices that support commands but not DMP status."""
    return NiceBidiStatus(
        state=None,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
        registers={},
    )


class NiceBidiDataUpdateCoordinator(DataUpdateCoordinator[NiceBidiStatus]):
    """DataUpdateCoordinator for one Nice interface."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = entry
        self.connection_state = CONNECTION_STATE_UNKNOWN
        self.device_info: NiceBidiDeviceInfo | None = None
        self.status_polling_supported = True
        self.last_command: str | None = None
        self.last_command_latency_ms: int | None = None
        self.last_error: str | None = None
        self.last_successful_update: datetime | None = None
        self._position_target_task: asyncio.Task[None] | None = None
        self._post_command_refresh_task: asyncio.Task[None] | None = None
        self._position_simulation_task: asyncio.Task[None] | None = None
        self._position_simulation_action: str | None = None
        self._position_simulation_anchor_position: float | None = None
        self._position_simulation_anchor_monotonic: float | None = None
        self._position_simulation_started_monotonic: float | None = None
        self._position_simulation_deadline_monotonic: float | None = None
        self._position_simulation_confirmed_moving = False
        self._position_simulation_target_position: float | None = None
        self._position_simulation_speed_percent_per_second: float | None = None
        self._calibration_task: asyncio.Task[None] | None = None
        self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
        self.calibration_last_error: str | None = None
        self.calibration_updated_at: datetime | None = None
        self.calibration_profile: CalibrationProfile | None = None
        self.calibration_report: CalibrationReport | None = None
        self._calibration_events: list[CalibrationEvent] = []
        self._calibration_store = Store[CalibrationProfile](
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
            _LOGGER.warning("Nice stored calibration could not be loaded: %s", err)
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
            self._clear_position_simulation()
            raise ConfigEntryAuthFailed(str(err)) from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            self._clear_position_simulation()
            raise UpdateFailed(str(err)) from err

        self._store_successful_status(status)
        return status

    def _read_status_and_maybe_info(self) -> NiceBidiStatus:
        """Read dynamic status and cache static device info."""
        if not self.status_polling_supported:
            if self.device_info is None:
                self.device_info = self.client.read_info()
            return _unknown_status()

        try:
            status = self.client.read_status()
        except NiceBidiConnectionError as err:
            if nice_bidi_error_code(err) != "14":
                raise
            try:
                self.device_info = self.device_info or self.client.read_info()
            except NiceBidiError:
                raise err from None
            if not self._supports_high_level_actions():
                raise
            self.status_polling_supported = False
            _LOGGER.info(
                "Nice DMP status polling is not supported by this device; "
                "using command-only mode"
            )
            return _unknown_status()

        if self.device_info is None:
            try:
                self.device_info = self.client.read_info()
            except NiceBidiError as err:
                _LOGGER.debug("Could not read Nice INFO metadata: %s", err)
        return status

    def _supports_high_level_actions(self) -> bool:
        """Return true when INFO advertises writable DoorAction support."""
        if self.device_info is None:
            return False
        device_id = str(self.config_entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID))
        for service in self.device_info.services:
            if service.name != "DoorAction":
                continue
            if service.owner != "Device" or service.owner_id not in {None, device_id}:
                continue
            if "w" in (service.permission or ""):
                return True
        return False

    def _store_successful_status(self, status: NiceBidiStatus) -> None:
        """Store successful status read metadata."""
        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_error = None
        self.last_successful_update = datetime.now(UTC)
        self.update_interval = MOVING_UPDATE_INTERVAL if status.is_moving else IDLE_UPDATE_INTERVAL
        self._sync_position_simulation_from_status(status)

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

    async def _async_cancel_position_simulation(self) -> None:
        """Cancel the optimistic position animation."""
        task = self._position_simulation_task
        self._clear_position_simulation(notify=False)
        if task is None or task.done() or task is asyncio.current_task():
            return
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

    async def _async_cancel_background_tasks(self, *, stop_calibration: bool = True) -> None:
        """Cancel background tasks owned by this coordinator."""
        await self._async_cancel_position_target()
        await self._async_cancel_post_command_refresh()
        await self._async_cancel_position_simulation()
        await self._async_cancel_calibration(stop=stop_calibration)

    async def async_send_action(self, action: str) -> None:
        """Send an open, close, or stop command."""
        await self._async_cancel_position_target()
        await self._async_cancel_calibration(stop=action != "stop")
        await self._async_send_action(action)

    async def async_send_dep_action(self, action: str) -> None:
        """Send a low-level DEP action command."""
        await self._async_cancel_position_target()
        await self._async_cancel_calibration()
        await self._async_send_dep_action(action)

    async def _async_send_action(
        self,
        action: str,
        *,
        refresh: bool = True,
        simulate: bool = True,
        simulation_target_position: float | None = None,
    ) -> None:
        """Send an open, close, or stop command without touching target watchers."""
        started = time.monotonic()
        try:
            await self.hass.async_add_executor_job(self.client.send_action, action)
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            self._clear_position_simulation()
            raise HomeAssistantError(f"Nice authentication failed: {err}") from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self._clear_position_simulation()
            raise HomeAssistantError(f"Nice command failed: {err}") from err

        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_command = action
        self.last_command_latency_ms = round((time.monotonic() - started) * 1000)
        self.last_error = None
        self.update_interval = MOVING_UPDATE_INTERVAL if action in {"open", "close"} else IDLE_UPDATE_INTERVAL
        if action in {"open", "close"} and simulate:
            self._start_position_simulation(action, target_position=simulation_target_position)
        elif action == "stop":
            self._clear_position_simulation()
        if refresh:
            self._schedule_post_command_refresh()
            await self.async_request_refresh()

    async def _async_send_dep_action(self, action: str, *, refresh: bool = True) -> None:
        """Send a low-level DEP action command."""
        started = time.monotonic()
        try:
            await self.hass.async_add_executor_job(self.client.send_dep_action, action)
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            self._clear_position_simulation()
            raise HomeAssistantError(f"Nice authentication failed: {err}") from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self._clear_position_simulation()
            raise HomeAssistantError(f"Nice command failed: {err}") from err

        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_command = action
        self.last_command_latency_ms = round((time.monotonic() - started) * 1000)
        self.last_error = None
        self.update_interval = MOVING_UPDATE_INTERVAL if action in DEP_MOVEMENT_ACTIONS else IDLE_UPDATE_INTERVAL
        self._clear_position_simulation()
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

    @property
    def display_position(self) -> float | None:
        """Return the position HA should display, using simulation while active."""
        simulated = self._current_simulated_position()
        if simulated is not None:
            return round(simulated, 1)
        status = self.data
        return status.position if status else None

    @property
    def display_position_estimated(self) -> bool:
        """Return true when the displayed position is currently estimated."""
        return self._current_simulated_position() is not None

    @property
    def position_simulation_action(self) -> str | None:
        """Return the active simulated direction."""
        return self._position_simulation_action if self.display_position_estimated else None

    @property
    def position_simulation_speed_percent_per_second(self) -> float | None:
        """Return the active simulated display speed."""
        if not self.display_position_estimated:
            return None
        speed = self._position_simulation_speed_percent_per_second
        return round(speed, 2) if speed is not None else None

    def _start_position_simulation(self, action: str, *, target_position: float | None = None) -> None:
        """Start or restart optimistic position animation after a movement command."""
        anchor = self._current_simulated_position()
        if anchor is None:
            status = self.data
            anchor = status.position if status and status.position is not None else None
        if anchor is None:
            return

        now = time.monotonic()
        speed = self._position_simulation_speed(action)
        target = None if target_position is None else max(0.0, min(100.0, float(target_position)))
        limit = 100.0 if action == "open" and target is None else 0.0 if target is None else target
        distance = abs(limit - anchor)
        expected_seconds = distance / speed if speed > 0 else 0
        deadline = now + max(
            POSITION_SIMULATION_START_GRACE_SECONDS,
            expected_seconds + POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
        )

        self._position_simulation_action = action
        self._position_simulation_anchor_position = anchor
        self._position_simulation_anchor_monotonic = now
        self._position_simulation_started_monotonic = now
        self._position_simulation_deadline_monotonic = deadline
        self._position_simulation_confirmed_moving = False
        self._position_simulation_target_position = target
        self._position_simulation_speed_percent_per_second = speed
        self._ensure_position_simulation_task()
        self.async_update_listeners()

    def _ensure_position_simulation_task(self) -> None:
        """Ensure the simulation tick task is running."""
        task = self._position_simulation_task
        if task is not None and not task.done():
            return
        self._position_simulation_task = self.hass.async_create_task(
            self._async_run_position_simulation(),
            name=f"{DOMAIN} position simulation",
        )

    async def _async_run_position_simulation(self) -> None:
        """Tick the optimistic display position until real status stops it."""
        task = asyncio.current_task()
        try:
            while self._position_simulation_action is not None:
                await asyncio.sleep(POSITION_SIMULATION_TICK_SECONDS)
                deadline = self._position_simulation_deadline_monotonic
                if deadline is not None and time.monotonic() > deadline:
                    self._clear_position_simulation()
                    return
                self.async_update_listeners()
        except asyncio.CancelledError:
            raise
        finally:
            if self._position_simulation_task is task:
                self._position_simulation_task = None

    def _current_simulated_position(self) -> float | None:
        """Return the current synthetic display position."""
        action = self._position_simulation_action
        anchor = self._position_simulation_anchor_position
        anchor_time = self._position_simulation_anchor_monotonic
        speed = self._position_simulation_speed_percent_per_second
        if action is None or anchor is None or anchor_time is None or speed is None:
            return None

        elapsed = max(0.0, time.monotonic() - anchor_time)
        if action == "open":
            calculated = min(100.0, anchor + (speed * elapsed))
            target = self._position_simulation_target_position
            if target is None:
                cap = 100.0 if anchor >= 100.0 else max(99.0, anchor)
            else:
                cap = target if anchor < target else anchor
            return max(0.0, min(calculated, cap))

        calculated = max(0.0, anchor - (speed * elapsed))
        target = self._position_simulation_target_position
        if target is None:
            floor = 0.0 if anchor <= 0.0 else min(1.0, anchor)
        else:
            floor = target if anchor > target else anchor
        return min(100.0, max(calculated, floor))

    def _sync_position_simulation_from_status(self, status: NiceBidiStatus) -> None:
        """Rebase or stop simulated display movement from a real status update."""
        if self.calibration_state == CALIBRATION_STATE_RUNNING:
            self._clear_position_simulation(notify=False)
            return
        if status.position is None:
            return

        real_action = self._motion_action_from_state(status.state)
        if real_action is not None:
            self._rebase_position_simulation(
                real_action,
                status.position,
                confirmed_moving=True,
                keep_target=real_action == self._position_simulation_action,
            )
            return

        if self._position_simulation_action is None:
            return
        if not self._position_simulation_confirmed_moving:
            started = self._position_simulation_started_monotonic
            if (
                started is not None
                and time.monotonic() - started < POSITION_SIMULATION_START_GRACE_SECONDS
            ):
                return
        self._clear_position_simulation(notify=False)

    def _rebase_position_simulation(
        self,
        action: str,
        position: float,
        *,
        confirmed_moving: bool,
        keep_target: bool,
    ) -> None:
        """Anchor simulation to a fresh real position update."""
        now = time.monotonic()
        speed = self._position_simulation_speed(action)
        target = self._position_simulation_target_position if keep_target else None
        limit = 100.0 if action == "open" and target is None else 0.0 if target is None else target
        distance = abs(limit - position)
        expected_seconds = distance / speed if speed > 0 else 0
        self._position_simulation_action = action
        self._position_simulation_anchor_position = max(0.0, min(100.0, position))
        self._position_simulation_anchor_monotonic = now
        if self._position_simulation_started_monotonic is None:
            self._position_simulation_started_monotonic = now
        self._position_simulation_deadline_monotonic = now + max(
            POSITION_SIMULATION_START_GRACE_SECONDS,
            expected_seconds + POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
        )
        self._position_simulation_confirmed_moving = confirmed_moving
        self._position_simulation_target_position = target
        self._position_simulation_speed_percent_per_second = speed
        self._ensure_position_simulation_task()

    def _clear_position_simulation(self, *, notify: bool = True) -> None:
        """Clear optimistic position animation state."""
        task = self._position_simulation_task
        self._position_simulation_task = None
        self._position_simulation_action = None
        self._position_simulation_anchor_position = None
        self._position_simulation_anchor_monotonic = None
        self._position_simulation_started_monotonic = None
        self._position_simulation_deadline_monotonic = None
        self._position_simulation_confirmed_moving = False
        self._position_simulation_target_position = None
        self._position_simulation_speed_percent_per_second = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        if notify:
            self.async_update_listeners()

    @staticmethod
    def _motion_action_from_state(state: str) -> str | None:
        """Return the movement action represented by a BiDi state."""
        if state == STATE_OPENING:
            return "open"
        if state == STATE_CLOSING:
            return "close"
        return None

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
            raise HomeAssistantError("Nice position is not available")

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
        await self._async_send_action(action, refresh=False, simulation_target_position=target)
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
            _LOGGER.warning("Nice target-position authentication failed: %s", err)
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            _LOGGER.warning("Nice target-position tracking failed: %s", err)
        finally:
            if self._position_target_task is task:
                self._position_target_task = None

    async def async_start_position_calibration(self) -> None:
        """Start a background position calibration run."""
        task = self._calibration_task
        if task is not None and not task.done():
            raise HomeAssistantError("Nice position calibration is already running")

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
            _LOGGER.warning("Nice position calibration failed: %s", err)
        finally:
            if self._calibration_task is task:
                self._calibration_task = None
            self.async_update_listeners()

    async def _async_build_position_calibration(self) -> CalibrationProfile:
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

        self._add_calibration_event("speed", "Moving fully closed before speed calibration")
        speed_start_status = await self._async_move_to_end("close")
        self._add_calibration_event("speed", "Measuring full opening speed")
        opening_travel = await self._async_measure_full_travel("open")
        self._add_calibration_event("speed", "Measuring full closing speed")
        closing_travel = await self._async_measure_full_travel("close")

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
            "version": 5,
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
                "speed_start_closed_raw": speed_start_status.closed_position,
                "speed_start_open_raw": speed_start_status.open_position,
                "open_run_closed_raw": open_status.closed_position,
                "open_run_open_raw": open_status.open_position,
                "final_closed_raw": final_status.closed_position,
                "final_open_raw": final_status.open_position,
                "final_state": final_status.state,
            },
            "travel_speed": {
                "open": opening_travel,
                "close": closing_travel,
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
            raise HomeAssistantError("Nice current encoder position is not available")

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
        await self._async_send_action(action, refresh=False, simulate=False)
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
                await self._async_send_action("stop", refresh=False, simulate=False)
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
            raise HomeAssistantError("Nice final encoder position is not available")

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

    async def _async_measure_full_travel(self, action: str) -> dict[str, Any]:
        """Measure one full endpoint-to-endpoint movement for display speed."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if status.current_position is None or status.position is None:
            raise HomeAssistantError("Nice current position is not available")

        start_raw = status.current_position
        start_percent = status.position
        started = time.monotonic()
        self._add_calibration_event(
            "speed",
            f"Full {action} speed measurement started",
            action=action,
            start_raw=start_raw,
            start_percent=start_percent,
            state=status.state,
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + POSITION_SIMULATION_START_GRACE_SECONDS

        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                if status.current_position is None or status.position is None:
                    raise HomeAssistantError("Nice endpoint position is not available")
                duration_ms = round((time.monotonic() - started) * 1000)
                distance_raw = status.current_position - start_raw
                distance_percent = status.position - start_percent
                duration_seconds = duration_ms / 1000
                speed_raw_per_second = (
                    distance_raw / duration_seconds if duration_seconds > 0 else None
                )
                speed_percent_per_second = (
                    abs(distance_percent) / duration_seconds if duration_seconds > 0 else None
                )
                result = {
                    "action": action,
                    "start_raw": start_raw,
                    "start_percent": round(start_percent, 2),
                    "end_raw": status.current_position,
                    "end_percent": round(status.position, 2),
                    "end_state": status.state,
                    "end_closed_raw": status.closed_position,
                    "end_open_raw": status.open_position,
                    "distance_raw": distance_raw,
                    "distance_percent": round(distance_percent, 2),
                    "duration_ms": duration_ms,
                    "speed_raw_per_second": (
                        round(speed_raw_per_second, 2)
                        if speed_raw_per_second is not None
                        else None
                    ),
                    "speed_percent_per_second": (
                        round(speed_percent_per_second, 2)
                        if speed_percent_per_second is not None
                        else None
                    ),
                }
                self._add_calibration_event(
                    "speed",
                    f"Full {action} speed measurement completed",
                    **result,
                )
                await self._async_pause_before_next_calibration_command()
                return result

            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                raise HomeAssistantError(f"Position calibration stopped during full {action}")

        raise HomeAssistantError(f"Timed out measuring full {action} speed")

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
        await self._async_send_action(action, refresh=False, simulate=False)
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
            await self._async_send_action("stop", refresh=False, simulate=False)
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

    def _position_simulation_speed(self, action: str) -> float:
        """Return display animation speed in percent per second."""
        calibrated_speed = self._calibrated_travel_speed_percent_per_second(action)
        if calibrated_speed is None:
            return POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND
        return calibrated_speed * POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR

    def _calibrated_travel_speed_percent_per_second(self, action: str) -> float | None:
        """Return measured full-travel speed from calibration, with legacy fallback."""
        profile = self.calibration_profile
        if profile is None:
            return None

        travel_speed = profile.get("travel_speed")
        if isinstance(travel_speed, dict):
            action_speed = travel_speed.get(action)
            if isinstance(action_speed, dict):
                speed = action_speed.get("speed_percent_per_second")
                if isinstance(speed, (int, float)) and speed > 0:
                    return float(speed)

        return self._sampled_calibration_speed_percent_per_second(action)

    def _sampled_calibration_speed_percent_per_second(self, action: str) -> float | None:
        """Estimate direction speed from older calibration samples."""
        profile = self.calibration_profile
        if profile is None:
            return None
        profile_samples = profile.get("samples")
        if not isinstance(profile_samples, dict):
            return None
        samples = profile_samples.get(action)
        if not isinstance(samples, list):
            return None

        speeds: list[float] = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            if sample.get("valid", True) is False or sample.get("selection_strategy") == "no_valid_attempt":
                continue
            start_percent = sample.get("start_percent")
            final_percent = sample.get("final_percent")
            duration_ms = sample.get("move_duration_ms")
            if not isinstance(start_percent, (int, float)):
                continue
            if not isinstance(final_percent, (int, float)):
                continue
            if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
                continue
            speed = abs(float(final_percent) - float(start_percent)) / (float(duration_ms) / 1000)
            if speed > 0:
                speeds.append(speed)

        if not speeds:
            return None
        return sum(speeds) / len(speeds)

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
            raise HomeAssistantError("Nice encoder endpoints are not available")
        if status.closed_position == status.open_position:
            raise HomeAssistantError("Nice encoder endpoints are identical")

    @staticmethod
    def _raw_for_percent(status: NiceBidiStatus, percent: float) -> int:
        """Convert percentage to raw encoder position."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        span = status.open_position - status.closed_position
        raw = status.closed_position + (span * (percent / 100.0))
        return round(raw)

    @staticmethod
    def _percent_for_raw(status: NiceBidiStatus, raw: int) -> float:
        """Convert raw encoder position to percentage."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        span = status.open_position - status.closed_position
        if span == 0:
            raise HomeAssistantError("Nice encoder endpoints are identical")
        return max(0.0, min(100.0, ((raw - status.closed_position) / span) * 100.0))

    @staticmethod
    def _raw_reached(action: str, status: NiceBidiStatus, current_raw: int, target_raw: int) -> bool:
        """Return true if raw position crossed target in the requested direction."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        increasing = status.open_position > status.closed_position
        if action == "open":
            return current_raw >= target_raw if increasing else current_raw <= target_raw
        return current_raw <= target_raw if increasing else current_raw >= target_raw

    @staticmethod
    def _clamp_raw(status: NiceBidiStatus, raw: int) -> int:
        """Clamp raw position to known encoder bounds."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
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
        return report_calibration_quality(self.calibration_report)

    @property
    def calibration_report_summary(self) -> str | None:
        """Return a compact calibration report summary."""
        return report_calibration_report_summary(self.calibration_report)

    @property
    def calibration_report_attributes(self) -> dict[str, Any]:
        """Return recorder-safe calibration report attributes."""
        return report_calibration_report_attributes(self.calibration_report)

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
            _LOGGER.info("Nice calibration: %s | %s", message, json.dumps(details, sort_keys=True))
        else:
            _LOGGER.info("Nice calibration: %s", message)

        if self.calibration_state == CALIBRATION_STATE_RUNNING:
            self.calibration_report = self._build_live_calibration_report(message)
            self.async_update_listeners()

    def _log_calibration_report(self, report: CalibrationReport, reason: str) -> None:
        """Write the full calibration report to HA logs in bounded chunks."""
        report_text = self._format_calibration_report(report)
        total_chunks = max(
            1,
            (len(report_text) + CALIBRATION_REPORT_LOG_CHUNK_SIZE - 1) // CALIBRATION_REPORT_LOG_CHUNK_SIZE,
        )
        _LOGGER.warning(
            "Nice calibration report %s: writing %s bytes in %s chunks",
            reason,
            len(report_text),
            total_chunks,
        )
        for chunk_index in range(total_chunks):
            start = chunk_index * CALIBRATION_REPORT_LOG_CHUNK_SIZE
            chunk = report_text[start : start + CALIBRATION_REPORT_LOG_CHUNK_SIZE]
            _LOGGER.warning(
                "Nice calibration report %s chunk %s/%s:\n%s",
                reason,
                chunk_index + 1,
                total_chunks,
                chunk,
            )

    def _build_live_calibration_report(self, summary: str) -> CalibrationReport:
        """Build a report while calibration is still running or failed."""
        return build_live_calibration_report(
            state=self.calibration_state,
            summary=summary,
            events=self._calibration_events,
            tolerance_percent=CALIBRATION_TARGET_TOLERANCE_PERCENT,
            poll_seconds=POSITION_TARGET_POLL_SECONDS,
            settle_seconds=CALIBRATION_SETTLE_SECONDS,
            command_pause_seconds=CALIBRATION_COMMAND_PAUSE_SECONDS,
            max_attempts=CALIBRATION_MAX_ATTEMPTS,
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
        )

    def _build_calibration_report(self, profile: CalibrationProfile) -> CalibrationReport:
        """Build quality metrics and a copyable report from a calibration profile."""
        return build_calibration_report(profile, self.calibration_state)

    @staticmethod
    def _format_calibration_report(report: CalibrationReport) -> str:
        """Format a calibration report as copyable plain text."""
        return format_calibration_report(report)

    async def async_reconnect(self) -> None:
        """Force the current NHK/TLS session to be recreated."""
        await self._async_cancel_background_tasks()
        self.connection_state = CONNECTION_STATE_RECONNECTING
        await self.hass.async_add_executor_job(self.client.close)
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the persistent connection."""
        await self._async_cancel_background_tasks()
        await self.hass.async_add_executor_job(self.client.close)
