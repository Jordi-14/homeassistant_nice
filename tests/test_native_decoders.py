"""Independent synthetic vectors for contracts recovered in the native audit."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from custom_components.nice_bidiwifi.entities.diagnostics import diagnostic_entities
from custom_components.nice_bidiwifi.errors import (
    NiceParameterError,
    NiceReadPreemptedError,
)
from custom_components.nice_bidiwifi.protocol.t4.automation import (
    decode_automation_state,
)
from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo
from custom_components.nice_bidiwifi.protocol.t4.maneuvers import maneuver_evidence
from custom_components.nice_bidiwifi.protocol.t4.menu import (
    MenuWireDecoder,
    decode_menu_records,
)
from tests.conftest import FakeClient, make_status
from tests.test_coordinator import _coordinator


@pytest.mark.parametrize("raw", [None, b"", b"\xff", b"\xff" * 8, bytes(9), bytes(257)])
def test_unknown_or_unreviewed_automation_layout(raw):
    assert decode_automation_state(raw) is None


def test_legacy_state_does_not_fabricate_extended_fields():
    assert decode_automation_state(b"\x02") == {"status_code": 2, "reported_status": "opening_in_progress"}
    values = decode_automation_state(bytes.fromhex("fc ff fd fe"))
    assert values["reported_status"] is values["reported_diagnostic"] is values["reported_advanced_diagnostic"] is None
    assert values["status_code"] == 252
    assert values["diagnostic_code"] == 253
    assert values["advanced_diagnostic_code"] == 254
    assert values["flags_raw"] == 255
    assert "search_devices" not in values


def test_status_codes_flags_and_byte_masks_are_distinct():
    # Native emits 0|2|y|n|y|n|y|n|y|n|12|1|2|128|99 for this vector.
    values = decode_automation_state(bytes.fromhex("00 55 02 0c 01 02 80 63"))
    assert values["status_code"] == 0
    assert values["diagnostic_code"] == 2
    assert values["advanced_diagnostic_code"] == 12
    assert values["search_devices"] is True
    assert values["search_positions"] is False
    assert values["first_maneuvers"] is True
    assert values["no_programming"] is False
    assert values["no_eeprom_errors"] is True
    assert values["psu_detected"] is True  # Java's inverse n comparison
    assert values["built_in_radio"] is False
    assert values["modular_controller"] is True
    assert values["installed_modules_mask"] == 1
    assert values["modules_error_mask"] == 2
    assert values["modules_boot_mask"] == 128
    assert values["uninterpreted_tail_byte"] == 99
    assert "position" not in values


@pytest.mark.parametrize("length", range(1, 9))
def test_only_present_optional_bytes_are_decoded(length):
    values = decode_automation_state(bytes.fromhex("02 18 03 0c 01 02 04 08")[:length])
    for minimum, field in [(2, "flags_raw"), (3, "diagnostic_code"), (4, "advanced_diagnostic_code"), (5, "installed_modules_mask"), (6, "modules_error_mask"), (7, "modules_boot_mask"), (8, "uninterpreted_tail_byte")]:
        assert (field in values) == (length >= minimum)


def test_zero_flags_preserve_java_inverse_semantics():
    values = decode_automation_state(bytes.fromhex("01 00"))
    assert values["search_devices"] is False
    assert values["no_programming"] is False
    assert values["psu_detected"] is values["built_in_radio"] is values["modular_controller"] is True


@pytest.mark.parametrize("flag,field", [(0x80, "user_set"), (0x40, "user_get"), (0x20, "installer_set"), (0x10, "installer_get"), (8, "is_group")])
def test_menu_permission_columns_are_not_interchanged(flag, field):
    row, = decode_menu_records(bytes([flag | 3, 0x45]), group=4)
    assert row.binding == (4, 0x45)
    assert row.indentation == 3
    for name in ["user_set", "user_get", "installer_set", "installer_get", "is_group"]:
        assert getattr(row, name) == (name == field)
    assert not hasattr(row, "can_set")
    assert not hasattr(row, "app_id")


def test_menu_bounds_and_role_boundary():
    assert decode_menu_records(b"", group=0) == ()
    rows = decode_menu_records(bytes.fromhex("ff 00 00 ff"), group=255)
    assert rows[0].binding == (255, 0)
    assert rows[1].binding == (255, 255)
    for raw, group in [(b"\x80", 4), (bytes(1026), 4), (b"", -1), (b"", 256), (b"", True)]:
        with pytest.raises(NiceParameterError):
            decode_menu_records(raw, group=group)
    # The phase-2 decoder is usable, but raw records cannot grant a role.
    record, = MenuWireDecoder().decode(bytes.fromhex("f0 45"))
    assert record.binding == (4, 0x45)
    assert not hasattr(record, "role")
    assert not hasattr(record, "can_set")


def test_native_history_order_does_not_establish_freshness():
    assert maneuver_evidence(bytes(7))["state"] == "unavailable"
    report = maneuver_evidence(bytes.fromhex("00 01 02 03 04 05 0a fe"))
    assert [row["code"] for row in report["slots"]] == [0, 1, 2, 3, 4, 5, 10, 254]
    assert report["confirmed_success_count"] is None
    assert report["slots"][7]["reported_outcome"] == "undefined"
    assert report["physical_cause"] == "unknown"
    assert maneuver_evidence(bytes(8))["reported_failure_count"] is None


async def owner(hass):
    instance = _coordinator(hass)
    instance.client = FakeClient()
    instance.async_set_updated_data(make_status(state="closed", position=0))
    return instance


async def test_optional_automation_read_targets_status_and_changes_no_gate_state(hass):
    instance = await owner(hass)
    instance.client.dmp_values[(4, 1)] = bytes.fromhex("09 12 03 0c")
    instance.client.read_dmp_info = Mock(side_effect=AssertionError("No INFO probe needed for fixed status layout"))
    screens = diagnostic_entities(instance, instance.config_entry)
    sensor = next(e for e in screens if e.definition.key == "automation_state")
    assert sensor.entity_registry_enabled_default is False
    assert instance.client.value_reads == []
    before = instance.data
    await instance.diagnostic_runtime.async_read("automation_state")
    view = instance.diagnostic_runtime.view("automation_state")
    assert instance.client.value_reads == [(4, 1)]
    assert view["values"]["reported_status"] == "searching_positions"
    assert view["affects_control_or_permissions"] is False
    assert view["physical_cause"] == "unknown"
    assert instance.data is before
    assert instance.client.dmp_writes == []
    assert instance.diagnostic_runtime.definition("other").register == (4, 0xD3)
    assert instance.diagnostic_runtime.definition("visual").register == (4, 0xD5)
    await instance.async_shutdown()


async def test_diagnostic_read_yields_to_motion_and_blocks_indexed_layout(hass):
    instance = await owner(hass)
    instance.async_set_updated_data(make_status(state="opening"))
    with pytest.raises(NiceReadPreemptedError):
        await instance.diagnostic_runtime.async_read("automation_state")
    assert instance.client.value_reads == []
    instance.async_set_updated_data(make_status(state="closed"))
    address = instance.parameters.address(4, 1)
    instance.parameters.record_info(address, DmpInfo(1, 0, 0, 0x81, array_size=2))
    await instance.diagnostic_runtime.async_read("automation_state")
    assert instance.diagnostic_runtime.view("automation_state")["state"] == "blocked"
    assert instance.client.value_reads == []
    await instance.async_shutdown()


async def test_current_poll_updates_readonly_evidence_with_its_source(hass):
    instance = await owner(hass)
    raw = "02 ff 03 00"
    status = replace(make_status(state="opening"), registers={"04/01": raw}, fresh_registers=frozenset({"04/01"}))
    instance.diagnostic_runtime.record_poll(status)
    view = instance.diagnostic_runtime.view("automation_state")
    assert view["source"] == "poll"
    assert view["values"]["status_code"] == 2
    assert view["flag_validity"] == "unverified_all_ones"
    assert "first_maneuvers" not in view["values"]
    assert instance.data.state == "closed"
    # Cached registers in a partial poll are not a new observation.
    instance.diagnostic_runtime.record_poll(replace(status, fresh_registers=frozenset()))
    assert instance.diagnostic_runtime.view("automation_state")["received_at"] == view["received_at"]
    instance.diagnostic_runtime.invalidate()
    assert instance.diagnostic_runtime.view("automation_state")["state"] == "unavailable"
    assert instance.client.value_reads == []
    await instance.async_shutdown()
