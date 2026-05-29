"""Tests for the Nice BiDi-WiFi cover state switch entity."""

from __future__ import annotations

from custom_components.nice_bidiwifi.switch import NiceBidiCoverSwitch
from tests.conftest import FakeCoordinator, config_entry, make_status


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
