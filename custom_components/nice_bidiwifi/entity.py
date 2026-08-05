"""Shared entity helpers for Nice."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .client import NiceBidiDeviceInfo
from .const import CONF_DEVICE_ID, CONF_TARGET_MAC, DEFAULT_DEVICE_ID, DOMAIN
from .coordinator import NiceBidiDataUpdateCoordinator


def bidi_unique_id(entry: ConfigEntry, suffix: str) -> str:
    """Build a stable unique ID for one BiDi entity."""
    target_mac = entry.data[CONF_TARGET_MAC]
    device_id = entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)
    normalized_mac = target_mac.lower().replace(":", "")
    return f"{normalized_mac}_{device_id}_{suffix}"


def bidi_entity_name(entry: ConfigEntry, suffix: str | None = None) -> str:
    """Build a full entity name from the configured gate name."""
    name = str(entry.data.get(CONF_NAME) or entry.title or DOMAIN)
    if suffix is None:
        return name
    return f"{name} {suffix}"


def bidi_suggested_entity_id(domain: str, entry: ConfigEntry, suffix: str | None = None) -> str:
    """Build a registry-managed entity ID suggestion from the configured gate name."""
    object_id = slugify(bidi_entity_name(entry, suffix)) or DOMAIN
    return f"{domain}.{object_id}"


def bidi_device_info(entry: ConfigEntry, info: NiceBidiDeviceInfo | None = None) -> DeviceInfo:
    """Build Home Assistant device info from config and optional INFO metadata."""
    target_mac = entry.data[CONF_TARGET_MAC]
    return DeviceInfo(
        identifiers={(DOMAIN, target_mac)},
        name=entry.data.get(CONF_NAME),
        manufacturer=info.device_manufacturer if info and info.device_manufacturer else "Nice",
        model=(
            info.device_description
            if info and info.device_description
            else info.interface_product
            if info and info.interface_product
            else "BiDi-WiFi"
        ),
        serial_number=info.device_serial if info and info.device_serial else None,
        sw_version=info.device_fw_version if info and info.device_fw_version else None,
        hw_version=info.device_hw_version if info and info.device_hw_version else None,
        configuration_url=f"https://{entry.data[CONF_HOST]}",
    )


class NiceCoordinatorEntity(
    CoordinatorEntity[NiceBidiDataUpdateCoordinator]
):
    """Shared identity and device metadata for Nice entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        *,
        platform_domain: str,
        unique_id_suffix: str,
        name: str | None,
        suggested_id_suffix: str | None,
        description: EntityDescription | None = None,
    ) -> None:
        """Initialize stable identity and registry defaults."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = bidi_unique_id(entry, unique_id_suffix)
        self._attr_name = name
        self.entity_id = bidi_suggested_entity_id(
            platform_domain,
            entry,
            suggested_id_suffix,
        )
        if description is not None:
            self._attr_entity_registry_enabled_default = (
                description.entity_registry_enabled_default
            )
            self._attr_entity_registry_visible_default = (
                description.entity_registry_visible_default
            )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info enriched with INFO metadata."""
        return bidi_device_info(
            self._entry,
            self.coordinator.device_info,
        )
