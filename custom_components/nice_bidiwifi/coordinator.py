"""Coordinator for Nice BiDi-WiFi."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
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

    async def async_send_action(self, action: str) -> None:
        """Send an open, close, or stop command."""
        await self._async_cancel_position_target()
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
        await self._async_cancel_position_target()
        await self._async_send_action(action, refresh=False)
        self._position_target_task = self.hass.async_create_task(
            self._async_stop_at_position(target, action),
            name=f"{DOMAIN} stop at {target}%",
        )

    async def _async_stop_at_position(self, target: int, action: str) -> None:
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

    async def async_reconnect(self) -> None:
        """Force the current NHK/TLS session to be recreated."""
        await self._async_cancel_position_target()
        self.connection_state = CONNECTION_STATE_RECONNECTING
        await self.hass.async_add_executor_job(self.client.close)
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the persistent connection."""
        await self._async_cancel_position_target()
        await self.hass.async_add_executor_job(self.client.close)
