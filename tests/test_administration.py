"""Tests for guarded shared Wi-Fi administration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from custom_components.nice_bidiwifi.models.administration import (
    NiceGroupSnapshot,
    NiceGroupSummary,
    NiceLogEvent,
    NiceLogSnapshot,
)
from custom_components.nice_bidiwifi.models.capabilities import NiceCapabilities
from tests.conftest import FakeClient, config_entry_data, make_device_info, make_status


def _coordinator(hass: HomeAssistant) -> NiceBidiDataUpdateCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-admin",
    )
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    coordinator._store_device_info(make_device_info())
    coordinator.async_set_updated_data(
        make_status(state="open", position=100.0, current_position=1000)
    )
    return coordinator


@pytest.mark.parametrize(
    "interface_product",
    ["BiDi-WiFi", "IT4-WiFi", "CU_WIFI"],
)
def test_known_shared_wifi_families_get_administration_capabilities(
    interface_product: str,
) -> None:
    capabilities = NiceCapabilities.from_device_info(
        replace(make_device_info(), interface_product=interface_product)
    )

    assert capabilities.logs is True
    assert capabilities.groups is True
    assert capabilities.interface_name_write is True
    assert capabilities.time_sync is True
    assert capabilities.reboot is True


def test_unknown_family_does_not_claim_administration_capabilities() -> None:
    capabilities = NiceCapabilities.from_device_info(
        replace(make_device_info(), interface_product="Unknown")
    )

    assert capabilities.logs is None
    assert capabilities.groups is None
    assert capabilities.interface_name_write is None
    assert capabilities.time_sync is None
    assert capabilities.reboot is None


async def test_read_only_snapshots_are_bounded_models_and_audited(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)
    coordinator.client.read_logs_result = NiceLogSnapshot(
        events=(
            NiceLogEvent(
                scope="device",
                event_type="DoorStatus",
                door_status="open",
            ),
        )
    )
    coordinator.client.read_groups_result = NiceGroupSnapshot(
        groups=(
            NiceGroupSummary(
                ordinal=1,
                interface_rule_count=1,
                device_count=1,
                device_rule_count=2,
            ),
        )
    )

    await coordinator.async_refresh_interface_logs()
    await coordinator.async_refresh_access_groups()

    assert coordinator.interface_log_snapshot.count == 1
    assert coordinator.interface_log_snapshot.retrieved_at is not None
    assert coordinator.access_group_snapshot.count == 1
    assert [operation.action for operation in coordinator.administration_history] == [
        "refresh_logs",
        "refresh_groups",
    ]
    assert all(
        operation.read_only for operation in coordinator.administration_history
    )
    assert coordinator.last_administration_operation.status == "completed"


async def test_name_write_is_verified_and_updates_cached_info(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)

    await coordinator.async_update_interface_name("Courtyard gate")

    assert coordinator.client.updated_interface_names == ["Courtyard gate"]
    assert coordinator.device_info.interface_name == "Courtyard gate"
    operation = coordinator.last_administration_operation
    assert operation.action == "update_name"
    assert operation.verification == "info_name_round_trip"
    assert operation.status == "completed"


async def test_name_write_reports_round_trip_mismatch(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)
    coordinator.client.update_interface_name = lambda name: None

    with pytest.raises(HomeAssistantError, match="verification failed"):
        await coordinator.async_update_interface_name("Courtyard gate")

    operation = coordinator.last_administration_operation
    assert operation.action == "update_name"
    assert operation.status == "failed"
    assert operation.error_type == "NiceBidiConnectionError"
    assert "Courtyard gate" not in repr(operation.as_diagnostics())


async def test_time_sync_round_trip_uses_home_assistant_timezone(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)

    await coordinator.async_sync_interface_time()

    clock = coordinator.client.updated_interface_clocks[0]
    assert datetime.strptime(clock.date, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    ) <= datetime.now(UTC)
    assert coordinator.device_info.interface_zone == clock.zone
    assert coordinator.device_info.interface_dst == clock.dst
    assert coordinator.last_administration_operation.verification == (
        "info_clock_round_trip"
    )


@pytest.mark.parametrize(
    ("method_name", "client_attribute"),
    [
        ("async_update_interface_name", "updated_interface_names"),
        ("async_sync_interface_time", "updated_interface_clocks"),
        ("async_reboot_interface", "interface_reboots"),
    ],
)
async def test_mutations_are_rejected_while_gate_is_moving(
    hass: HomeAssistant,
    method_name: str,
    client_attribute: str,
) -> None:
    coordinator = _coordinator(hass)
    coordinator.async_set_updated_data(make_status(state="opening"))
    method = getattr(coordinator, method_name)

    with pytest.raises(HomeAssistantError, match="blocked while the gate is moving"):
        if method_name == "async_update_interface_name":
            await method("Blocked name")
        else:
            await method()

    value = getattr(coordinator.client, client_attribute)
    if isinstance(value, int):
        assert value == 0
    else:
        assert value == []


async def test_reboot_is_sent_once_and_audited(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)

    await coordinator.async_reboot_interface()

    assert coordinator.client.interface_reboots == 1
    operation = coordinator.last_administration_operation
    assert operation.action == "reboot_interface"
    assert operation.verification == "change_acknowledgement"


async def test_unknown_family_rejects_administration(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator._store_device_info(
        replace(
            make_device_info(device_product="Unknown"),
            interface_product="Unknown",
        )
    )

    with pytest.raises(HomeAssistantError, match="not supported"):
        await coordinator.async_refresh_interface_logs()
