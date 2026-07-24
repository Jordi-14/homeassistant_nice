"""Number platform for Nice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .calibration_constants import CALIBRATION_STATE_RUNNING
from .client import NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entities.factory import (
    NiceCapabilityKey,
    NiceEntityDescriptionMixin,
    build_described_entities,
)
from .entity import NiceCoordinatorEntity
from .runtime import get_coordinator
from .protocol.t4.settings import (
    ALWAYS_CLOSE_MODE,
    ALWAYS_CLOSE_TIME,
    CLOSING_FORCE,
    CLOSING_SPEED,
    MAINTENANCE_THRESHOLD,
    OPENING_FORCE,
    OPENING_SPEED,
    PARTIAL_OPEN_1_POSITION,
    PARTIAL_OPEN_2_POSITION,
    PARTIAL_OPEN_3_POSITION,
    PAUSE_TIME,
    PHOTO_CLOSE_MODE,
    PHOTO_CLOSE_TIME,
    DmpSetting,
)


@dataclass(frozen=True, kw_only=True)
class NiceBidiNumberEntityDescription(
    NiceEntityDescriptionMixin,
    NumberEntityDescription,
):
    """Description for a writable Nice BusT4 number."""

    setting: DmpSetting
    value_fn: Callable[[NiceBidiStatus], int | None]
    dynamic_max_fn: Callable[[NiceBidiStatus], int | None] | None = None
    required_capability: NiceCapabilityKey = NiceCapabilityKey.DMP


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
        setting=PAUSE_TIME,
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
        setting=OPENING_FORCE,
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
        setting=CLOSING_FORCE,
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
        setting=OPENING_SPEED,
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
        setting=CLOSING_SPEED,
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
        setting=PHOTO_CLOSE_TIME,
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
        setting=PHOTO_CLOSE_MODE,
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
        setting=ALWAYS_CLOSE_TIME,
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
        setting=ALWAYS_CLOSE_MODE,
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
        setting=PARTIAL_OPEN_1_POSITION,
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
        setting=PARTIAL_OPEN_2_POSITION,
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
        setting=PARTIAL_OPEN_3_POSITION,
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
        setting=MAINTENANCE_THRESHOLD,
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
    async_add_entities(
        build_described_entities(
            coordinator,
            entry,
            NUMBERS,
            NiceBidiNumber,
        )
    )


class NiceBidiNumber(NiceCoordinatorEntity, NumberEntity):
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
        super().__init__(
            coordinator,
            entry,
            platform_domain=NUMBER_DOMAIN,
            unique_id_suffix=description.key,
            name=description.name,
            suggested_id_suffix=description.name,
            description=description,
        )
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return true if this config value is known."""
        status = self.coordinator.data
        return (
            self.coordinator.last_update_success
            and status is not None
            and self.coordinator.calibration_state != CALIBRATION_STATE_RUNNING
            and not self._disabled_for_device()
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
        if self._disabled_for_device():
            raise HomeAssistantError(
                f"{self.entity_description.name} is disabled for this controller model"
            )
        int_value = int(round(value))
        min_value = self.native_min_value
        max_value = self.native_max_value
        if int_value < min_value or int_value > max_value:
            raise ValueError(f"{self.entity_description.name} must be between {min_value} and {max_value}")
        await self.coordinator.async_write_setting(
            self.entity_description.setting,
            int_value,
        )

    def _disabled_for_device(self) -> bool:
        """Return true when this config value should not be changed on this device."""
        return (
            self.coordinator.setting_write_block_reason(
                self.entity_description.setting
            )
            is not None
        )
