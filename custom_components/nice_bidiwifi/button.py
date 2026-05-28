"""Button platform for Nice BiDi-WiFi."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_unique_id
from .runtime import get_coordinator

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NiceBidiButtonEntityDescription(ButtonEntityDescription):
    """Description for a Nice BiDi-WiFi button."""

    press_fn: Callable[[NiceBidiDataUpdateCoordinator], Awaitable[None]]
    available_when_offline: bool = False


BUTTONS: tuple[NiceBidiButtonEntityDescription, ...] = (
    NiceBidiButtonEntityDescription(
        key="refresh_status",
        name="Refresh status",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:refresh",
        press_fn=lambda coordinator: coordinator.async_request_refresh(),
        available_when_offline=True,
    ),
    NiceBidiButtonEntityDescription(
        key="reconnect",
        name="Reconnect",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:connection",
        press_fn=lambda coordinator: coordinator.async_reconnect(),
        available_when_offline=True,
    ),
    NiceBidiButtonEntityDescription(
        key="calibrate_positions",
        name="Calibrate positions",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:map-marker-path",
        press_fn=lambda coordinator: coordinator.async_start_position_calibration(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities(NiceBidiButton(coordinator, entry, description) for description in BUTTONS)


class NiceBidiButton(CoordinatorEntity[NiceBidiDataUpdateCoordinator], ButtonEntity):
    """Nice BiDi-WiFi diagnostic button."""

    _attr_has_entity_name = True

    entity_description: NiceBidiButtonEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = bidi_unique_id(entry, description.key)
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default

    @property
    def device_info(self):
        """Return device info, enriched with INFO metadata when available."""
        return bidi_device_info(self._entry, self.coordinator.device_info)

    @property
    def available(self) -> bool:
        """Return if the diagnostic button can currently be pressed."""
        if self.entity_description.available_when_offline:
            return True
        return super().available

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.coordinator)
