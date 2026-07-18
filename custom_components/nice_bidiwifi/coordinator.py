"""Coordinator for Nice."""

from __future__ import annotations

import asyncio  # noqa: F401 - re-exported for compatibility with existing tests.
from dataclasses import fields, replace
from datetime import UTC, datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .calibration import NiceBidiCalibrationMixin
from .calibration_constants import (  # noqa: F401 - re-exported for compatibility.
    CALIBRATION_COMMAND_PAUSE_SECONDS,
    CALIBRATION_ENDPOINT_TOLERANCE,
    CALIBRATION_MAX_ATTEMPTS,
    CALIBRATION_MOVEMENT_TIMEOUT_SECONDS,
    CALIBRATION_OUTLIER_ERROR_PERCENT,
    CALIBRATION_REPORT_LOG_CHUNK_SIZE,
    CALIBRATION_SETTLE_SECONDS,
    CALIBRATION_SETTLE_TIMEOUT_SECONDS,
    CALIBRATION_STABILITY_ATTEMPTS,
    CALIBRATION_STATE_CALIBRATED,
    CALIBRATION_STATE_CANCELLED,
    CALIBRATION_STATE_FAILED,
    CALIBRATION_STATE_NOT_CALIBRATED,
    CALIBRATION_STATE_RUNNING,
    CALIBRATION_STOPPED_ENDPOINT_GRACE_SECONDS,
    CALIBRATION_STOPPED_ENDPOINT_MIN_DURATION_RATIO,
    CALIBRATION_STORAGE_VERSION,
    CALIBRATION_TARGET_TOLERANCE_PERCENT,
    CALIBRATION_TARGETS,
)
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
    device_info_supports_nhk_status,
)
from .connection import (
    CONNECTION_STATE_AUTH_FAILED,
    CONNECTION_STATE_CONNECTED,
    CONNECTION_STATE_FAILED,
    CONNECTION_STATE_RECONNECTING,
    CONNECTION_STATE_UNKNOWN,
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
from .position import (  # noqa: F401 - constants are re-exported for compatibility.
    POST_COMMAND_FAST_POLL_SECONDS,
    POST_COMMAND_REFRESH_DELAY_SECONDS,
    POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR,
    POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND,
    POSITION_SIMULATION_START_GRACE_SECONDS,
    POSITION_SIMULATION_TICK_SECONDS,
    POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
    POSITION_TARGET_POLL_SECONDS,
    POSITION_TARGET_TOLERANCE,
    RECENT_STOP_STATUS_OVERRIDE_SECONDS,
    NiceBidiPositionMixin,
)

_LOGGER = logging.getLogger(__name__)

EXTENDED_STATUS_REFRESH_SECONDS = 300.0

DEP_MOVEMENT_ACTIONS = {
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
}
CORE_STATUS_FIELD_NAMES = frozenset(
    {
        "state",
        "position",
        "current_position",
        "closed_position",
        "open_position",
        "registers",
    }
)
EXTENDED_STATUS_FIELD_NAMES = tuple(
    field.name for field in fields(NiceBidiStatus) if field.name not in CORE_STATUS_FIELD_NAMES
)


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


class NiceBidiDataUpdateCoordinator(
    NiceBidiCalibrationMixin,
    NiceBidiPositionMixin,
    DataUpdateCoordinator[NiceBidiStatus],
):
    """DataUpdateCoordinator for one Nice interface."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = entry
        self.connection_state = CONNECTION_STATE_UNKNOWN
        self.device_info: NiceBidiDeviceInfo | None = None
        self.status_polling_supported = True
        self._use_nhk_status = False
        self.last_command: str | None = None
        self.last_command_latency_ms: int | None = None
        self.last_error: str | None = None
        self.last_successful_update: datetime | None = None
        self._init_position_state()
        self._extended_status_cache: NiceBidiStatus | None = None
        self._extended_status_next_refresh_monotonic = 0.0
        self._init_calibration_state(hass, entry)
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

        status = self._normalize_status_for_display(self._apply_recent_stop_status_hint(status))
        self._store_successful_status(status)
        return status

    def _read_status_and_maybe_info(self) -> NiceBidiStatus:
        """Read dynamic status and cache static device info."""
        if not self.status_polling_supported:
            if self.device_info is None:
                self.device_info = self.client.read_info()
            return _unknown_status()

        if self._use_nhk_status:
            if self.device_info is None:
                self.device_info = self.client.read_info()
            return self.client.read_nhk_status()

        try:
            include_extended = self._should_read_extended_status()
            status = self.client.read_status(include_extended=include_extended)
        except NiceBidiConnectionError as err:
            if nice_bidi_error_code(err) != "14":
                raise
            try:
                self.device_info = self.device_info or self.client.read_info()
            except NiceBidiError:
                raise err from None
            if self._supports_nhk_status():
                status = self.client.read_nhk_status()
                self._use_nhk_status = True
                _LOGGER.info(
                    "Nice DMP status polling is not supported by this device; "
                    "using NHK DoorStatus polling"
                )
                return status
            if not self._supports_high_level_actions():
                raise
            self.status_polling_supported = False
            _LOGGER.info(
                "Nice DMP status polling is not supported by this device; "
                "using command-only mode"
            )
            return _unknown_status()

        if include_extended:
            self._cache_extended_status(status)
        else:
            status = self._merge_cached_extended_status(status)
        if self.device_info is None:
            try:
                self.device_info = self.client.read_info()
            except NiceBidiError as err:
                _LOGGER.debug("Could not read Nice INFO metadata: %s", err)
        return status

    def _should_read_extended_status(self) -> bool:
        """Return true when the slower BusT4 diagnostic scan is due."""
        status = self.data
        if status is not None and status.is_moving:
            return False
        return time.monotonic() >= self._extended_status_next_refresh_monotonic

    def _cache_extended_status(self, status: NiceBidiStatus) -> None:
        """Store the latest broad BusT4 scan."""
        self._extended_status_cache = status
        self._extended_status_next_refresh_monotonic = time.monotonic() + EXTENDED_STATUS_REFRESH_SECONDS

    def _merge_cached_extended_status(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Merge cached diagnostic fields into a fresh core status read."""
        cached = self._extended_status_cache
        if cached is None:
            return status
        registers = dict(cached.registers)
        registers.update(status.registers)
        values = {name: getattr(cached, name) for name in EXTENDED_STATUS_FIELD_NAMES}
        return replace(status, registers=registers, **values)

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

    def _supports_nhk_status(self) -> bool:
        """Return true when INFO advertises readable NHK status properties."""
        if self.device_info is None:
            return False
        return device_info_supports_nhk_status(
            self.device_info,
            self.config_entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
        )

    def _store_successful_status(self, status: NiceBidiStatus) -> None:
        """Store successful status read metadata."""
        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_error = None
        self.last_successful_update = datetime.now(UTC)
        if status.position is not None:
            self._last_known_position = status.position
        self.update_interval = self._update_interval_for_status(status)
        self._sync_position_simulation_from_status(status)

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

    async def async_write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
    ) -> None:
        """Write a BusT4/DMP register and refresh extended status."""
        if self.data is not None and self.data.is_moving:
            raise HomeAssistantError("Nice DMP writes are blocked while the gate is moving")
        await self._async_cancel_position_target()
        await self._async_cancel_calibration()
        await self._async_write_dmp_register(group, parameter, value, size=size)

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
        stop_started_from_motion = action == "stop" and (
            self._position_simulation_action is not None
            or (self.data is not None and self.data.is_moving)
        )
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
        self._extend_post_command_fast_poll_window()
        if action == "stop":
            self._recent_stop_command_monotonic = time.monotonic()
            self._recent_stop_started_from_motion = stop_started_from_motion
        else:
            self._recent_stop_command_monotonic = None
            self._recent_stop_started_from_motion = False
        self.update_interval = MOVING_UPDATE_INTERVAL
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
        self._extend_post_command_fast_poll_window()
        if action in DEP_MOVEMENT_ACTIONS:
            self._recent_stop_command_monotonic = None
            self._recent_stop_started_from_motion = False
        self.update_interval = MOVING_UPDATE_INTERVAL
        self._clear_position_simulation()
        if refresh:
            self._schedule_post_command_refresh()
            await self.async_request_refresh()

    async def _async_write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
        refresh: bool = True,
    ) -> None:
        """Write a BusT4/DMP register without touching target watchers."""
        command_name = f"dmp_{group:02X}_{parameter:02X}_set"
        started = time.monotonic()
        try:
            await self.hass.async_add_executor_job(
                lambda: self.client.write_dmp_register(group, parameter, value, size=size)
            )
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            raise HomeAssistantError(f"Nice authentication failed: {err}") from err
        except (NiceBidiConnectionError, OSError, ValueError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            raise HomeAssistantError(f"Nice DMP write failed: {err}") from err

        self.connection_state = CONNECTION_STATE_CONNECTED
        self.last_command = command_name
        self.last_command_latency_ms = round((time.monotonic() - started) * 1000)
        self.last_error = None
        self._extend_post_command_fast_poll_window()
        self.update_interval = MOVING_UPDATE_INTERVAL
        self._extended_status_next_refresh_monotonic = 0.0
        if refresh:
            await self.async_request_refresh()

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
