"""Tests for Nice binary sensor entities."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.nice_bidiwifi.binary_sensor import (
    BINARY_SENSORS,
    NiceBidiBinarySensor,
    async_setup_entry,
)
from tests.conftest import FakeCoordinator, config_entry, make_status


def _description(key: str):
    return next(description for description in BINARY_SENSORS if description.key == key)


class TestNiceBidiBinarySensorProperties:
    """Test binary sensor entity properties."""

    def test_binary_sensor_descriptions_have_unique_keys(self) -> None:
        keys = [description.key for description in BINARY_SENSORS]
        assert len(keys) == len(set(keys))
        assert "gate_open" in keys
        assert "limit_closed" in keys
        assert "limit_open" in keys
        assert "photocell" in keys
        assert "obstacle" in keys
        assert "auto_close" in keys
        assert "oxi_detected" in keys

    def test_limit_switch_binary_sensor_reads_status(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiBinarySensor(coordinator, config_entry(), _description("limit_open"))

        assert entity.unique_id == "aabbccddeeff_1_limit_open"
        assert entity.entity_description.entity_registry_enabled_default is False
        assert entity.entity_description.entity_registry_visible_default is False
        assert entity._attr_entity_registry_enabled_default is False
        assert entity._attr_entity_registry_visible_default is False
        assert entity.is_on is True
        assert entity.available is True

    def test_gate_open_binary_sensor_matches_gate_switch_state(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiBinarySensor(coordinator, config_entry(), _description("gate_open"))

        assert entity.unique_id == "aabbccddeeff_1_gate_open"
        assert entity.entity_description.device_class == BinarySensorDeviceClass.OPENING
        assert entity.entity_description.entity_registry_enabled_default is True
        assert entity.entity_description.entity_registry_visible_default is True
        assert entity.is_on is True
        assert entity.available is True

        for state in ("open", "opening", "closing", "stopped", "partially_open"):
            coordinator.data = make_status(state=state)
            assert entity.is_on is True
            assert entity.available is True

        coordinator.data = make_status(state="closed")
        assert entity.is_on is False
        assert entity.available is True

        coordinator.data = make_status(state=None)
        assert entity.is_on is None
        assert entity.available is False

    def test_false_binary_sensor_is_available(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiBinarySensor(coordinator, config_entry(), _description("photocell"))

        assert entity.is_on is False
        assert entity.available is True

    def test_unavailable_when_value_unknown(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(limit_open=None)
        entity = NiceBidiBinarySensor(coordinator, config_entry(), _description("limit_open"))

        assert entity.is_on is None
        assert entity.available is False

    def test_device_info_uses_coordinator_metadata(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiBinarySensor(coordinator, config_entry(), _description("obstacle"))

        assert entity.device_info["serial_number"] == "0E6809FF"


async def test_async_setup_entry_adds_all_binary_sensors() -> None:
    """Test platform setup."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    def add_entities(entities):
        created.extend(list(entities))

    await async_setup_entry(None, entry, add_entities)

    assert len(created) == len(BINARY_SENSORS)
    assert all(isinstance(entity, NiceBidiBinarySensor) for entity in created)
