"""Cover platform for MyNice cloud covers."""

from __future__ import annotations

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .cloud_coordinator import NiceHub, SIGNAL_AVAILABLE, SIGNAL_STATE
from .const import DOMAIN

STATE_CLOSED = "closed"
STATE_CLOSING = "closing"
STATE_OPENING = "opening"


async def async_setup_cloud_covers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MyNice cloud covers from a config entry."""
    hub: NiceHub = entry.runtime_data
    async_add_entities(NiceCloudCover(hub, door) for door in hub.doors if door.get("creds"))


class NiceCloudCover(CoverEntity):
    """A single Nice cloud cover."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = CoverDeviceClass.GATE
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    _attr_should_poll = False

    def __init__(self, hub: NiceHub, door: dict) -> None:
        self._hub = hub
        self._door = door
        self._automation_id = door["automation_id"]
        self._status = hub.state_for(self._automation_id)
        self._attr_unique_id = f"{DOMAIN}_cloud_{self._automation_id}"
        device_name = str(door.get("name") or "Nice gate")
        self.entity_id = f"{COVER_DOMAIN}.{slugify(device_name) or DOMAIN}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"cloud:{self._automation_id}")},
            name=device_name,
            manufacturer="Nice",
            model=door.get("model") or door.get("type"),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to live state and availability updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_STATE.format(self._automation_id),
                self._handle_state,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABLE.format(self._automation_id),
                self._handle_available,
            )
        )

    @callback
    def _handle_state(self, status: str) -> None:
        self._status = status
        self.async_write_ha_state()

    @callback
    def _handle_available(self, _available: bool) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._hub.available_for(self._automation_id)

    @property
    def is_closed(self) -> bool | None:
        if self._status is None:
            return None
        return self._status == STATE_CLOSED

    @property
    def is_opening(self) -> bool:
        return self._status == STATE_OPENING

    @property
    def is_closing(self) -> bool:
        return self._status == STATE_CLOSING

    async def async_open_cover(self, **kwargs) -> None:
        await self._hub.async_door_action(self._door, "open")

    async def async_close_cover(self, **kwargs) -> None:
        await self._hub.async_door_action(self._door, "close")

    async def async_stop_cover(self, **kwargs) -> None:
        await self._hub.async_door_action(self._door, "stop")
