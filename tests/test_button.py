"""Tests for Nice BiDi-WiFi button entities."""

from __future__ import annotations

from custom_components.nice_bidiwifi.button import (
    BUTTONS,
    NiceBidiButton,
    async_setup_entry,
)
from custom_components.nice_bidiwifi.client import (
    DEP_ACTION_COURTESY_LIGHT,
    DEP_ACTION_COURTESY_LIGHT_TIMER,
    DEP_ACTION_LOCK,
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
    DEP_ACTION_UNLOCK,
)
from tests.conftest import FakeCoordinator, config_entry


async def test_async_setup_entry_adds_all_buttons() -> None:
    """Test platform setup."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    def add_entities(entities):
        created.extend(list(entities))

    await async_setup_entry(None, entry, add_entities)

    assert len(created) == len(BUTTONS)
    assert all(isinstance(entity, NiceBidiButton) for entity in created)


async def test_buttons_delegate_to_expected_coordinator_methods() -> None:
    """Test button press handlers."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    buttons = [NiceBidiButton(coordinator, entry, description) for description in BUTTONS]

    for entity in buttons:
        await entity.async_press()

    assert coordinator.calls == [
        ("dep_action", DEP_ACTION_PARTIAL_OPEN_1),
        ("dep_action", DEP_ACTION_PARTIAL_OPEN_2),
        ("dep_action", DEP_ACTION_PARTIAL_OPEN_3),
        ("dep_action", DEP_ACTION_STEP_STEP),
        ("dep_action", DEP_ACTION_COURTESY_LIGHT),
        ("dep_action", DEP_ACTION_COURTESY_LIGHT_TIMER),
        ("dep_action", DEP_ACTION_LOCK),
        ("dep_action", DEP_ACTION_UNLOCK),
        ("refresh", None),
        ("reconnect", None),
        ("calibrate", None),
    ]


def test_button_unique_ids() -> None:
    """Test button unique IDs."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    buttons = [NiceBidiButton(coordinator, entry, description) for description in BUTTONS]

    assert [entity.unique_id for entity in buttons] == [
        "aabbccddeeff_1_partial_open_1",
        "aabbccddeeff_1_partial_open_2",
        "aabbccddeeff_1_partial_open_3",
        "aabbccddeeff_1_step_step",
        "aabbccddeeff_1_courtesy_light",
        "aabbccddeeff_1_courtesy_light_timer",
        "aabbccddeeff_1_lock",
        "aabbccddeeff_1_unlock",
        "aabbccddeeff_1_refresh_status",
        "aabbccddeeff_1_reconnect",
        "aabbccddeeff_1_calibrate_positions",
    ]


def test_reconnect_and_refresh_buttons_remain_available_when_coordinator_failed() -> None:
    """Test recovery buttons are available while the coordinator is offline."""
    coordinator = FakeCoordinator()
    coordinator.last_update_success = False
    entry = config_entry()
    buttons = {
        entity.entity_description.key: entity
        for entity in (NiceBidiButton(coordinator, entry, description) for description in BUTTONS)
    }

    assert buttons["refresh_status"].available is True
    assert buttons["reconnect"].available is True
    assert buttons["partial_open_1"].available is False
    assert buttons["calibrate_positions"].available is False
