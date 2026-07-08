"""Binary sensor platform for Nice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_suggested_object_id, bidi_unique_id
from .runtime import get_coordinator


@dataclass(frozen=True, kw_only=True)
class NiceBidiBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for a Nice binary sensor."""

    value_fn: Callable[[NiceBidiStatus], bool | None]


BINARY_SENSORS: tuple[NiceBidiBinarySensorEntityDescription, ...] = (
    NiceBidiBinarySensorEntityDescription(
        key="limit_closed",
        name="Closed limit switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.limit_closed,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="limit_open",
        name="Open limit switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.limit_open,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="photocell",
        name="Photocell",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.photocell,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="obstacle",
        name="Obstacle detected",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.obstacle,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="input_1",
        name="Input 1 enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.input_1,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="input_2",
        name="Input 2 enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.input_2,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="input_3",
        name="Input 3 enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.input_3,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="input_4",
        name="Input 4 enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.input_4,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="auto_close",
        name="Auto close",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.auto_close,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="photo_close",
        name="Photo close",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.photo_close,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="always_close",
        name="Always close",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.always_close,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="standby",
        name="Standby",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.standby,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="pre_flash",
        name="Pre-flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.pre_flash,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="key_lock",
        name="Key lock",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.key_lock,
    ),
    NiceBidiBinarySensorEntityDescription(
        key="oxi_detected",
        name="OXI receiver detected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda status: status.oxi_detected,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities(NiceBidiBinarySensor(coordinator, entry, description) for description in BINARY_SENSORS)


class NiceBidiBinarySensor(CoordinatorEntity[NiceBidiDataUpdateCoordinator], BinarySensorEntity):
    """Nice BusT4 binary diagnostic sensor."""

    _attr_has_entity_name = True

    entity_description: NiceBidiBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = bidi_unique_id(entry, description.key)
        self._attr_suggested_object_id = bidi_suggested_object_id(entry, description.name)
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        self._attr_entity_registry_visible_default = description.entity_registry_visible_default

    @property
    def device_info(self):
        """Return device info, enriched with INFO metadata when available."""
        return bidi_device_info(self._entry, self.coordinator.device_info)

    @property
    def is_on(self) -> bool | None:
        """Return the binary value."""
        status = self.coordinator.data
        if status is None:
            return None
        return self.entity_description.value_fn(status)

    @property
    def available(self) -> bool:
        """Return true if a value is known."""
        return self.coordinator.last_update_success and self.is_on is not None
