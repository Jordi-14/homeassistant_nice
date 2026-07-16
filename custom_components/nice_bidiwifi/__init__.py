"""Nice integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .cloud import async_setup_cloud_entry, async_unload_cloud_entry
from .coordinator import NiceBidiDataUpdateCoordinator
from .const import (
    CONF_CONNECTION_METHOD,
    CONNECTION_METHOD_CLOUD,
    CONNECTION_METHOD_LOCAL,
    LOCAL_PLATFORMS,
)
from .runtime import get_coordinator

PLATFORMS = LOCAL_PLATFORMS


def entry_connection_method(entry: ConfigEntry) -> str:
    """Return the selected connection method for a config entry."""
    return str(entry.data.get(CONF_CONNECTION_METHOD, CONNECTION_METHOD_LOCAL))


def entry_uses_cloud(entry: ConfigEntry) -> bool:
    """Return true when a config entry is backed by MyNice cloud setup."""
    return entry_connection_method(entry) == CONNECTION_METHOD_CLOUD


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Nice from a config entry."""
    if entry_uses_cloud(entry):
        return await async_setup_cloud_entry(hass, entry)

    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    await coordinator.async_load_calibration()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if entry_uses_cloud(entry):
        return await async_unload_cloud_entry(hass, entry)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    coordinator = get_coordinator(entry)
    await coordinator.async_shutdown()
    entry.runtime_data = None
    return unload_ok
