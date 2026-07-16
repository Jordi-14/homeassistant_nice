"""Tests for MyNice cloud cover entities."""

from __future__ import annotations

from homeassistant.components.cover import CoverDeviceClass

from custom_components.nice_bidiwifi.cloud_cover import NiceCloudCover
from custom_components.nice_bidiwifi.const import DOMAIN


class FakeCloudHub:
    """Minimal cloud hub fake for entity tests."""

    def state_for(self, automation_id: int) -> str | None:
        """Return no initial state."""
        return None

    def available_for(self, automation_id: int) -> bool:
        """Return the cloud door as available."""
        return True


def test_cloud_cover_device_identifier_is_connection_method_scoped() -> None:
    """Test cloud devices do not merge with local devices that use the same MAC."""
    door = {
        "automation_id": 12345,
        "device_id": "1",
        "name": "Porta Exterior Pàrquing Cloud",
        "type": "garage",
        "model": "SLIGHTR10",
        "mac": "AA:BB:CC:DD:EE:FF",
        "creds": {"user": "user", "password": "password", "controller": "controller"},
    }

    entity = NiceCloudCover(FakeCloudHub(), door)

    assert entity.has_entity_name is True
    assert entity.name is None
    assert entity.entity_id == "cover.porta_exterior_parquing_cloud"
    assert entity.unique_id == f"{DOMAIN}_cloud_12345"
    assert entity.device_class == CoverDeviceClass.GATE
    assert entity.device_info["identifiers"] == {(DOMAIN, "cloud:12345")}
    assert entity.device_info["name"] == "Porta Exterior Pàrquing Cloud"
    assert "connections" not in entity.device_info
