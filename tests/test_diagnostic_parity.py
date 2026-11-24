"""Source-backed diagnostic parity, uncertainty, lifecycle and redaction regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from custom_components.nice_bidiwifi.diagnostic_runtime import DIAGNOSTICS
from custom_components.nice_bidiwifi.errors import (
    NiceBidiConnectionError,
    NiceParameterError,
)
from custom_components.nice_bidiwifi.incidents import (
    MAX_TIMELINE_RECORDS,
    IncidentTimeline,
)
from custom_components.nice_bidiwifi.models.commands import (
    CommandAcknowledgement,
    CommandKind,
    NiceCommand,
    NiceCommandResult,
)
from custom_components.nice_bidiwifi.models.events import (
    NiceEvent,
    NiceEventCategory,
    NiceEventKind,
)
from custom_components.nice_bidiwifi.protocol.nhk.events import parse_nhk_event_frame
from custom_components.nice_bidiwifi.protocol.t4.history import decode_history_row
from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo, decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.live import (
    parse_cuwifi_live_status_payload,
)
from custom_components.nice_bidiwifi.protocol.t4.maneuvers import maneuver_evidence
from custom_components.nice_bidiwifi.redaction import redact_evidence_tree
from tests.conftest import FakeClient, make_status
from tests.test_coordinator import _coordinator
from tests.test_events import _t4_event
from tests.test_io_diagnostics import D2_INFO


def plain(message):
    inner = bytes.fromhex(message)
    body = (
        bytes.fromhex("00 ff 00 03 01") + bytes([len(inner) + 1, 0]) + inner + b"\x00"
    )
    return bytes([0x55, len(body)]) + body + bytes([len(body)])


def event(message="04 02 88 19 00 00", timestamp=True):
    return parse_nhk_event_frame(
        _t4_event(
            message,
            xml="<Timestamp>2026-11-24T12:00:00</Timestamp>" if timestamp else "",
        )
    )[0]


async def instance(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state="closed"))
    return owner


@pytest.mark.parametrize(
    "message",
    [
        "04 02 88 19 00 00",
        "04 02 91 00 00 00 23 03 0c",
        "04 02 05",
        "04 82 01",
        "04 82 02",
        "04 82 ff",
        "04 c1 03",
        "04 81 01",
        "04 01 00",
        "04 08 00",
        "04 10 00",
        "04 11 00",
        "04 83 00",
        "04 d1 00",
    ],
)
def test_same_live_and_history_payload_has_same_interpretation(message):
    live = parse_cuwifi_live_status_payload(plain(message))
    stored = decode_history_row(1, "2026-11-24T12:00:00", "DEP", plain(message))
    assert live.event_class == stored.event_class
    assert live.change_state == stored.change_state
    assert live.cause == stored.cause
    assert live.command == stored.command
    assert live.basic_diagnostic == stored.basic_diagnostic
    assert live.advanced_diagnostic == stored.advanced_diagnostic
    assert live.obstruction == stored.obstruction
    assert dict(live.event_details or ()) == dict(stored.details)
    assert stored.actor == stored.physical_cause == "unknown"


@pytest.mark.parametrize(
    "message",
    [
        "0a 02 01 23 45 67 89 ab",
        "04 fb de ad be ef",
        "04 fe 01 23 45 67",
        "00 13 01 23 45 67",
    ],
)
def test_receiver_password_and_unknown_payloads_are_not_history_exports(message):
    assert decode_history_row(1, None, "DEP", plain(message)) is None


def test_multiple_payloads_retain_independent_causes_in_timeline():
    parsed = parse_nhk_event_frame(
        _t4_event(
            "04 02 91 00 00 00 23 03 0c",
            "04 02 88 19 00 00",
            xml="<Timestamp>2026-11-24T12:00:00</Timestamp>",
        )
    )[0]
    assert len(parsed.t4_evidence) == 2
    timeline = IncidentTimeline({})
    timeline.event(parsed)
    events = [r["values"] for r in timeline.export()["records"] if r["kind"] == "event"]
    assert events[0]["change_state"] == "force_limiter_intervent"
    assert events[0]["cause"] == "none"
    assert events[1]["change_state"] == "stopped_for_command_arrival"
    assert events[1]["cause"] == "oview"
    assert [
        r["values"]["payload_index"]
        for r in timeline.export()["records"]
        if r["kind"] == "event"
    ] == [0, 1]


def test_command_arrival_and_static_limiter_do_not_establish_external_actor_or_trip():
    now = [10.0]
    timeline = IncidentTimeline({}, clock=lambda: now[0])
    command = NiceCommand("close", CommandKind.DOOR_ACTION)
    receipt = NiceCommandResult(command, CommandAcknowledgement.ACCEPTED, 10)
    timeline.command(command, receipt)
    timeline.event(event())
    timeline.add(
        "load", "motion_trace", {"force_limiter_state": "level_2", "torque_m1": 30}
    )
    incidents = [
        r["values"] for r in timeline.export()["records"] if r["kind"] == "incident"
    ]
    assert len(incidents) == 1
    assert incidents[0]["reason"] == "command_arrival"
    assert incidents[0]["actor"] == incidents[0]["physical_cause"] == "unknown"
    assert incidents[0]["relationship"] == "temporal_only"
    assert incidents[0]["recent_ha_command"] == "close"


def test_retained_obstruction_and_session_baselines_are_not_new_interventions():
    timeline = IncidentTimeline({})
    base = NiceEvent(
        NiceEventKind.CHANGE,
        NiceEventCategory.STATE_CHANGE,
        datetime.now(UTC),
        obstruction=True,
    )
    timeline.event(base)
    timeline.event(base)
    assert not [r for r in timeline.records if r["kind"] == "incident"]
    timeline.event(replace(base, obstruction=False))
    timeline.event(base)
    assert len([r for r in timeline.records if r["kind"] == "incident"]) == 1
    timeline.new_session()
    timeline.event(base)
    assert len([r for r in timeline.records if r["kind"] == "incident"]) == 1


def test_wire_deduplication_is_consecutive_scoped_and_bounded_in_time():
    now = [10.0]
    timeline = IncidentTimeline({}, clock=lambda: now[0])
    value = event()
    assert not timeline.duplicate(value)
    assert timeline.duplicate(value)
    assert timeline.duplicates == 1
    now[0] += 3
    assert not timeline.duplicate(value)
    assert not timeline.duplicate(event(timestamp=False))
    timeline.new_session()
    assert not timeline.duplicate(value)


def test_timeline_bounds_secrets_and_nonfinite_values():
    timeline = IncidentTimeline(
        {"password": "secret-value", "username": "private-user"}
    )
    for _ in range(MAX_TIMELINE_RECORDS + 100):
        timeline.add(
            "event",
            "nhk",
            {
                "reason": "secret-value from private-user",
                "position": float("nan"),
                "unreviewed": "private",
            },
        )
    exported = timeline.export()
    assert len(exported["records"]) == MAX_TIMELINE_RECORDS
    assert "secret-value" not in str(exported)
    assert "private-user" not in str(exported)
    assert "unreviewed" not in str(exported)
    assert all("position" not in r["values"] for r in exported["records"])
    assert exported["records"][-1]["sequence"] == MAX_TIMELINE_RECORDS + 100
    tree = redact_evidence_tree(
        {"nested": [{"arbitrary": "secret-value"}], "nan": float("inf")},
        ("secret-value",),
    )
    assert tree["nested"][0]["arbitrary"] == "<redacted>" and tree["nan"] is None


def test_legacy_all_zero_maneuver_slots_do_not_prove_eight_successes():
    result = maneuver_evidence(bytes(8))
    assert result["state"] == "all_zero_unverified"
    assert result["confirmed_success_count"] is None
    assert result["reported_failure_count"] is None
    assert [v["reported_outcome"] for v in result["slots"]] == ["ok"] * 8
    assert result["slot_order"] == "vendor_index_order_newest_unknown"


@pytest.mark.parametrize("raw", [None, b"", bytes(7), b"\xff" * 8])
def test_missing_maneuver_history_is_not_success(raw):
    assert maneuver_evidence(raw)["state"] == "unavailable"


@pytest.mark.parametrize("definition", DIAGNOSTICS, ids=lambda row: row.key)
async def test_unresolved_diagnostic_readers_have_no_queue_or_network_side_effect(
    hass, definition
):
    owner = await instance(hass)
    owner.client.read_dmp_info = AsyncMock()
    if definition.decoder is None:
        with pytest.raises(NiceParameterError):
            await owner.diagnostic_runtime.async_read(definition.key)
        owner.client.read_dmp_info.assert_not_called()
        assert owner.tasks.pending == 0
    await owner.async_shutdown()


async def test_inverter_reuses_hardware_decoder_with_verified_metadata(hass):
    owner = await instance(hass)
    owner.client.read_dmp_info = lambda *args, **kwargs: D2_INFO
    raw = bytes.fromhex(
        "00 ee 00 00 00 00 00 0b 00 20 00 1e 00 00 00 1c 00 00 00 00 00 4c 00 00"
    )
    owner.client.read_dmp_value = lambda *args, **kwargs: raw
    await owner.diagnostic_runtime.async_read("inverter")
    view = owner.diagnostic_runtime.view("inverter")
    assert view["state"] == "available"
    assert view["values"]["speed_m1"] == 76
    assert view["values"]["torque_m1"] == 30
    assert view["values"]["service_voltage"] == 32
    assert view["values"]["motor_temperature"] == 28
    assert "speed_m2" not in view["values"]
    await owner.async_shutdown()


async def test_advanced_history_uses_declared_format_not_advanced_diagnostic_enum(hass):
    owner = await instance(hass)
    owner.client.read_dmp_info = lambda *args, **kwargs: decode_dmp_info(
        bytes.fromhex("01001111006401000a")
    )
    owner.client.read_dmp_value = lambda *args, **kwargs: b"\x0c"
    await owner.diagnostic_runtime.async_read("advanced_history")
    view = owner.diagnostic_runtime.view("advanced_history")
    assert view["value"] == 1.2
    assert view["advanced_diagnostic_enum_assumed"] is False
    assert view["slot_relation_to_04_DA"] == "unknown"
    await owner.async_shutdown()


async def test_signed_or_structured_advanced_history_is_blocked_before_get(hass):
    owner = await instance(hass)
    owner.client.read_dmp_info = lambda *args, **kwargs: DmpInfo(
        8, 0x81, 0, 0x41, values=(1, 2)
    )
    owner.client.read_dmp_value = AsyncMock()
    await owner.diagnostic_runtime.async_read("advanced_history")
    assert owner.diagnostic_runtime.view("advanced_history")["state"] == "blocked"
    owner.client.read_dmp_value.assert_not_called()
    await owner.async_shutdown()


async def test_failed_read_keeps_old_capture_age_and_does_not_refresh_it(
    hass, monkeypatch
):
    from custom_components.nice_bidiwifi import diagnostic_runtime as module

    owner = await instance(hass)
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    owner.client.read_dmp_value = lambda *args, **kwargs: bytes(8)
    await owner.diagnostic_runtime.async_read("maneuvers")
    now[0] = 120
    owner.diagnostic_runtime._watched["maneuvers"] = 1
    owner.diagnostic_runtime._attempts["maneuvers"] = 0

    def fail(*args, **kwargs):
        raise NiceBidiConnectionError("test failure")

    owner.client.read_dmp_value = fail
    now[0] = 400
    await owner.diagnostic_runtime._refresh()
    view = owner.diagnostic_runtime.view("maneuvers")
    assert view["state"] == "unavailable"
    assert view["age_seconds"] == 300
    assert view["last_attempt_age_seconds"] == 0
    await owner.async_shutdown()


async def test_close_fences_late_read_result_and_blocked_maintenance_starts_nothing(
    hass,
):
    owner = await instance(hass)
    owner.observations.invalidate(close=True)
    owner.client.read_dmp_value = AsyncMock()
    for key in ["maneuvers", "maintenance_reset", "power_supply"]:
        with pytest.raises(NiceParameterError):
            await owner.diagnostic_runtime.async_read(key)
    owner.client.read_dmp_value.assert_not_called()
    await owner.async_shutdown()


async def test_enabled_hardware_screen_adds_only_read_only_live_registers(hass):
    owner = await instance(hass)
    owner.async_set_updated_data(make_status(state="opening"))
    owner.diagnostic_runtime._watched["hardware"] = 1
    assert owner.diagnostic_runtime.extra_registers() == ((4, 0xD1), (4, 0xD2))
    owner.diagnostic_runtime._watched.clear()
    assert owner.diagnostic_runtime.extra_registers() == ()
    await owner.async_shutdown()


def test_measurement_field_ages_do_not_become_display_animation_samples():
    timeline = IncidentTimeline({}, clock=lambda: 20)
    status = replace(
        make_status(state="opening", position=30),
        observed_fields=frozenset({"position"}),
        observation_times={"position": 12},
    )
    timeline.measurement(status)
    record = timeline.export()["records"][0]
    assert record["values"] == {"position": 30}
    assert record["field_age_seconds"] == {"position": 8}


async def test_maneuver_event_channel_does_not_overwrite_distinct_dmp_counters(hass):
    from tests.test_events import DIAGNOSTIC_EVENT

    owner = await instance(hass)
    owner.async_set_updated_data(
        make_status(maintenance_count=10, total_maneuver_count=20)
    )
    owner.event_controller.ensure_registered()
    owner.event_controller._handle_raw_frame(DIAGNOSTIC_EVENT)
    assert owner.data.maintenance_count == 10
    assert owner.data.total_maneuver_count == 20
    view = owner.diagnostic_runtime.report()["counters"]
    assert view["nhk_counter_channel"]["nhk_manoeuvre_count"] == 7001
    assert view["values"]["maintenance_count"] == 10
    assert view["nhk_to_dmp_mapping"] == "not_established"
    await owner.async_shutdown()


async def test_unseeded_true_obstruction_is_baseline_not_new_intervention(hass):
    from tests.test_events import _obstruction_event

    owner = await instance(hass)
    owner.event_controller.ensure_registered()
    owner.event_controller._handle_raw_frame(_obstruction_event(True))
    assert owner.latest_event.category is NiceEventCategory.STATE_CHANGE
    assert owner.latest_event.obstruction_evidence == "baseline"
    owner.event_controller._handle_raw_frame(_obstruction_event(False))
    owner.event_controller._handle_raw_frame(_obstruction_event(True))
    assert owner.latest_event.category is NiceEventCategory.OBSTRUCTION
    assert owner.latest_event.obstruction_evidence == "new_edge"
    await owner.async_shutdown()


@pytest.mark.parametrize(
    "product,firmware,key",
    [
        ("MC824H", "CD08", "mc824h_hyke"),
        ("ROBUSR10", "FG01h", "robus_fg01h"),
        ("UNKNOWN", "FG01h", "conservative_unknown"),
    ],
)
def test_profiles_explain_evidence_limits_without_granting_capabilities(
    product, firmware, key
):
    from custom_components.nice_bidiwifi.diagnostic_profiles import diagnostic_profile
    from tests.conftest import make_device_info

    info = replace(
        make_device_info(device_product=product, device_description=product),
        device_fw_version=firmware,
    )
    profile = diagnostic_profile(info)
    assert profile["key"] == key
    assert profile["success_validity"] == "not_established"
    assert profile["newest_slot"] == "unknown"


async def test_counter_reader_never_resets_any_counter_or_merges_channels(hass):
    owner = await instance(hass)
    sent = []
    owner.client.read_dmp_value = lambda group, param, **kwargs: (
        sent.append((group, param)) or (param - 0xB0).to_bytes(4, "big")
    )
    await owner.diagnostic_runtime.async_read("counters")
    view = owner.diagnostic_runtime.view("counters")
    assert sent == [(4, 0xB1), (4, 0xB2), (4, 0xB3)]
    assert view["values"] == {
        "maintenance_threshold": 1,
        "maintenance_count": 2,
        "total_maneuver_count": 3,
    }
    assert view["nhk_to_dmp_mapping"] == "not_established"
    await owner.async_shutdown()


async def test_rejected_read_only_log_request_keeps_healthy_connection(hass):
    from unittest.mock import Mock

    from custom_components.nice_bidiwifi.errors import NiceUnsupportedError
    from tests.conftest import make_device_info

    owner = await instance(hass)
    owner._store_device_info(make_device_info())
    owner.connection_state = "connected"
    owner.client.close = Mock()

    def fail():
        raise NiceUnsupportedError("not supported", code="34")

    owner.client.read_logs = fail
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await owner.administration_controller.async_refresh_interface_logs()
    owner.client.close.assert_not_called()
    assert owner.connection_state == "connected"
    await owner.async_shutdown()


async def test_old_log_response_cannot_be_installed_after_reconnect(hass):
    import asyncio
    import threading

    from homeassistant.exceptions import HomeAssistantError

    from custom_components.nice_bidiwifi.models.administration import NiceLogSnapshot
    from tests.conftest import make_device_info

    owner = await instance(hass)
    owner._store_device_info(make_device_info())
    started = threading.Event()
    release = threading.Event()

    def read():
        started.set()
        assert release.wait(2)
        return NiceLogSnapshot(())

    owner.client.read_logs = read
    task = asyncio.create_task(
        owner.administration_controller.async_refresh_interface_logs()
    )
    await hass.async_add_executor_job(started.wait, 1)
    owner.observations.invalidate()
    release.set()
    with pytest.raises(HomeAssistantError, match="obsolete session"):
        await task
    assert owner.administration_controller.interface_log_snapshot is None
    await owner.async_shutdown()


async def test_counter_reader_does_not_flatten_an_indexed_or_structured_value(hass):
    owner = await instance(hass)
    owner.parameters.record_info(
        owner.parameters.address(4, 0xB2), DmpInfo(3, 0, 4, 0x81, array_size=2)
    )
    owner.client.read_dmp_value = lambda *args, **kwargs: b"\x01\x00\x00\x05"
    await owner.diagnostic_runtime.async_read("counters")
    assert (
        owner.diagnostic_runtime.view("counters")["values"]["maintenance_count"] is None
    )
    await owner.async_shutdown()


@pytest.mark.parametrize(
    "section,code,label",
    [
        (0xC1, 0, "stop_m1"),
        (0xC1, 1, "open_man_present_m1"),
        (0xC1, 2, "close_man_present_m1"),
        (0xC1, 16, "stop_m2"),
        (0xC1, 17, "open_man_present_m2"),
        (0xC1, 18, "close_man_present_m2"),
        (0x81, 1, "step_step"),
        (0x81, 2, "open_partial_1"),
        (0x81, 4, "open"),
        (0x81, 8, "close"),
    ],
)
def test_reported_manual_and_channel_labels_are_not_reinterpreted_as_dep_action_codes(
    section, code, label
):
    message = f"04 {section:02x} {code:02x}"
    live = parse_cuwifi_live_status_payload(plain(message))
    history = decode_history_row(1, None, "DEP", plain(message))
    assert live.event_description == history.event_description == label
    assert live.command is None and history.command is None
    assert history.actor == "unknown"


async def test_unresolved_transport_cannot_queue_even_an_existing_diagnostic_codec(
    hass,
):
    from custom_components.nice_bidiwifi.models.capabilities import (
        NiceCapabilities,
        ProductFamily,
    )
    from tests.conftest import make_device_info

    owner = await instance(hass)
    owner.capabilities = replace(
        NiceCapabilities.from_device_info(make_device_info(), 1),
        family=ProductFamily.CORE,
    )
    owner.client.read_dmp_value = AsyncMock()
    with pytest.raises(NiceParameterError, match="transport.core"):
        await owner.diagnostic_runtime.async_read("maneuvers")
    owner.client.read_dmp_value.assert_not_called()
    await owner.async_shutdown()


def test_large_event_frame_reports_partial_payload_retention():
    parsed = parse_nhk_event_frame(_t4_event(*(["04 82 03"] * 25)))[0]
    assert len(parsed.t4_evidence) == 16
    assert parsed.t4_evidence_truncated
    timeline = IncidentTimeline({})
    timeline.event(parsed)
    assert all(row["values"]["frame_payloads_truncated"] for row in timeline.records)


async def test_nhk_only_counter_channel_stays_visible_without_invented_dmp_values(hass):
    owner = await instance(hass)
    owner.async_set_updated_data(
        make_status(
            maintenance_count=None,
            total_maneuver_count=None,
            maintenance_threshold=None,
        )
    )
    owner.diagnostic_runtime.event_counters.update(
        nhk_manoeuvre_count=123, received_at="2026-11-24T12:00:00Z"
    )
    view = owner.diagnostic_runtime.view("counters")
    assert view["state"] == "available"
    assert view["nhk_channel_state"] == "available"
    assert view["dmp_channel_state"] == "unavailable"
    assert view["values"]["total_maneuver_count"] is None
    assert view["nhk_counter_channel"]["nhk_manoeuvre_count"] == 123
    owner.diagnostic_runtime._samples["counters"] = {"state": "unsupported"}
    view = owner.diagnostic_runtime.view("counters")
    assert view["state"] == "available" and view["dmp_channel_state"] == "unsupported"
    await owner.async_shutdown()


def test_secret_redaction_precedes_evidence_length_limits():
    secret = "private-token-" * 100
    timeline = IncidentTimeline({"access_token": secret})
    timeline.add("event", "nhk", {"reason": secret + " trailing context"})
    assert timeline.records[0]["values"]["reason"] == "<redacted> trailing context"
    assert (
        redact_evidence_tree({"message": secret + " tail"}, (secret,))["message"]
        == "<redacted> tail"
    )


async def test_counter_card_honors_known_info_scale_and_preserves_raw_evidence(hass):
    owner = await instance(hass)
    owner.parameters.record_info(
        owner.parameters.address(4, 0xB2),
        DmpInfo(4, 0, 4, 0x11, minimum=0, maximum=10000, step=1, scale=10),
    )
    owner.async_set_updated_data(make_status(maintenance_count=120))
    assert (
        owner.diagnostic_runtime.view("counters")["values"]["maintenance_count"] == 12
    )
    owner.client.read_dmp_value = lambda *args, **kwargs: (120).to_bytes(4, "big")
    await owner.diagnostic_runtime.async_read("counters")
    view = owner.diagnostic_runtime.view("counters")
    assert view["values"]["maintenance_count"] == 12
    assert view["raw_values"]["maintenance_count"] == 120
    await owner.async_shutdown()
