"""Tests for the Nice cover entity."""

from __future__ import annotations

from homeassistant.components.cover import CoverEntityFeature

from custom_components.nice_bidiwifi.cover import NiceBidiCover
from tests.conftest import FakeCoordinator, config_entry, make_status


class TestNiceBidiCoverProperties:
    """Test cover entity properties."""

    def test_unique_id(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.unique_id == "aabbccddeeff_1_cover"

    def test_supported_features(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.supported_features & CoverEntityFeature.OPEN
        assert entity.supported_features & CoverEntityFeature.CLOSE
        assert entity.supported_features & CoverEntityFeature.STOP
        assert entity.supported_features & CoverEntityFeature.SET_POSITION

    def test_supported_features_without_position(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state=None, position=None)
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.available is True
        assert entity.supported_features & CoverEntityFeature.OPEN
        assert entity.supported_features & CoverEntityFeature.CLOSE
        assert entity.supported_features & CoverEntityFeature.STOP
        assert not entity.supported_features & CoverEntityFeature.SET_POSITION

    def test_supported_features_uses_display_position(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="stopped", position=None)

        class DisplayCoordinator(FakeCoordinator):
            @property
            def display_position(self) -> float | None:
                return 55.0

        display_coordinator = DisplayCoordinator()
        display_coordinator.data = coordinator.data
        entity = NiceBidiCover(display_coordinator, config_entry())

        assert entity.current_cover_position == 55
        assert entity.supported_features & CoverEntityFeature.SET_POSITION

    def test_current_cover_position(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(position=42.4)
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.current_cover_position == 42

    def test_motion_flags(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="opening")
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.is_opening is True
        assert entity.is_closing is False
        assert entity.is_closed is False

    def test_closed_state(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="closed", position=0.0, current_position=0)
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.is_closed is True
        assert entity.current_cover_position == 0

    def test_unavailable_without_status(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = None
        entity = NiceBidiCover(coordinator, config_entry())
        assert entity.available is False
        assert entity.current_cover_position is None
        assert entity.is_closed is None
        assert entity.extra_state_attributes == {}

    def test_device_info(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCover(coordinator, config_entry())
        info = entity.device_info
        assert info["manufacturer"] == "NICE"
        assert info["model"] == "NewRobus"
        assert info["serial_number"] == "0E6809FF"

    def test_extra_state_attributes(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCover(coordinator, config_entry())
        attrs = entity.extra_state_attributes
        assert attrs["bidi_state"] == "opening"
        assert attrs["current_position_raw"] == 424
        assert attrs["real_position"] == 42.4
        assert attrs["display_position"] == 42.4
        assert attrs["display_position_estimated"] is False
        assert attrs["position_calibration_state"] == "calibrated"
        assert attrs["position_calibration_quality"] == "good"


class TestNiceBidiCoverCommands:
    """Test cover command forwarding."""

    async def test_open_close_stop_cover(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCover(coordinator, config_entry())

        await entity.async_open_cover()
        await entity.async_close_cover()
        await entity.async_stop_cover()

        assert coordinator.calls == [
            ("action", "open"),
            ("action", "close"),
            ("action", "stop"),
        ]

    async def test_set_cover_position(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiCover(coordinator, config_entry())

        await entity.async_set_cover_position(position=55)

        assert coordinator.calls == [("position", 55)]
