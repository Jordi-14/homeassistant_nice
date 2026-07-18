"""Cloud hub for MyNice-backed Nice devices."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .cloud_api import NhkClient, NiceApiError, NiceCloud, parse_door_statuses
from .const import DOMAIN

KEEPALIVE_INTERVAL = 25.0
RECONNECT_BACKOFF = 5.0
SIGNAL_STATE = f"{DOMAIN}_cloud_state_{{}}"
SIGNAL_AVAILABLE = f"{DOMAIN}_cloud_avail_{{}}"

_LOGGER = logging.getLogger(__name__)


class NiceHub:
    """Owns discovery results and the single live socket for an account."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        cloud: NiceCloud,
        doors: list[dict],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.cloud = cloud
        self.doors = doors

        self._stopping = False
        self._task: asyncio.Task | None = None
        self._client = NhkClient()
        self._states: dict[int, str] = {}
        self._available: dict[int, bool] = {}

        self._route: dict[str, dict[str, int]] = {}
        self._creds: dict[str, dict] = {}
        for door in doors:
            mac = door.get("mac")
            if mac and door.get("creds"):
                self._route.setdefault(mac, {})[door["device_id"]] = door["automation_id"]
                self._creds[mac] = door["creds"]

    async def async_start(self) -> None:
        """Spawn the single connection task."""
        if not self._creds:
            return
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(), "mynice_connection"
        )

    async def async_stop(self) -> None:
        """Stop the live socket task."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._client.close()

    def state_for(self, automation_id: int) -> str | None:
        """Return the last DoorStatus for an automation."""
        return self._states.get(automation_id)

    def available_for(self, automation_id: int) -> bool:
        """Return whether an automation has an active proxy session."""
        return self._available.get(automation_id, False)

    async def async_door_action(self, door: dict, action: str) -> None:
        """Send open/close/stop for a door over the shared socket."""
        mac = door.get("mac")
        if not self._client.connected or not self._client.has_session(mac):
            raise NiceApiError(f"device {mac} is not connected")
        await self._client.send_change(mac, action, str(door.get("device_id") or "1"))

    def _set_available(self, mac: str, available: bool) -> None:
        for automation_id in self._route.get(mac, {}).values():
            self._available[automation_id] = available
            async_dispatcher_send(self.hass, SIGNAL_AVAILABLE.format(automation_id), available)

    def _publish(self, mac: str, statuses: dict[str, str]) -> None:
        route = self._route.get(mac, {})
        for dev_id, status in statuses.items():
            automation_id = route.get(dev_id)
            if automation_id is None and len(route) == 1:
                automation_id = next(iter(route.values()))
            if automation_id is None:
                continue
            if self._states.get(automation_id) != status:
                _LOGGER.debug("Nice door %s -> %s", automation_id, status)
            self._states[automation_id] = status
            async_dispatcher_send(self.hass, SIGNAL_STATE.format(automation_id), status)

    async def _connect_all(self) -> int:
        """Open the socket and CONNECT every accessory best-effort."""
        await self._client.open()
        for mac, creds in self._creds.items():
            try:
                await self._client.add_session(
                    mac, creds["user"], creds["password"], creds["controller"]
                )
            except (NiceApiError, TimeoutError, OSError) as err:
                _LOGGER.warning("Nice: could not connect accessory %s: %s", mac, err)
                self._set_available(mac, False)
        for mac in list(self._client.sessions):
            self._set_available(mac, True)
            await self._client.send_status(mac)
        return len(self._client.sessions)

    async def _run(self) -> None:
        """Maintain the shared socket, reconnecting all sessions on failure."""
        while not self._stopping:
            try:
                if await self._connect_all() == 0:
                    raise NiceApiError("no accessories connected")
                last_ka = monotonic()
                while not self._stopping:
                    frame = await self._client.read_frame(timeout=5.0)
                    if frame:
                        if "<Error" in frame:
                            _LOGGER.debug("NHK error frame: %s", frame.strip())
                        mac = self._client.route(frame)
                        if mac:
                            statuses = parse_door_statuses(frame)
                            if statuses:
                                self._publish(mac, statuses)
                    if monotonic() - last_ka >= KEEPALIVE_INTERVAL:
                        for mac in list(self._client.sessions):
                            await self._client.send_status(mac)
                        last_ka = monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Nice connection lost: %s", err)
            finally:
                for mac in self._creds:
                    self._set_available(mac, False)
                await self._client.close()
            if not self._stopping:
                await asyncio.sleep(RECONNECT_BACKOFF)
