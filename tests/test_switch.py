"""Tests for the Nice cover state switch entity."""

from __future__ import annotations

from custom_components.nice_bidiwifi.switch import (
    CONFIG_SWITCHES,
    NiceBidiConfigSwitch,
    NiceBidiCoverSwitch,
    async_setup_entry,
)
from tests.conftest import FakeCoordinator, config_entry, make_status


def _description(key: str):
    return next(description for description in CONFIG_SWITCHES if description.key == key)


class TestNiceBidiCoverSwitchProperties:
    """Test switch entity properties."""

    def test_unique_id(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCoverSwitch(coordinator, config_entry())
        assert entity.unique_id == "aabbccddeeff_1_cover_switch"

    def test_on_states(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="opening")
        entity = NiceBidiCoverSwitch(coordinator, config_entry())

        assert entity.is_on is True

        coordinator.data = make_status(state="open")
        assert entity.is_on is True

        coordinator.data = make_status(state="closing")
        assert entity.is_on is True

        coordinator.data = make_status(state="stopped")
        assert entity.is_on is True

        coordinator.data = make_status(state="partially_open")
        assert entity.is_on is True
        assert entity.available is True

    def test_off_state(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="closed")
        entity = NiceBidiCoverSwitch(coordinator, config_entry())

        assert entity.is_on is False

    def test_unavailable_without_status(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = None
        entity = NiceBidiCoverSwitch(coordinator, config_entry())

        assert entity.available is False
        assert entity.is_on is None

    def test_unavailable_without_status_state(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state=None, position=None)
        entity = NiceBidiCoverSwitch(coordinator, config_entry())

        assert entity.available is False
        assert entity.is_on is None


class TestNiceBidiCoverSwitchCommands:
    """Test switch command forwarding."""

    async def test_turn_on_off_switch(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCoverSwitch(coordinator, config_entry())

        await entity.async_turn_on()
        await entity.async_turn_off()

        assert coordinator.calls == [
            ("action", "open"),
            ("action", "close"),
        ]


class TestNiceBidiConfigSwitchProperties:
    """Test BusT4 configuration switch entities."""

    def test_config_switch_descriptions_have_unique_keys(self) -> None:
        keys = [description.key for description in CONFIG_SWITCHES]
        assert len(keys) == len(set(keys))
        assert "bus_t4_auto_close" in keys
        assert "bus_t4_key_lock" in keys

    def test_config_switch_reads_status(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open")
        entity = NiceBidiConfigSwitch(coordinator, config_entry(), _description("bus_t4_auto_close"))

        assert entity.unique_id == "aabbccddeeff_1_bus_t4_auto_close"
        assert entity.entity_description.entity_registry_enabled_default is True
        assert entity.entity_description.entity_registry_visible_default is True
        assert entity.is_on is True
        assert entity.available is True

    def test_config_switch_unavailable_when_value_unknown(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", auto_close=None)
        entity = NiceBidiConfigSwitch(coordinator, config_entry(), _description("bus_t4_auto_close"))

        assert entity.is_on is None
        assert entity.available is False

    def test_config_switch_unavailable_while_gate_is_moving(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="opening")
        entity = NiceBidiConfigSwitch(coordinator, config_entry(), _description("bus_t4_auto_close"))

        assert entity.is_on is True
        assert entity.available is False

    async def test_config_switch_writes_register(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiConfigSwitch(coordinator, config_entry(), _description("bus_t4_auto_close"))

        await entity.async_turn_on()
        await entity.async_turn_off()

        assert coordinator.calls == [
            ("dmp_write", (0x04, 0x80, 1, 1)),
            ("dmp_write", (0x04, 0x80, 0, 1)),
        ]


async def test_async_setup_entry_adds_cover_and_config_switches() -> None:
    """Test switch platform setup."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    def add_entities(entities):
        created.extend(list(entities))

    await async_setup_entry(None, entry, add_entities)

    assert len(created) == 1 + len(CONFIG_SWITCHES)
    assert any(isinstance(entity, NiceBidiCoverSwitch) for entity in created)
    assert sum(isinstance(entity, NiceBidiConfigSwitch) for entity in created) == len(CONFIG_SWITCHES)
