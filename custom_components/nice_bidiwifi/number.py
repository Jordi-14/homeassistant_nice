"""Number platform for Nice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .calibration_constants import CALIBRATION_STATE_RUNNING
from .client import NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_suggested_entity_id, bidi_unique_id
from .runtime import get_coordinator


@dataclass(frozen=True, kw_only=True)
class NiceBidiNumberEntityDescription(NumberEntityDescription):
    """Description for a writable Nice BusT4 number."""

    register_parameter: int
    value_size: int
    value_fn: Callable[[NiceBidiStatus], int | None]
    dynamic_max_fn: Callable[[NiceBidiStatus], int | None] | None = None


def _max_known_open_position(status: NiceBidiStatus) -> int | None:
    return status.max_open_position or status.open_position


NUMBERS: tuple[NiceBidiNumberEntityDescription, ...] = (
    NiceBidiNumberEntityDescription(
        key="bus_t4_pause_time",
        name="Pause time setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-pause-outline",
        native_min_value=0,
        native_max_value=250,
        native_step=5,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        register_parameter=0x81,
        value_size=1,
        value_fn=lambda status: status.pause_time,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_opening_force",
        name="Opening force setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arm-flex",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        register_parameter=0x4A,
        value_size=1,
        value_fn=lambda status: status.opening_force,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_closing_force",
        name="Closing force setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arm-flex",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        register_parameter=0x4B,
        value_size=1,
        value_fn=lambda status: status.closing_force,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_opening_speed",
        name="Opening speed setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:speedometer",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        register_parameter=0x42,
        value_size=1,
        value_fn=lambda status: status.opening_speed,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_closing_speed",
        name="Closing speed setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:speedometer",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        register_parameter=0x43,
        value_size=1,
        value_fn=lambda status: status.closing_speed,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_photo_close_time",
        name="Photo close time setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:camera-timer",
        native_min_value=0,
        native_max_value=250,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        register_parameter=0x85,
        value_size=1,
        value_fn=lambda status: status.photo_close_time,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_photo_close_mode",
        name="Photo close mode setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:tune-variant",
        native_min_value=0,
        native_max_value=255,
        native_step=1,
        mode=NumberMode.BOX,
        register_parameter=0x86,
        value_size=1,
        value_fn=lambda status: status.photo_close_mode,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_always_close_time",
        name="Always close time setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-lock-outline",
        native_min_value=0,
        native_max_value=250,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        register_parameter=0x89,
        value_size=1,
        value_fn=lambda status: status.always_close_time,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_always_close_mode",
        name="Always close mode setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:tune-variant",
        native_min_value=0,
        native_max_value=255,
        native_step=1,
        mode=NumberMode.BOX,
        register_parameter=0x8A,
        value_size=1,
        value_fn=lambda status: status.always_close_mode,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_partial_open_1_position",
        name="Partial open 1 position setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:gate-open",
        native_min_value=0,
        native_max_value=65535,
        native_step=1,
        mode=NumberMode.BOX,
        register_parameter=0x21,
        value_size=2,
        value_fn=lambda status: status.partial_open_1_position,
        dynamic_max_fn=_max_known_open_position,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_partial_open_2_position",
        name="Partial open 2 position setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:gate-open",
        native_min_value=0,
        native_max_value=65535,
        native_step=1,
        mode=NumberMode.BOX,
        register_parameter=0x22,
        value_size=2,
        value_fn=lambda status: status.partial_open_2_position,
        dynamic_max_fn=_max_known_open_position,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_partial_open_3_position",
        name="Partial open 3 position setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:gate-open",
        native_min_value=0,
        native_max_value=65535,
        native_step=1,
        mode=NumberMode.BOX,
        register_parameter=0x23,
        value_size=2,
        value_fn=lambda status: status.partial_open_3_position,
        dynamic_max_fn=_max_known_open_position,
    ),
    NiceBidiNumberEntityDescription(
        key="bus_t4_maintenance_threshold",
        name="Maintenance threshold setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:wrench-clock",
        native_min_value=0,
        native_max_value=65535,
        native_step=1,
        mode=NumberMode.BOX,
        register_parameter=0xB1,
        value_size=2,
        value_fn=lambda status: status.maintenance_threshold,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up numbers from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities(NiceBidiNumber(coordinator, entry, description) for description in NUMBERS)


class NiceBidiNumber(CoordinatorEntity[NiceBidiDataUpdateCoordinator], NumberEntity):
    """Writable Nice BusT4 configuration number."""

    _attr_has_entity_name = True

    entity_description: NiceBidiNumberEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiNumberEntityDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = bidi_unique_id(entry, description.key)
        self._attr_name = description.name
        self.entity_id = bidi_suggested_entity_id(NUMBER_DOMAIN, entry, description.name)
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        self._attr_entity_registry_visible_default = description.entity_registry_visible_default

    @property
    def device_info(self):
        """Return device info, enriched with INFO metadata when available."""
        return bidi_device_info(self._entry, self.coordinator.device_info)

    @property
    def available(self) -> bool:
        """Return true if this config value is known."""
        status = self.coordinator.data
        return (
            self.coordinator.last_update_success
            and status is not None
            and self.coordinator.calibration_state != CALIBRATION_STATE_RUNNING
            and not status.is_moving
            and self.native_value is not None
        )

    @property
    def native_value(self) -> int | None:
        """Return the current BusT4 number value."""
        status = self.coordinator.data
        if status is None:
            return None
        return self.entity_description.value_fn(status)

    @property
    def native_max_value(self) -> float:
        """Return the dynamic maximum value when the controller reports one."""
        dynamic_max_fn = self.entity_description.dynamic_max_fn
        status = self.coordinator.data
        if dynamic_max_fn is not None and status is not None:
            dynamic_max = dynamic_max_fn(status)
            if dynamic_max is not None:
                return dynamic_max
        return self.entity_description.native_max_value or 0

    async def async_set_native_value(self, value: float) -> None:
        """Write the BusT4 number value."""
        int_value = int(round(value))
        min_value = self.native_min_value
        max_value = self.native_max_value
        if int_value < min_value or int_value > max_value:
            raise ValueError(f"{self.entity_description.name} must be between {min_value} and {max_value}")
        await self.coordinator.async_write_dmp_register(
            0x04,
            self.entity_description.register_parameter,
            int_value,
            size=self.entity_description.value_size,
        )
