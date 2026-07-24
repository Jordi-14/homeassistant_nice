"""Compatibility contracts for entity construction."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.nice_bidiwifi.binary_sensor import BINARY_SENSORS
from custom_components.nice_bidiwifi.button import BUTTONS
from custom_components.nice_bidiwifi.coordinator import (
    NiceBidiDataUpdateCoordinator,
)
from custom_components.nice_bidiwifi.entities.catalog import (
    PROTECTED_ENTITY_CATALOG,
    ProtectedEntity,
)
from custom_components.nice_bidiwifi.entities.factory import (
    NiceCapabilityKey,
    NiceCoreEntityDescription,
    build_described_entities,
)
from custom_components.nice_bidiwifi.models.capabilities import NiceCapabilities
from custom_components.nice_bidiwifi.number import NUMBERS
from custom_components.nice_bidiwifi.protocol.nhk.info import parse_info_xml
from custom_components.nice_bidiwifi.sensor import SENSORS
from custom_components.nice_bidiwifi.switch import CONFIG_SWITCHES


def _protected(platform: str, description) -> ProtectedEntity:
    """Convert one current description into its frozen contract form."""
    return ProtectedEntity(
        platform=platform,
        key=description.key,
        unique_id_suffix=description.key,
        enabled_default=description.entity_registry_enabled_default,
        visible_default=description.entity_registry_visible_default,
    )


def test_protected_catalog_matches_all_current_descriptions() -> None:
    """The complete 91-definition catalog remains stable."""
    actual = (
        ProtectedEntity("cover", "cover", "cover", True, True),
        ProtectedEntity(
            "switch",
            "cover_switch",
            "cover_switch",
            True,
            True,
        ),
        *(
            _protected("button", description)
            for description in BUTTONS
            if description.protected
        ),
        *(
            _protected("switch", description)
            for description in CONFIG_SWITCHES
        ),
        *(_protected("number", description) for description in NUMBERS),
        *(_protected("sensor", description) for description in SENSORS),
        *(
            _protected("binary_sensor", description)
            for description in BINARY_SENSORS
        ),
    )

    assert actual == PROTECTED_ENTITY_CATALOG
    assert len(actual) == 91
    assert len(
        {(definition.platform, definition.key) for definition in actual}
    ) == 91


def test_protected_open_close_entities_survive_explicitly_unsupported_info() -> None:
    """HA-native cover and open/close switch remain in the factory output."""
    coordinator = SimpleNamespace(
        capabilities=SimpleNamespace(high_level_actions=False),
        data=None,
    )
    descriptions = (
        NiceCoreEntityDescription(
            key="cover",
            required_capability=NiceCapabilityKey.OPEN_CLOSE,
        ),
        NiceCoreEntityDescription(
            key="cover_switch",
            required_capability=NiceCapabilityKey.OPEN_CLOSE,
        ),
    )

    entities = build_described_entities(
        coordinator,
        SimpleNamespace(),
        descriptions,
        lambda _coordinator, _entry, description: description.key,
    )

    assert entities == ["cover", "cover_switch"]


def test_dynamic_t4_entities_follow_only_reviewed_advertised_bits() -> None:
    """New T4 entities are added from supported catalog bits only."""
    mask = (1 << 0x02) | (1 << 0x0B) | (1 << 0x08)
    capabilities = NiceCapabilities.from_device_info(
        parse_info_xml(
            f"""
            <Response>
              <Devices>
                <Device id="1">
                  <Properties>
                    <T4_allowed values="{mask:X}" type="hex" perm="r"/>
                  </Properties>
                </Device>
              </Devices>
            </Response>
            """
        )
    )
    coordinator = SimpleNamespace(
        capabilities=capabilities,
        data=SimpleNamespace(
            partial_open_2_position=None,
            partial_open_3_position=None,
        ),
    )
    coordinator.t4_action_supported = lambda action: (
        NiceBidiDataUpdateCoordinator.t4_action_supported(
            coordinator,
            action,
        )
    )

    created = build_described_entities(
        coordinator,
        SimpleNamespace(),
        BUTTONS,
        lambda _coordinator, _entry, description: description.key,
    )
    protected = {
        description.key
        for description in BUTTONS
        if description.protected
    }

    assert set(created) - protected == {
        "stop_remote",
        "apartment_step_step",
    }
