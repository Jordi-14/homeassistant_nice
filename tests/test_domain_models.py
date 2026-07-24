"""Tests for Home Assistant-independent domain models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from custom_components.nice_bidiwifi.errors import NiceAuthError
from custom_components.nice_bidiwifi.models.capabilities import (
    NiceCapabilities,
    ProductFamily,
)
from custom_components.nice_bidiwifi.models.commands import (
    CommandAcknowledgement,
    CommandKind,
    NiceCommand,
    NiceCommandResult,
)
from custom_components.nice_bidiwifi.models.config import (
    ConnectionMode,
    NiceEntryConfig,
)
from custom_components.nice_bidiwifi.models.credentials import NiceCredentials
from custom_components.nice_bidiwifi.models.position import (
    PositionConfidence,
    PositionSource,
    resolve_position,
)
from custom_components.nice_bidiwifi.models.status import NiceStatus
from custom_components.nice_bidiwifi.protocol.nhk.info import parse_info_xml
from custom_components.nice_bidiwifi.protocol.t4.dmp import decode_dmp_response


def _config(**overrides):
    data = {
        "name": "Gate",
        "host": "192.0.2.10",
        "port": 443,
        "username": "user",
        "password": "AA" * 32,
        "source_id": "source",
        "target_mac": "AA:BB:CC:DD:EE:FF",
        "device_id": 1,
        "t4_timeout_ms": 200,
    }
    data.update(overrides)
    return data


def test_credentials_validate_and_decode_password() -> None:
    """Credentials decode only validated 64-character hexadecimal secrets."""
    credentials = NiceCredentials(
        username="user",
        password_hex="AA" * 32,
        target_mac="AA:BB:CC:DD:EE:FF",
    )

    assert credentials.source == "user"
    assert credentials.password == bytes.fromhex("AA" * 32)
    with pytest.raises(NiceAuthError):
        _ = NiceCredentials("user", "not-hex", "target").password


def test_entry_config_defaults_existing_entries_to_local_only() -> None:
    """Legacy config dictionaries normalize to a typed local-only policy."""
    config = NiceEntryConfig.from_mapping(_config())

    assert config.connection.mode is ConnectionMode.LOCAL_ONLY
    assert config.connection.local.host == "192.0.2.10"
    assert config.connection.relay is None
    assert config.device_id == 1


def test_entry_config_validates_future_route_requirements() -> None:
    """Fallback and cloud-only policies cannot exist without relay endpoints."""
    with pytest.raises(ValueError, match="relay"):
        NiceEntryConfig.from_mapping(
            _config(connection_mode="local_with_cloud_fallback")
        )

    cloud = NiceEntryConfig.from_mapping(
        _config(
            connection_mode="cloud_only",
            relay_host="relay.example",
            relay_port=443,
        )
    )
    assert cloud.connection.mode is ConnectionMode.CLOUD_ONLY
    assert cloud.connection.relay.host == "relay.example"


def test_info_capabilities_decode_permissions_family_and_t4_mask() -> None:
    """Advertised INFO data becomes one normalized capability model."""
    info = parse_info_xml(
        """
        <Response>
          <Interface>
            <Prod>CU_WIFI</Prod>
            <VersionFW>2.0</VersionFW>
            <Services>
              <T4_allowed type="hex" values="00000062" perm="r"/>
            </Services>
          </Interface>
          <Devices>
            <Device id="1">
              <Services>
                <DoorAction type="string" perm="w"/>
              </Services>
              <Properties>
                <DoorStatus type="string" perm="r"/>
                <Obstruct type="bool" perm="r"/>
              </Properties>
            </Device>
          </Devices>
        </Response>
        """
    )

    capabilities = NiceCapabilities.from_device_info(info)

    assert capabilities.family is ProductFamily.CU_WIFI
    assert capabilities.high_level_actions is True
    assert capabilities.readable_status is True
    assert capabilities.obstruction is True
    assert capabilities.supported_t4_action_codes == frozenset({1, 5, 6})
    assert capabilities.supports_t4_action(5) is True
    assert capabilities.supports_t4_action(7) is False


def test_missing_info_capabilities_remain_unknown() -> None:
    """Missing INFO declarations do not become false unsupported claims."""
    capabilities = NiceCapabilities.from_device_info(
        parse_info_xml("<Response><Devices><Device id='1'/></Devices></Response>")
    )

    assert capabilities.high_level_actions is None
    assert capabilities.readable_status is None
    assert capabilities.supports_t4_action(1) is None


def test_status_and_command_models_are_immutable() -> None:
    """Normalized status and command results cannot be mutated in place."""
    status = NiceStatus(
        state="open",
        position=100.0,
        current_position=None,
        closed_position=None,
        open_position=None,
        registers={"NHK/DoorStatus": "open"},
    )
    result = NiceCommandResult(
        command=NiceCommand("open", CommandKind.DOOR_ACTION),
        acknowledgement=CommandAcknowledgement.ACCEPTED,
        latency_ms=12,
    )

    with pytest.raises(TypeError):
        status.registers["NHK/DoorStatus"] = "closed"
    with pytest.raises(FrozenInstanceError):
        result.latency_ms = 99
    assert result.accepted


def test_position_resolution_carries_source_and_confidence() -> None:
    """Position selection returns explicit provenance and confidence."""
    measured = resolve_position(
        NiceStatus(
            state="opening",
            position=42.0,
            current_position=420,
            closed_position=0,
            open_position=1000,
            registers={"04/11": "01 a4"},
        ),
        simulated=None,
        last_known=None,
    )
    estimated = resolve_position(
        None,
        simulated=43.5,
        last_known=40.0,
    )

    assert measured.source is PositionSource.DMP_ENCODER
    assert measured.confidence is PositionConfidence.MEASURED
    assert estimated.source is PositionSource.TIME_SIMULATION
    assert estimated.confidence is PositionConfidence.ESTIMATED


def test_dmp_decoder_returns_typed_immutable_result() -> None:
    """DMP decoding carries numeric identity and raw value without dictionaries."""
    decoded = decode_dmp_response(
        bytes.fromhex(
            "55 0D 00 03 50 91 08 08 00 04 11 19 02 02 01 A4 0D"
        )
    )

    assert decoded.group == 0x04
    assert decoded.parameter == 0x11
    assert decoded.value == b"\x01\xa4"
    assert decoded.as_dict()["value_uint_be"] == 420
    with pytest.raises(FrozenInstanceError):
        decoded.group = 0x05
