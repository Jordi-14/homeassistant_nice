"""Sensor platform for Nice BiDi-WiFi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import NiceBidiStatus
from .const import DOMAIN
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_unique_id


@dataclass(frozen=True, kw_only=True)
class NiceBidiSensorEntityDescription(SensorEntityDescription):
    """Description for a Nice BiDi-WiFi sensor."""

    value_fn: Callable[[NiceBidiDataUpdateCoordinator], datetime | int | str | None]


def _status(coordinator: NiceBidiDataUpdateCoordinator) -> NiceBidiStatus | None:
    return coordinator.data


SENSORS: tuple[NiceBidiSensorEntityDescription, ...] = (
    NiceBidiSensorEntityDescription(
        key="connection_state",
        name="Connection state",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:connection",
        value_fn=lambda coordinator: coordinator.connection_state,
    ),
    NiceBidiSensorEntityDescription(
        key="last_successful_update",
        name="Last successful update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator: coordinator.last_successful_update,
    ),
    NiceBidiSensorEntityDescription(
        key="last_error",
        name="Last error",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:alert-circle-outline",
        value_fn=lambda coordinator: coordinator.last_error or "none",
    ),
    NiceBidiSensorEntityDescription(
        key="reconnect_count",
        name="Reconnect count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator: coordinator.client.reconnect_count,
    ),
    NiceBidiSensorEntityDescription(
        key="last_command",
        name="Last command",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:gesture-tap-button",
        value_fn=lambda coordinator: coordinator.last_command,
    ),
    NiceBidiSensorEntityDescription(
        key="last_command_latency",
        name="Last command latency",
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: coordinator.last_command_latency_ms,
    ),
    NiceBidiSensorEntityDescription(
        key="current_encoder_position",
        name="Current encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).current_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="closed_encoder_position",
        name="Closed encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).closed_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="open_encoder_position",
        name="Open encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).open_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="interface_firmware",
        name="Interface firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.interface_fw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="interface_hardware",
        name="Interface hardware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.interface_hw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="interface_serial",
        name="Interface serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:identifier",
        value_fn=lambda coordinator: coordinator.device_info.interface_serial if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_firmware",
        name="Control unit firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.device_fw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_hardware",
        name="Control unit hardware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.device_hw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_serial",
        name="Control unit serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:identifier",
        value_fn=lambda coordinator: coordinator.device_info.device_serial if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_product_detail",
        name="Control unit product detail",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:information-outline",
        value_fn=lambda coordinator: coordinator.device_info.device_product_detail if coordinator.device_info else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: NiceBidiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(NiceBidiSensor(coordinator, entry, description) for description in SENSORS)


class NiceBidiSensor(CoordinatorEntity[NiceBidiDataUpdateCoordinator], SensorEntity):
    """Nice BiDi-WiFi diagnostic sensor."""

    _attr_has_entity_name = True

    entity_description: NiceBidiSensorEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = bidi_unique_id(entry, description.key)
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default

    @property
    def available(self) -> bool:
        """Return true if this sensor has a known value."""
        return self.native_value is not None

    @property
    def device_info(self):
        """Return device info, enriched with INFO metadata when available."""
        return bidi_device_info(self._entry, self.coordinator.device_info)

    @property
    def native_value(self) -> datetime | int | str | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator)
