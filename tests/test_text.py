"""Tests for shared Wi-Fi text entities."""

from __future__ import annotations

from custom_components.nice_bidiwifi.text import (
    TEXTS,
    NiceBidiText,
    async_setup_entry,
)
from tests.conftest import FakeCoordinator, config_entry, make_status


async def test_text_setup_is_additive_and_disabled_by_default() -> None:
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    await async_setup_entry(None, entry, lambda entities: created.extend(entities))

    assert len(created) == len(TEXTS) == 1
    assert created[0].entity_description.protected is False
    assert created[0].entity_description.entity_registry_enabled_default is False
    assert created[0].entity_description.entity_registry_visible_default is False


async def test_interface_name_round_trip_delegates_to_coordinator() -> None:
    coordinator = FakeCoordinator()
    entity = NiceBidiText(coordinator, config_entry(), TEXTS[0])

    assert entity.native_value == "Parking interface"
    await entity.async_set_value("Courtyard gate")

    assert ("interface_name", "Courtyard gate") in coordinator.calls


def test_interface_name_is_unavailable_while_moving() -> None:
    coordinator = FakeCoordinator()
    coordinator.data = make_status(state="closing")
    entity = NiceBidiText(coordinator, config_entry(), TEXTS[0])

    assert entity.available is False
