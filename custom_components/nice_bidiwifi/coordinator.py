"""Coordinator for Nice BiDi-WiFi."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError, NiceBidiCredentials, NiceBidiStatus
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


class NiceBidiDataUpdateCoordinator(DataUpdateCoordinator[NiceBidiStatus]):
    """DataUpdateCoordinator for one Nice BiDi-WiFi interface."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = entry
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
            status = await self.hass.async_add_executor_job(self.client.read_status)
        except NiceBidiAuthError as err:
            self.client.close()
            raise ConfigEntryAuthFailed(str(err)) from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.update_interval = ERROR_UPDATE_INTERVAL
            raise UpdateFailed(str(err)) from err

        self.update_interval = MOVING_UPDATE_INTERVAL if status.is_moving else IDLE_UPDATE_INTERVAL
        return status

    async def async_send_action(self, action: str) -> None:
        """Send an open, close, or stop command."""
        try:
            await self.hass.async_add_executor_job(self.client.send_action, action)
        except NiceBidiAuthError as err:
            self.client.close()
            raise HomeAssistantError(f"Nice BiDi-WiFi authentication failed: {err}") from err
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            raise HomeAssistantError(f"Nice BiDi-WiFi command failed: {err}") from err

        self.update_interval = MOVING_UPDATE_INTERVAL if action in {"open", "close"} else IDLE_UPDATE_INTERVAL
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the persistent connection."""
        await self.hass.async_add_executor_job(self.client.close)
