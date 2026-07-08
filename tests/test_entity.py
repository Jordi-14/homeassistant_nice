"""Tests for shared Nice entity helpers."""

from __future__ import annotations

from homeassistant.components import button as button_component
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.button import BUTTONS, NiceBidiButton
from custom_components.nice_bidiwifi.const import CONF_TARGET_MAC, DOMAIN
from custom_components.nice_bidiwifi.entity import (
    bidi_entity_name,
)
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


def test_button_uses_full_name_as_entity_id_suggestion() -> None:
    """Test entities stay registry-managed while avoiding HA's area prefix."""
    coordinator = FakeCoordinator()
    entry = config_entry(**{CONF_NAME: "p0_garage_door_exterior"})
    description = next(description for description in BUTTONS if description.key == "courtesy_light")

    entity = NiceBidiButton(coordinator, entry, description)

    assert entity.has_entity_name is False
    assert entity.name == "p0_garage_door_exterior Courtesy light"
    assert entity.entity_id == "button.p0_garage_door_exterior_courtesy_light"
    assert entity.unique_id == "aabbccddeeff_1_courtesy_light"


async def test_button_registry_entity_id_ignores_device_area_prefix(
    hass: HomeAssistant,
) -> None:
    """Test HA registry IDs use the configured gate name, not area + device name."""
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
    assert (
        entity_registry.async_get_entity_id(
            button_component.DOMAIN,
            DOMAIN,
            "aabbccddeeff_1_partial_open_3",
        )
        == "button.p0_garage_door_exterior_partial_open_3"
    )
