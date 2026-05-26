"""Cover platform for Nice BiDi-WiFi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import STATE_CLOSED, STATE_CLOSING, STATE_OPEN, STATE_OPENING, STATE_STOPPED, NiceBidiStatus
from .const import CONF_DEVICE_ID, CONF_TARGET_MAC, DEFAULT_DEVICE_ID, DOMAIN
from .coordinator import NiceBidiDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cover from a config entry."""
    coordinator: NiceBidiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NiceBidiCover(coordinator, entry)])


class NiceBidiCover(CoordinatorEntity[NiceBidiDataUpdateCoordinator], CoverEntity):
    """Nice BiDi-WiFi gate cover."""

    _attr_device_class = CoverDeviceClass.GATE
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP

    def __init__(self, coordinator: NiceBidiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self._entry = entry
        target_mac = entry.data[CONF_TARGET_MAC]
        device_id = entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)
        self._attr_unique_id = f"{target_mac.lower().replace(':', '')}_{device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, target_mac)},
            name=entry.data.get(CONF_NAME),
            manufacturer="Nice",
            model="BiDi-WiFi",
            configuration_url=f"https://{entry.data[CONF_HOST]}",
        )

    @property
    def available(self) -> bool:
        """Return true if the latest coordinator update succeeded."""
        return self.coordinator.last_update_success and self.status is not None

    @property
    def status(self) -> NiceBidiStatus | None:
        """Return current BiDi status."""
        return self.coordinator.data

    @property
    def current_cover_position(self) -> int | None:
        """Return current position from the BiDi encoder registers."""
        status = self.status
        if status is None or status.position is None:
            return None
        return round(status.position)

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        status = self.status
        if status is None:
            return None
        if status.state == STATE_CLOSED:
            return True
        if status.state in {STATE_OPEN, STATE_OPENING, STATE_CLOSING, STATE_STOPPED}:
            return False
        return None

    @property
    def is_opening(self) -> bool | None:
        """Return if the cover is opening."""
        status = self.status
        return status.state == STATE_OPENING if status else None

    @property
    def is_closing(self) -> bool | None:
        """Return if the cover is closing."""
        status = self.status
        return status.state == STATE_CLOSING if status else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        status = self.status
        if status is None:
            return {}
        return {
            "bidi_state": status.state,
            "current_position_raw": status.current_position,
            "closed_position_raw": status.closed_position,
            "open_position_raw": status.open_position,
            "dmp_registers": status.registers,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the gate."""
        await self.coordinator.async_send_action("open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the gate."""
        await self.coordinator.async_send_action("close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the gate."""
        await self.coordinator.async_send_action("stop")
