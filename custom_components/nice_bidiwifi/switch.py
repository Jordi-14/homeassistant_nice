"""Switch platform for Nice."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import STATE_CLOSED, STATE_CLOSING, STATE_OPEN, STATE_OPENING, STATE_STOPPED, NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import bidi_device_info, bidi_unique_id
from .runtime import get_coordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities([NiceBidiCoverSwitch(coordinator, entry)])


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
