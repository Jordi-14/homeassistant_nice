"""Tests for advertised T4 action discovery and safety gating."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.coordinator import (
    NiceBidiDataUpdateCoordinator,
)
from custom_components.nice_bidiwifi.models.capabilities import NiceCapabilities
from custom_components.nice_bidiwifi.protocol.nhk.info import parse_info_xml
from custom_components.nice_bidiwifi.protocol.t4.actions import (
    T4_ACTION_BY_CODE,
    T4_ACTION_BY_KEY,
    T4_ACTIONS,
)
from custom_components.nice_bidiwifi.protocol.t4.allowed import (
    MAX_T4_ALLOWED_HEX_DIGITS,
    decode_t4_allowed,
)


def _capabilities(values: str | None, *, advertised: bool = True) -> NiceCapabilities:
    property_xml = (
        f'<T4_allowed type="hex" values="{values}" perm="r"/>'
        if advertised and values is not None
        else (
            '<T4_allowed type="hex" perm="r"/>'
            if advertised
            else ""
        )
    )
    return NiceCapabilities.from_device_info(
        parse_info_xml(
            f"""
            <Response>
              <Devices>
                <Device id="1">
                  <Properties>{property_xml}</Properties>
                </Device>
              </Devices>
            </Response>
            """
        )
    )


@pytest.mark.parametrize("action", T4_ACTIONS, ids=lambda action: action.key)
def test_each_defined_t4_bit_selects_only_its_catalog_action(action) -> None:
    """Every reviewed action maps to its protocol bit."""
    capabilities = _capabilities(f"{1 << action.code:X}")

    assert capabilities.supports_t4_action(action.code) is True
    assert capabilities.supported_t4_action_codes == frozenset({action.code})


@pytest.mark.parametrize("gap", (0x00, 0x08, 0x09, 0x0A))
def test_t4_mask_gaps_never_create_actions(gap: int) -> None:
    """Reserved and undefined bits do not produce command definitions."""
    capabilities = _capabilities(f"{1 << gap:X}")

    assert gap not in T4_ACTION_BY_CODE
    assert capabilities.supported_t4_action_codes == frozenset()


def test_zero_t4_mask_is_valid_and_supports_no_actions() -> None:
    """A zero mask is an explicit advertisement of no T4 actions."""
    capabilities = _capabilities("00000000")

    assert capabilities.t4_allowed_valid is True
    assert capabilities.t4_allowed_mask == 0
    assert capabilities.supported_t4_action_codes == frozenset()
    assert all(
        capabilities.supports_t4_action(action.code) is False
        for action in T4_ACTIONS
    )


def test_missing_t4_mask_remains_unknown() -> None:
    """Absent INFO data remains distinct from an invalid declaration."""
    capabilities = _capabilities(None, advertised=False)

    assert capabilities.t4_allowed.advertised is False
    assert capabilities.t4_allowed_valid is None
    assert capabilities.supported_t4_action_codes is None
    assert capabilities.supports_t4_action(1) is None


@pytest.mark.parametrize(
    "value",
    (
        "",
        "-1",
        "+1",
        "0x",
        "12,34",
        "not-hex",
        "1_000",
        "F" * (MAX_T4_ALLOWED_HEX_DIGITS + 1),
    ),
)
def test_malformed_t4_masks_are_advertised_but_invalid(value: str) -> None:
    """Malformed masks cannot become permissive action catalogs."""
    decoded = decode_t4_allowed(value)

    assert decoded.advertised is True
    assert decoded.valid is False
    assert decoded.mask is None
    assert all(decoded.supports(action.code) is False for action in T4_ACTIONS)


def test_large_t4_mask_is_bounded_to_reviewed_actions() -> None:
    """Large valid masks never synthesize commands for unknown high bits."""
    mask = (1 << 200) | (1 << T4_ACTION_BY_KEY["step_step"].code)
    capabilities = _capabilities(f"{mask:X}")

    assert capabilities.t4_allowed_valid is True
    assert capabilities.supported_t4_action_codes == frozenset({1})


def test_invalid_mask_blocks_compatibility_and_new_actions() -> None:
    """An invalid advertised mask cannot execute any T4 command."""
    coordinator = SimpleNamespace(capabilities=_capabilities("invalid"))

    assert (
        NiceBidiDataUpdateCoordinator.t4_action_supported(
            coordinator,
            "step_step",
        )
        is False
    )
    assert (
        NiceBidiDataUpdateCoordinator.t4_action_supported(
            coordinator,
            "open_and_block",
        )
        is False
    )


def test_missing_mask_keeps_only_compatibility_actions() -> None:
    """Legacy buttons survive absent masks while new actions stay undiscovered."""
    coordinator = SimpleNamespace(
        capabilities=_capabilities(None, advertised=False)
    )

    assert NiceBidiDataUpdateCoordinator.t4_action_supported(
        coordinator,
        "step_step",
    )
    assert not NiceBidiDataUpdateCoordinator.t4_action_supported(
        coordinator,
        "open_and_block",
    )


def test_dangerous_and_redundant_actions_default_disabled() -> None:
    """Risky and duplicate actions require deliberate registry enablement."""
    for action in T4_ACTIONS:
        if action.dangerous or action.redundant:
            assert action.enabled_by_default is False


def test_t4_catalog_keys_and_codes_are_unique() -> None:
    """Every reviewed action has one stable key and one protocol bit."""
    assert len(T4_ACTION_BY_KEY) == len(T4_ACTIONS)
    assert len(T4_ACTION_BY_CODE) == len(T4_ACTIONS)


async def test_invalid_mask_cannot_reach_command_execution() -> None:
    """Execution fails before touching a client for an invalid advertised mask."""
    coordinator = SimpleNamespace(capabilities=_capabilities("invalid"))
    coordinator.t4_action_supported = lambda action: (
        NiceBidiDataUpdateCoordinator.t4_action_supported(
            coordinator,
            action,
        )
    )

    with pytest.raises(HomeAssistantError, match="not advertised"):
        await NiceBidiDataUpdateCoordinator.async_send_dep_action(
            coordinator,
            "step_step",
        )
