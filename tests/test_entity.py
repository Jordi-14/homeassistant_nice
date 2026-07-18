"""Tests for shared Nice entity helpers."""

from __future__ import annotations

from homeassistant.components import button as button_component
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi import _async_migrate_default_entity_ids
from custom_components.nice_bidiwifi.button import BUTTONS, NiceBidiButton
from custom_components.nice_bidiwifi.const import CONF_TARGET_MAC, DOMAIN
from custom_components.nice_bidiwifi.cover import NiceBidiCover
from custom_components.nice_bidiwifi.entity import (
    bidi_entity_name,
)
from custom_components.nice_bidiwifi.switch import NiceBidiCoverSwitch
from tests.conftest import FakeCoordinator, config_entry, config_entry_data


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


def test_button_uses_short_entity_name_with_full_entity_id_suggestion() -> None:
    """Test child entity names are short while entity ID suggestions stay stable."""
    coordinator = FakeCoordinator()
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})
    description = next(description for description in BUTTONS if description.key == "courtesy_light")

    entity = NiceBidiButton(coordinator, entry, description)

    assert entity.has_entity_name is True
    assert entity.name == "Courtesy light"
    assert entity.entity_id == "button.p0_garage_door_exterior_courtesy_light"
    assert entity.unique_id == "aabbccddeeff_1_courtesy_light"


def test_primary_cover_uses_device_name_with_full_entity_id_suggestion() -> None:
    """Test the main cover remains the primary device entity."""
    coordinator = FakeCoordinator()
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    entity = NiceBidiCover(coordinator, entry)

    assert entity.has_entity_name is True
    assert entity.name is None
    assert entity.entity_id == "cover.p0_garage_door_exterior"


def test_cover_switch_uses_short_entity_name_with_full_entity_id_suggestion() -> None:
    """Test the auxiliary cover switch does not repeat the device name."""
    coordinator = FakeCoordinator()
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})

    entity = NiceBidiCoverSwitch(coordinator, entry)

    assert entity.has_entity_name is True
    assert entity.name == "Open/close switch"
    assert entity.entity_id == "switch.p0_garage_door_exterior"


async def test_button_registry_entity_id_ignores_device_area_prefix(
    hass: HomeAssistant,
) -> None:
    """Test HA registry IDs stay full while entity names are device-relative."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(**{CONF_NAME: "p0_garage_door_exterior"}),
        entry_id="entry-1",
        title="Nice gate",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = FakeCoordinator()

    area = ar.async_get(hass).async_create("Parquing")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data[CONF_TARGET_MAC])},
        name="p0_garage_door_exterior",
    )
    device_registry.async_update_device(device.id, area_id=area.id)

    assert await button_component.async_setup(hass, {})
    assert await button_component.async_setup_entry(hass, entry)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        button_component.DOMAIN,
        DOMAIN,
        "aabbccddeeff_1_partial_open_3",
    )
    assert entity_id == "button.p0_garage_door_exterior_partial_open_3"

    registry_entry = entity_registry.async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.has_entity_name is True
    assert registry_entry.original_name == "Partial open 3"
    assert er.async_get_unprefixed_name(hass, registry_entry) == "Partial open 3"


async def test_default_entity_id_migration_uses_configured_name(
    hass: HomeAssistant,
) -> None:
    """Test stale default-name registry IDs are migrated to the configured gate name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(**{CONF_NAME: "p0_garage_door_exterior"}),
        entry_id="entry-1",
        title="p0_garage_door_exterior",
    )
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "cover",
        DOMAIN,
        "aabbccddeeff_1_cover",
        config_entry=entry,
        suggested_object_id="nice_gate",
    )
    entity_registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "aabbccddeeff_1_always_close",
        config_entry=entry,
        suggested_object_id="nice_gate_always_close",
    )

    _async_migrate_default_entity_ids(hass, entry)

    assert entity_registry.async_get("cover.nice_gate") is None
    assert entity_registry.async_get("binary_sensor.nice_gate_always_close") is None
    assert entity_registry.async_get("cover.p0_garage_door_exterior") is not None
    assert entity_registry.async_get("binary_sensor.p0_garage_door_exterior_always_close") is not None


async def test_default_entity_id_migration_skips_collisions(
    hass: HomeAssistant,
) -> None:
    """Test the migration leaves stale IDs alone when the desired ID is taken."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(**{CONF_NAME: "p0_garage_door_exterior"}),
        entry_id="entry-1",
        title="p0_garage_door_exterior",
    )
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "aabbccddeeff_1_always_close",
        config_entry=entry,
        suggested_object_id="nice_gate_always_close",
    )
    entity_registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "collision",
        config_entry=entry,
        suggested_object_id="p0_garage_door_exterior_always_close",
    )

    _async_migrate_default_entity_ids(hass, entry)

    assert entity_registry.async_get("binary_sensor.nice_gate_always_close") is not None
    assert entity_registry.async_get("binary_sensor.p0_garage_door_exterior_always_close") is not None
