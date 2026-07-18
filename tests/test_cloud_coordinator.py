"""Tests for MyNice cloud coordinator behavior."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.cloud_coordinator import NiceHub
from custom_components.nice_bidiwifi.const import (
    CONF_CONNECTION_METHOD,
    CONNECTION_METHOD_CLOUD,
    DOMAIN,
)


class FakeNhkClient:
    """Minimal NHK client fake."""

    connected = True

    def __init__(self) -> None:
        self.sent_changes: list[tuple[str, str, str]] = []

    def has_session(self, mac: str) -> bool:
        """Return true for any MAC with a session."""
        return True

    async def send_change(self, mac: str, action: str, device_id: str = "1") -> None:
        """Record the cloud command."""
        self.sent_changes.append((mac, action, device_id))


async def test_door_action_sends_discovered_device_id(hass: HomeAssistant) -> None:
    """Test cloud actions use the door's discovered device ID."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD,
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
        },
        entry_id="cloud-entry",
    )
    client = FakeNhkClient()
    hub = NiceHub(hass, entry, cloud=None, doors=[])
    hub._client = client

    await hub.async_door_action(
        {"mac": "AA:BB:CC:DD:EE:FF", "device_id": "2"},
        "close",
    )

    assert client.sent_changes == [("AA:BB:CC:DD:EE:FF", "close", "2")]
