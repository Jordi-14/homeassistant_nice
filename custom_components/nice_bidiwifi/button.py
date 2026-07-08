"""Button platform for Nice."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import (
    DEP_ACTION_COURTESY_LIGHT,
    DEP_ACTION_COURTESY_LIGHT_TIMER,
    DEP_ACTION_LOCK,
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
    DEP_ACTION_UNLOCK,
)
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_entity_id, bidi_unique_id
from .runtime import get_coordinator

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NiceBidiButtonEntityDescription(ButtonEntityDescription):
    """Description for a Nice button."""

    press_fn: Callable[[NiceBidiDataUpdateCoordinator], Awaitable[None]]
    available_when_offline: bool = False


BUTTONS: tuple[NiceBidiButtonEntityDescription, ...] = (
    NiceBidiButtonEntityDescription(
        key="partial_open_1",
        name="Partial open 1",
        icon="mdi:gate-open",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_1),
    ),
    NiceBidiButtonEntityDescription(
        key="partial_open_2",
        name="Partial open 2",
        icon="mdi:gate-open",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_2),
    ),
    NiceBidiButtonEntityDescription(
        key="partial_open_3",
        name="Partial open 3",
        icon="mdi:gate-open",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_3),
    ),
    NiceBidiButtonEntityDescription(
        key="step_step",
        name="Step-step",
        icon="mdi:gesture-tap-button",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_STEP_STEP),
    ),
    NiceBidiButtonEntityDescription(
        key="courtesy_light",
        name="Courtesy light",
        entity_registry_visible_default=False,
        icon="mdi:lightbulb",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_COURTESY_LIGHT),
    ),
    NiceBidiButtonEntityDescription(
        key="courtesy_light_timer",
        name="Courtesy light timer",
        entity_registry_visible_default=False,
        icon="mdi:timer-outline",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_COURTESY_LIGHT_TIMER),
    ),
    NiceBidiButtonEntityDescription(
        key="lock",
        name="Lock",
        entity_registry_visible_default=False,
        icon="mdi:lock",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_LOCK),
    ),
    NiceBidiButtonEntityDescription(
        key="unlock",
        name="Unlock",
        entity_registry_visible_default=False,
        icon="mdi:lock-open-variant",
        press_fn=lambda coordinator: coordinator.async_send_dep_action(DEP_ACTION_UNLOCK),
    ),
    NiceBidiButtonEntityDescription(
        key="refresh_status",
        name="Refresh status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:refresh",
        press_fn=lambda coordinator: coordinator.async_request_refresh(),
        available_when_offline=True,
    ),
    NiceBidiButtonEntityDescription(
        key="reconnect",
        name="Reconnect",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:connection",
        press_fn=lambda coordinator: coordinator.async_reconnect(),
        available_when_offline=True,
    ),
    NiceBidiButtonEntityDescription(
        key="calibrate_positions",
        name="Calibrate positions",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
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
    """Nice diagnostic button."""

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
        self.entity_id = bidi_entity_id(Platform.BUTTON, entry, description.name)
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        self._attr_entity_registry_visible_default = description.entity_registry_visible_default

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
