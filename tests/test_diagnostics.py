"""Diagnostics tests for Nice."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.nice_bidiwifi.const import (
    CONF_CONNECTION_MODE,
    CONF_LEGACY_LOCAL_TLS,
    CONF_SOURCE_ID,
    CONF_TARGET_MAC,
)
from custom_components.nice_bidiwifi.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.nice_bidiwifi.models.administration import (
    NiceAdministrationOperation,
    NiceGroupSnapshot,
    NiceGroupSummary,
    NiceLogEvent,
    NiceLogSnapshot,
)
from tests.conftest import FakeCoordinator, config_entry


async def test_diagnostics_redacts_sensitive_data(hass) -> None:
    """Test diagnostics redacts credentials and local identifiers."""
    coordinator = FakeCoordinator()
    coordinator.interface_log_snapshot = NiceLogSnapshot(
        events=(
            NiceLogEvent(
                scope="device",
                event_type="DoorStatus",
                time="2026-07-24T12:00:00Z",
                door_status="open",
            ),
        ),
        retrieved_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
    )
    coordinator.access_group_snapshot = NiceGroupSnapshot(
        groups=(
            NiceGroupSummary(
                ordinal=1,
                interface_rule_count=1,
                device_count=2,
                device_rule_count=3,
            ),
        ),
        retrieved_at=datetime(2026, 7, 24, 12, 1, tzinfo=UTC),
    )
    coordinator.administration_history = deque(
        (
            NiceAdministrationOperation(
                action="refresh_logs",
                status="completed",
                read_only=True,
                route="local",
                latency_ms=42,
                completed_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
                verification="not_required",
            ),
        ),
        maxlen=16,
    )
    entry = config_entry()
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"][CONF_HOST] == "**REDACTED**"
    assert diagnostics["entry"][CONF_USERNAME] == "**REDACTED**"
    assert diagnostics["entry"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["entry"][CONF_TARGET_MAC] == "**REDACTED**"
    assert diagnostics["entry"][CONF_SOURCE_ID] == "**REDACTED**"
    assert diagnostics["connection"]["active_route"] == "local"
    assert diagnostics["connection"]["local_state"] == "connected"
    assert diagnostics["connection"]["cloud_state"] == "not_configured"
    assert diagnostics["connection"]["relay_tls"] == {
        "configured": False,
        "encrypted": False,
        "certificate_verification": None,
        "hostname_verification": None,
        "compatibility_reason": None,
    }
    assert diagnostics["connection"]["local_tls"] == {
        "configured": True,
        "legacy_compatibility_enabled": False,
        "legacy_compatibility_active": False,
        "cipher_security_level": None,
    }
    assert diagnostics["device_info"]["interface_serial"] == "**REDACTED**"
    assert diagnostics["device_info"]["device_serial"] == "**REDACTED**"
    assert diagnostics["status"]["state"] == "opening"
    assert diagnostics["status"]["position"] == 42.4
    assert diagnostics["status"]["current_position"] == 424
    assert diagnostics["status"]["state_source"] == "dmp_04_01"
    assert diagnostics["status"]["position_source"] == "dmp_encoder"
    assert diagnostics["status"]["position_confidence"] == "measured"
    assert diagnostics["status"]["position_reporting_observed"] is True
    assert diagnostics["status"]["is_moving"] is True
    assert diagnostics["status"]["bus_t4"]["opening_speed"] == 60
    assert diagnostics["status"]["bus_t4"]["maintenance_count"] == 12
    assert diagnostics["status"]["bus_t4"]["limit_open"] is True
    assert diagnostics["status"]["bus_t4"]["obstacle"] is True
    assert diagnostics["status"]["bus_t4"]["oxi_product"] == "OXI"
    assert diagnostics["calibration"]["cancel_reason"] is None
    assert diagnostics["device_info"]["interface_name_present"] is True
    assert "interface_name" not in diagnostics["device_info"]
    assert diagnostics["administration"]["mutation_blocked_while_moving"] is True
    assert diagnostics["administration"]["writes_replayed_after_ambiguous_failure"] is False
    assert diagnostics["administration"]["logs"]["retained_count"] == 1
    assert diagnostics["administration"]["groups"]["summaries"] == [
        {
            "ordinal": 1,
            "interface_rule_count": 1,
            "device_count": 2,
            "device_rule_count": 3,
        }
    ]
    assert diagnostics["administration"]["operation_history"][0]["latency_ms"] == 42
    assert "dmp_registers" not in diagnostics["status"]


async def test_diagnostics_reports_unverified_relay_tls(hass) -> None:
    """Cloud diagnostics must make the relay trust limitation explicit."""
    coordinator = FakeCoordinator()
    coordinator.cloud_connection_state = "connected"
    entry = config_entry(
        **{CONF_CONNECTION_MODE: "local_with_cloud_fallback"},
    )
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["connection"]["relay_tls"] == {
        "configured": True,
        "encrypted": True,
        "certificate_verification": False,
        "hostname_verification": False,
        "compatibility_reason": "nice_relay_unverifiable_certificate",
    }


async def test_diagnostics_reports_legacy_local_tls_opt_in(hass) -> None:
    """Diagnostics make the local TLS compatibility choice explicit."""
    coordinator = FakeCoordinator()
    coordinator.client.local_tls_legacy_active = True
    coordinator.client.local_tls_cipher_security_level = 0
    entry = config_entry(**{CONF_LEGACY_LOCAL_TLS: True})
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"][CONF_LEGACY_LOCAL_TLS] is True
    assert diagnostics["connection"]["local_tls"] == {
        "configured": True,
        "legacy_compatibility_enabled": True,
        "legacy_compatibility_active": True,
        "cipher_security_level": 0,
    }
