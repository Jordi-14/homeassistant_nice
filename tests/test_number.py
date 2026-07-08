"""Tests for Nice number entities."""

from __future__ import annotations

import pytest
from homeassistant.const import PERCENTAGE, UnitOfTime

from custom_components.nice_bidiwifi.number import (
    NUMBERS,
    NiceBidiNumber,
    async_setup_entry,
)
from tests.conftest import FakeCoordinator, config_entry, make_status


def _description(key: str):
    return next(description for description in NUMBERS if description.key == key)


class TestNiceBidiNumberProperties:
    """Test BusT4 configuration number properties."""

    def test_number_descriptions_have_unique_keys(self) -> None:
        keys = [description.key for description in NUMBERS]
        assert len(keys) == len(set(keys))
        assert "bus_t4_opening_speed" in keys
        assert "bus_t4_partial_open_1_position" in keys
        assert "bus_t4_maintenance_threshold" in keys

    def test_number_reads_status(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.unique_id == "aabbccddeeff_1_bus_t4_opening_speed"
        assert entity.entity_description.entity_registry_enabled_default is True
        assert entity.entity_description.entity_registry_visible_default is True
        assert entity.native_value == 60
        assert entity.native_unit_of_measurement == PERCENTAGE
        assert entity.native_min_value == 1
        assert entity.native_max_value == 100
        assert entity.available is True

    def test_pause_time_uses_seconds(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_pause_time"))

        assert entity.native_value == 30
        assert entity.native_unit_of_measurement == UnitOfTime.SECONDS

    def test_partial_open_position_uses_dynamic_max(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", max_open_position=4043, open_position=4000)
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_partial_open_1_position"),
        )

        assert entity.native_value == 250
        assert entity.native_max_value == 4043

    def test_mode_registers_allow_full_raw_byte_range(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", always_close_mode=32)
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_always_close_mode"),
        )

        assert entity.native_value == 32
        assert entity.native_max_value == 255

    def test_unavailable_when_value_unknown(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", opening_speed=None)
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.native_value is None
        assert entity.available is False

    def test_unavailable_while_gate_is_moving(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="opening")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.native_value == 60
        assert entity.available is False

    async def test_number_writes_single_byte_register(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        await entity.async_set_native_value(75)

        assert coordinator.calls == [("dmp_write", (0x04, 0x42, 75, 1))]

    async def test_number_writes_two_byte_register(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(max_open_position=4043)
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_partial_open_1_position"),
        )

        await entity.async_set_native_value(1500)

        assert coordinator.calls == [("dmp_write", (0x04, 0x21, 1500, 2))]

    async def test_number_rejects_out_of_range_value(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        with pytest.raises(ValueError, match="between 1 and 100"):
            await entity.async_set_native_value(101)


async def test_async_setup_entry_adds_all_numbers() -> None:
    """Test number platform setup."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    def add_entities(entities):
        created.extend(list(entities))

    await async_setup_entry(None, entry, add_entities)

    assert len(created) == len(NUMBERS)
    assert all(isinstance(entity, NiceBidiNumber) for entity in created)
