"""Nice integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .coordinator import NiceBidiDataUpdateCoordinator
from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_CONNECTION_MODE,
    CONF_TARGET_MAC,
    DEFAULT_NAME,
    DOMAIN,
)
from .models.config import ConnectionMode
from .models.discovery import normalize_device_id
from .runtime import NiceRuntimeData, get_coordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.COVER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.EVENT,
]


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Migrate legacy local entries without changing entity identity."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    if entry.version == CONFIG_ENTRY_VERSION:
        return True

    data = dict(entry.data)
    data.setdefault(
        CONF_CONNECTION_MODE,
        ConnectionMode.LOCAL_ONLY.value,
    )
    unique_id = entry.unique_id
    if normalized := normalize_device_id(
        unique_id or str(data.get(CONF_TARGET_MAC) or "")
    ):
        unique_id = normalized
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        unique_id=unique_id,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


def _async_migrate_default_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename registry IDs that were created from the old default gate name."""
    default_slug = slugify(DEFAULT_NAME)
    configured_name = str(entry.data.get(CONF_NAME) or entry.title or DOMAIN)
    configured_slug = slugify(configured_name)
    if not configured_slug or configured_slug == default_slug:
        return

    entity_registry = er.async_get(hass)
    renamed = 0
    for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if registry_entry.platform != DOMAIN:
            continue

        entity_domain, object_id = registry_entry.entity_id.split(".", 1)
        if object_id != default_slug and not object_id.startswith(f"{default_slug}_"):
            continue

        new_entity_id = f"{entity_domain}.{configured_slug}{object_id.removeprefix(default_slug)}"
        if new_entity_id == registry_entry.entity_id:
            continue

        try:
            entity_registry.async_update_entity(
                registry_entry.entity_id,
                new_entity_id=new_entity_id,
            )
        except ValueError as err:
            _LOGGER.debug(
                "Skipping Nice entity ID migration from %s to %s: %s",
                registry_entry.entity_id,
                new_entity_id,
                err,
            )
        else:
            renamed += 1

    if renamed:
        _LOGGER.info(
            "Renamed %s Nice entity registry IDs from %s to %s",
            renamed,
            default_slug,
            configured_slug,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Nice from a config entry."""
    _async_migrate_default_entity_ids(hass, entry)

    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    await coordinator.async_load_calibration()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = NiceRuntimeData(
        coordinator=coordinator,
        config=coordinator.entry_config,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    coordinator = get_coordinator(entry)
    await coordinator.async_shutdown()
    entry.runtime_data = None
    return unload_ok
