"""Tests for Nice button entities."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.button import (
    BUTTONS,
    NiceBidiButton,
    async_setup_entry,
)
from custom_components.nice_bidiwifi.client import DEP_ACTION_PARTIAL_OPEN_1
from tests.conftest import FakeCoordinator, config_entry, make_status


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

    assert [
        value for call, value in coordinator.calls if call == "dep_action"
    ] == [
        description.t4_action_key
        for description in BUTTONS
        if description.t4_action_key is not None
    ]
    assert ("refresh", None) in coordinator.calls
    assert ("reconnect", None) in coordinator.calls
    assert ("calibrate", None) in coordinator.calls


def test_button_unique_ids() -> None:
    """Test button unique IDs."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    buttons = [NiceBidiButton(coordinator, entry, description) for description in BUTTONS]

    assert [entity.unique_id for entity in buttons] == [
        f"aabbccddeeff_1_{description.key}"
        for description in BUTTONS
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


def test_optional_partial_open_buttons_require_known_configuration() -> None:
    """Test optional partial-open slots are hidden when their registers are absent."""
    coordinator = FakeCoordinator()
    coordinator.data = make_status(
        partial_open_2_position=None,
        partial_open_3_position=None,
    )
    entry = config_entry()
    buttons = {
        entity.entity_description.key: entity
        for entity in (NiceBidiButton(coordinator, entry, description) for description in BUTTONS)
    }

    assert buttons["partial_open_1"].available is True
    assert buttons["partial_open_2"].available is False
    assert buttons["partial_open_3"].available is False


async def test_t4_buttons_recheck_advertised_support_before_execution() -> None:
    """A stale or unsupported T4 button cannot execute a command."""
    coordinator = FakeCoordinator()
    coordinator.supported_t4_actions = {DEP_ACTION_PARTIAL_OPEN_1}
    entry = config_entry()
    buttons = {
        description.key: NiceBidiButton(coordinator, entry, description)
        for description in BUTTONS
    }

    assert buttons[DEP_ACTION_PARTIAL_OPEN_1].available is True
    assert buttons["open_and_block"].available is False
    with pytest.raises(HomeAssistantError, match="not advertised"):
        await buttons["open_and_block"].async_press()
