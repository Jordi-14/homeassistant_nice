"""Setup helpers for MyNice cloud config entries."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud_api import NiceApiError, NiceAuthError, NiceCloud
from .cloud_coordinator import NiceHub
from .const import CLOUD_PLATFORMS, CONF_CLOUD_TOKEN


async def async_setup_cloud_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Nice from a MyNice cloud config entry."""
    session = async_get_clientsession(hass)

    def _save_token(token: dict) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_CLOUD_TOKEN: token},
        )

    cloud = NiceCloud(
        session,
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        token=entry.data.get(CONF_CLOUD_TOKEN),
        on_token=_save_token,
    )

    try:
        doors = await cloud.async_discover()
    except NiceAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NiceApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hub = NiceHub(hass, entry, cloud, doors)
    await hub.async_start()

    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, CLOUD_PLATFORMS)
    return True


async def async_unload_cloud_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a MyNice cloud config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, CLOUD_PLATFORMS)
    hub: NiceHub | None = entry.runtime_data
    if hub is not None:
        await hub.async_stop()
    if unload_ok:
        entry.runtime_data = None
    return unload_ok
