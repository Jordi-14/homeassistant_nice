"""Tests for shared Nice entity helpers."""

from __future__ import annotations

from homeassistant.const import CONF_NAME

from custom_components.nice_bidiwifi.button import BUTTONS, NiceBidiButton
from custom_components.nice_bidiwifi.entity import (
    bidi_entity_name,
)
from tests.conftest import FakeCoordinator, config_entry


def test_entity_name_uses_configured_gate_name() -> None:
    """Test entity names do not include the Home Assistant area name."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_entity_name(entry, "Courtesy light") == "p0_garage_door_exterior Courtesy light"


def test_entity_name_keeps_entity_name_suffix() -> None:
    """Test suffixes match the existing entity-name based convention."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_entity_name(entry, "Always close mode setting") == (
        "p0_garage_door_exterior Always close mode setting"
    )


def test_entity_name_without_suffix_is_device_name() -> None:
    """Test primary cover/switch entities use the configured gate name."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_entity_name(entry) == "p0_garage_door_exterior"


def test_button_uses_full_name_without_preassigning_entity_id() -> None:
    """Test entities stay registry-managed while avoiding HA's area prefix."""
    coordinator = FakeCoordinator()
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})
    description = next(description for description in BUTTONS if description.key == "courtesy_light")

    entity = NiceBidiButton(coordinator, entry, description)

    assert entity.has_entity_name is False
    assert entity.name == "p0_garage_door_exterior Courtesy light"
    assert entity.entity_id is None
    assert entity.unique_id == "aabbccddeeff_1_courtesy_light"
