"""Tests for shared Nice entity helpers."""

from __future__ import annotations

from homeassistant.const import CONF_NAME, Platform

from custom_components.nice_bidiwifi.button import BUTTONS, NiceBidiButton
from custom_components.nice_bidiwifi.entity import (
    bidi_entity_id,
    bidi_suggested_object_id,
)
from tests.conftest import FakeCoordinator, config_entry


def test_suggested_object_id_uses_configured_gate_name() -> None:
    """Test suggested object IDs do not include the Home Assistant area name."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_suggested_object_id(entry, "Courtesy light") == "p0_garage_door_exterior_courtesy_light"


def test_suggested_object_id_slugs_entity_name_suffix() -> None:
    """Test the suffix matches the existing entity-name based convention."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_suggested_object_id(entry, "Always close mode setting") == (
        "p0_garage_door_exterior_always_close_mode_setting"
    )


def test_suggested_object_id_without_suffix_is_device_name() -> None:
    """Test primary cover/switch entities use the configured gate name."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_suggested_object_id(entry) == "p0_garage_door_exterior"


def test_entity_id_uses_configured_gate_name() -> None:
    """Test entity ID suggestions bypass Home Assistant's device-name prefix."""
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    assert bidi_entity_id(Platform.BUTTON, entry, "Courtesy light") == (
        "button.p0_garage_door_exterior_courtesy_light"
    )


def test_button_sets_initial_entity_id_suggestion() -> None:
    """Test platforms pass the desired entity ID into Home Assistant registration."""
    coordinator = FakeCoordinator()
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})
    description = next(description for description in BUTTONS if description.key == "courtesy_light")

    entity = NiceBidiButton(coordinator, entry, description)

    assert entity.entity_id == "button.p0_garage_door_exterior_courtesy_light"
