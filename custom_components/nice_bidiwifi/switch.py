"""Switch platform for Nice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import STATE_CLOSED, STATE_CLOSING, STATE_OPEN, STATE_OPENING, STATE_STOPPED, NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_entity_id, bidi_unique_id
from .runtime import get_coordinator

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NiceBidiConfigSwitchEntityDescription(SwitchEntityDescription):
    """Description for a writable Nice BusT4 switch."""

    register_parameter: int
    value_fn: Callable[[NiceBidiStatus], bool | None]


CONFIG_SWITCHES: tuple[NiceBidiConfigSwitchEntityDescription, ...] = (
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_auto_close",
        name="Auto close setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-sync-outline",
        register_parameter=0x80,
        value_fn=lambda status: status.auto_close,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_photo_close",
        name="Photo close setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:camera-timer",
        register_parameter=0x84,
        value_fn=lambda status: status.photo_close,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_always_close",
        name="Always close setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:gate-arrow-left",
        register_parameter=0x88,
        value_fn=lambda status: status.always_close,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_standby",
        name="Standby setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_visible_default=False,
        icon="mdi:power-sleep",
        register_parameter=0x8C,
        value_fn=lambda status: status.standby,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_pre_flash",
        name="Pre-flash setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_visible_default=False,
        icon="mdi:alarm-light-outline",
        register_parameter=0x94,
        value_fn=lambda status: status.pre_flash,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_key_lock",
        name="Key lock setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_visible_default=False,
        icon="mdi:lock",
        register_parameter=0x9C,
        value_fn=lambda status: status.key_lock,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities(
        [NiceBidiCoverSwitch(coordinator, entry)]
        + [
            NiceBidiConfigSwitch(coordinator, entry, description)
            for description in CONFIG_SWITCHES
        ]
    )


class NiceBidiCoverSwitch(CoordinatorEntity[NiceBidiDataUpdateCoordinator], SwitchEntity):
    """Nice gate state switch."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:gate"

    def __init__(self, coordinator: NiceBidiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = bidi_unique_id(entry, "cover_switch")
        self.entity_id = bidi_entity_id(Platform.SWITCH, entry)

    @property
    def device_info(self):
        """Return device info, enriched with INFO metadata when available."""
        return bidi_device_info(self._entry, self.coordinator.device_info)

    @property
    def available(self) -> bool:
        """Return true if the latest coordinator update succeeded."""
        return (
            self.coordinator.last_update_success
            and self.status is not None
            and self.status.state is not None
        )

    @property
    def status(self) -> NiceBidiStatus | None:
        """Return current BiDi status."""
        return self.coordinator.data

    @property
    def is_on(self) -> bool | None:
        """Return true when the gate is not closed."""
        status = self.status
        if status is None:
            return None
        if status.state == STATE_CLOSED:
            return False
        if status.state in {STATE_OPEN, STATE_OPENING, STATE_CLOSING, STATE_STOPPED}:
            return True
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open the gate."""
        await self.coordinator.async_send_action("open")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Close the gate."""
        await self.coordinator.async_send_action("close")


class NiceBidiConfigSwitch(CoordinatorEntity[NiceBidiDataUpdateCoordinator], SwitchEntity):
    """Writable Nice BusT4 configuration switch."""

    _attr_has_entity_name = True

    entity_description: NiceBidiConfigSwitchEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiConfigSwitchEntityDescription,
    ) -> None:
        """Initialize the config switch."""
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = bidi_unique_id(entry, description.key)
        self.entity_id = bidi_entity_id(Platform.SWITCH, entry, description.name)
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
            and not status.is_moving
            and self.is_on is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the current BusT4 switch value."""
        status = self.coordinator.data
        if status is None:
            return None
        return self.entity_description.value_fn(status)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the BusT4 setting."""
        await self.coordinator.async_write_dmp_register(
            0x04,
            self.entity_description.register_parameter,
            1,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the BusT4 setting."""
        await self.coordinator.async_write_dmp_register(
            0x04,
            self.entity_description.register_parameter,
            0,
        )
