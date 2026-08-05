"""Switch platform for Nice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .calibration_constants import CALIBRATION_STATE_RUNNING
from .client import STATE_CLOSED, STATE_CLOSING, STATE_OPEN, STATE_OPENING, STATE_PARTIALLY_OPEN, STATE_STOPPED, NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entities.factory import (
    NiceCapabilityKey,
    NiceCoreEntityDescription,
    NiceEntityDescriptionMixin,
    build_described_entities,
    entity_support,
    EntitySupport,
)
from .entity import NiceCoordinatorEntity
from .runtime import get_coordinator
from .protocol.t4.settings import (
    ALWAYS_CLOSE,
    AUTO_CLOSE,
    KEY_LOCK,
    PHOTO_CLOSE,
    PRE_FLASH,
    STANDBY,
    DmpSetting,
)

PARALLEL_UPDATES = 1

COVER_SWITCHES = (
    NiceCoreEntityDescription(
        key="cover_switch",
        required_capability=NiceCapabilityKey.OPEN_CLOSE,
    ),
)


@dataclass(frozen=True, kw_only=True)
class NiceBidiConfigSwitchEntityDescription(
    NiceEntityDescriptionMixin,
    SwitchEntityDescription,
):
    """Description for a writable Nice BusT4 switch."""

    setting: DmpSetting
    value_fn: Callable[[NiceBidiStatus], bool | None]
    required_capability: NiceCapabilityKey = NiceCapabilityKey.DMP


CONFIG_SWITCHES: tuple[NiceBidiConfigSwitchEntityDescription, ...] = (
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_auto_close",
        name="Auto close setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-sync-outline",
        setting=AUTO_CLOSE,
        value_fn=lambda status: status.auto_close,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_photo_close",
        name="Photo close setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:camera-timer",
        setting=PHOTO_CLOSE,
        value_fn=lambda status: status.photo_close,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_always_close",
        name="Always close setting",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:gate-arrow-left",
        setting=ALWAYS_CLOSE,
        value_fn=lambda status: status.always_close,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_standby",
        name="Standby setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_visible_default=False,
        icon="mdi:power-sleep",
        setting=STANDBY,
        value_fn=lambda status: status.standby,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_pre_flash",
        name="Pre-flash setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_visible_default=False,
        icon="mdi:alarm-light-outline",
        setting=PRE_FLASH,
        value_fn=lambda status: status.pre_flash,
    ),
    NiceBidiConfigSwitchEntityDescription(
        key="bus_t4_key_lock",
        name="Key lock setting",
        entity_category=EntityCategory.CONFIG,
        entity_registry_visible_default=False,
        icon="mdi:lock",
        setting=KEY_LOCK,
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
        build_described_entities(
            coordinator,
            entry,
            COVER_SWITCHES,
            lambda entity_coordinator, entity_entry, _description: NiceBidiCoverSwitch(
                entity_coordinator,
                entity_entry,
            ),
        )
        + build_described_entities(
            coordinator,
            entry,
            CONFIG_SWITCHES,
            NiceBidiConfigSwitch,
        )
    )


class NiceBidiCoverSwitch(NiceCoordinatorEntity, SwitchEntity):
    """Nice gate state switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:gate"

    def __init__(self, coordinator: NiceBidiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(
            coordinator,
            entry,
            platform_domain=SWITCH_DOMAIN,
            unique_id_suffix="cover_switch",
            name="Open/close switch",
            suggested_id_suffix=None,
        )

    @property
    def available(self) -> bool:
        """Return true if the latest coordinator update succeeded."""
        return (
            self.coordinator.last_update_success
            and self.status is not None
            and self.status.state is not None
            and entity_support(self.coordinator, COVER_SWITCHES[0])
            is not EntitySupport.UNSUPPORTED
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
        if status.state in {STATE_OPEN, STATE_OPENING, STATE_CLOSING, STATE_STOPPED, STATE_PARTIALLY_OPEN}:
            return True
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open the gate."""
        await self.coordinator.async_send_action("open")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Close the gate."""
        await self.coordinator.async_send_action("close")


class NiceBidiConfigSwitch(NiceCoordinatorEntity, SwitchEntity):
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
        super().__init__(
            coordinator,
            entry,
            platform_domain=SWITCH_DOMAIN,
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
        await self.coordinator.async_write_setting(
            self.entity_description.setting,
            1,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the BusT4 setting."""
        await self.coordinator.async_write_setting(
            self.entity_description.setting,
            0,
        )
