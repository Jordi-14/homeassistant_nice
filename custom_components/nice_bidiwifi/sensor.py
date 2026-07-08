"""Sensor platform for Nice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_suggested_object_id, bidi_unique_id
from .runtime import get_coordinator


@dataclass(frozen=True, kw_only=True)
class NiceBidiSensorEntityDescription(SensorEntityDescription):
    """Description for a Nice sensor."""

    value_fn: Callable[[NiceBidiDataUpdateCoordinator], datetime | float | int | str | None]
    extra_attributes_fn: Callable[[NiceBidiDataUpdateCoordinator], dict[str, Any]] | None = None


def _status(coordinator: NiceBidiDataUpdateCoordinator) -> NiceBidiStatus | None:
    return coordinator.data


def _hex_byte(value: int | None) -> str | None:
    if value is None:
        return None
    return f"0x{value:02X}"


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
        entity_registry_visible_default=False,
        value_fn=lambda coordinator: coordinator.last_successful_update,
    ),
    NiceBidiSensorEntityDescription(
        key="last_error",
        name="Last error",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:alert-circle-outline",
        value_fn=lambda coordinator: coordinator.last_error or "none",
    ),
    NiceBidiSensorEntityDescription(
        key="reconnect_count",
        name="Reconnect count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator: coordinator.client.reconnect_count,
    ),
    NiceBidiSensorEntityDescription(
        key="last_command",
        name="Last command",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:gesture-tap-button",
        value_fn=lambda coordinator: coordinator.last_command,
    ),
    NiceBidiSensorEntityDescription(
        key="last_command_latency",
        name="Last command latency",
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: coordinator.last_command_latency_ms,
    ),
    NiceBidiSensorEntityDescription(
        key="position_calibration_state",
        name="Position calibration state",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:map-marker-check",
        value_fn=lambda coordinator: coordinator.calibration_state,
    ),
    NiceBidiSensorEntityDescription(
        key="last_position_calibration",
        name="Last position calibration",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda coordinator: coordinator.calibration_updated_at,
    ),
    NiceBidiSensorEntityDescription(
        key="position_calibration_error",
        name="Position calibration error",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:alert-circle-outline",
        value_fn=lambda coordinator: coordinator.calibration_last_error or "none",
    ),
    NiceBidiSensorEntityDescription(
        key="position_calibration_quality",
        name="Position calibration quality",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:chart-timeline-variant",
        value_fn=lambda coordinator: coordinator.calibration_quality,
    ),
    NiceBidiSensorEntityDescription(
        key="position_calibration_report",
        name="Position calibration report",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:file-chart-outline",
        value_fn=lambda coordinator: coordinator.calibration_report_summary,
        extra_attributes_fn=lambda coordinator: coordinator.calibration_report_attributes,
    ),
    NiceBidiSensorEntityDescription(
        key="gate_position",
        name="Gate position",
        icon="mdi:gate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="current_encoder_position",
        name="Current encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).current_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="closed_encoder_position",
        name="Closed encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).closed_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="open_encoder_position",
        name="Open encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).open_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="max_open_encoder_position",
        name="Max open encoder position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).max_open_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="partial_open_1_position",
        name="Partial open 1 position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).partial_open_1_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="partial_open_2_position",
        name="Partial open 2 position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).partial_open_2_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="partial_open_3_position",
        name="Partial open 3 position",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).partial_open_3_position if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="opening_speed",
        name="Opening speed",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:speedometer",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).opening_speed if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="closing_speed",
        name="Closing speed",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:speedometer",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).closing_speed if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="opening_force",
        name="Opening force",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:arm-flex",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).opening_force if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="closing_force",
        name="Closing force",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:arm-flex",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).closing_force if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="pause_time",
        name="Pause time",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:timer-pause-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).pause_time if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="maintenance_threshold",
        name="Maintenance threshold",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:wrench-clock",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _status(coordinator).maintenance_threshold if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="maintenance_count",
        name="Maintenance count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:wrench",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator: _status(coordinator).maintenance_count if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="total_maneuver_count",
        name="Total maneuver count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator: _status(coordinator).total_maneuver_count if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="last_stop_reason",
        name="Last stop reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:sign-caution",
        value_fn=lambda coordinator: _status(coordinator).last_stop_reason if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="diagnostics_io_byte",
        name="Diagnostics I/O byte",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:code-brackets",
        value_fn=lambda coordinator: _hex_byte(_status(coordinator).diagnostics_io_byte)
        if _status(coordinator)
        else None,
    ),
    NiceBidiSensorEntityDescription(
        key="diagnostics_parameters",
        name="Diagnostics parameters",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:code-array",
        value_fn=lambda coordinator: _status(coordinator).diagnostics_parameters if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="oxi_product",
        name="OXI product",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:radio-tower",
        value_fn=lambda coordinator: _status(coordinator).oxi_product if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="oxi_firmware",
        name="OXI firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: _status(coordinator).oxi_firmware_version if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="oxi_hardware",
        name="OXI hardware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: _status(coordinator).oxi_hardware_version if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="oxi_description",
        name="OXI description",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:information-outline",
        value_fn=lambda coordinator: _status(coordinator).oxi_description if _status(coordinator) else None,
    ),
    NiceBidiSensorEntityDescription(
        key="interface_firmware",
        name="Interface firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.interface_fw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="interface_hardware",
        name="Interface hardware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.interface_hw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="interface_serial",
        name="Interface serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:identifier",
        value_fn=lambda coordinator: coordinator.device_info.interface_serial if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_firmware",
        name="Control unit firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.device_fw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_hardware",
        name="Control unit hardware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:chip",
        value_fn=lambda coordinator: coordinator.device_info.device_hw_version if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_serial",
        name="Control unit serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:identifier",
        value_fn=lambda coordinator: coordinator.device_info.device_serial if coordinator.device_info else None,
    ),
    NiceBidiSensorEntityDescription(
        key="control_unit_product_detail",
        name="Control unit product detail",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
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
    coordinator = get_coordinator(entry)
    async_add_entities(NiceBidiSensor(coordinator, entry, description) for description in SENSORS)


class NiceBidiSensor(CoordinatorEntity[NiceBidiDataUpdateCoordinator], SensorEntity):
    """Nice diagnostic sensor."""

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
        self._attr_suggested_object_id = bidi_suggested_object_id(entry, description.name)
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        self._attr_entity_registry_visible_default = description.entity_registry_visible_default

    @property
    def available(self) -> bool:
        """Return true if this sensor has a known value."""
        return self.native_value is not None

    @property
    def device_info(self):
        """Return device info, enriched with INFO metadata when available."""
        return bidi_device_info(self._entry, self.coordinator.device_info)

    @property
    def native_value(self) -> datetime | float | int | str | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra diagnostic attributes."""
        if self.entity_description.extra_attributes_fn is None:
            return None
        return self.entity_description.extra_attributes_fn(self.coordinator)
