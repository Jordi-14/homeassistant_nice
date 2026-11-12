"""Tests for normalized unsolicited Nice events."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from custom_components.nice_bidiwifi.errors import NiceProtocolError
from custom_components.nice_bidiwifi.event import (
    NiceProtocolEventEntity,
    async_setup_entry,
)
from custom_components.nice_bidiwifi.models.events import (
    NiceEvent,
    NiceEventCategory,
    NiceEventKind,
)
from custom_components.nice_bidiwifi.protocol.nhk.codec import frame_xml
from custom_components.nice_bidiwifi.protocol.nhk.events import parse_nhk_event_frame
from custom_components.nice_bidiwifi.protocol.nhk.status import parse_nhk_status_frames
from custom_components.nice_bidiwifi.protocol.t4.codec import xor_sha256
from tests.conftest import (
    FakeClient,
    FakeCoordinator,
    config_entry,
    config_entry_data,
    make_status,
)

DIAGNOSTIC_EVENT = frame_xml(
    """
    <Event id="17" type="DIAGNOSTIC">
      <Timestamp>2026-07-24T10:15:00Z</Timestamp>
      <Devices>
        <Device id="1">
          <Properties>
            <DoorStatus>opening</DoorStatus>
            <Obstruct>true</Obstruct>
            <EventDetails>
              <BasicDiagnostic>B01</BasicDiagnostic>
              <CauseCode>C02</CauseCode>
              <AdvDiagnostic>A03</AdvDiagnostic>
              <RelativeTimeStamp>44</RelativeTimeStamp>
              <BlueBusErrStatus>04</BlueBusErrStatus>
              <BatteryLevel devType="photocell" devMac="AA:BB:CC:DD:EE:FF">low</BatteryLevel>
              <ManoeuvreCount>7001</ManoeuvreCount>
              <ManoeuvreThLimit>7000</ManoeuvreThLimit>
              <ManoeuvreAvgCurrent>2.75</ManoeuvreAvgCurrent>
              <CUResetCause devClass="control_unit">watchdog</CUResetCause>
            </EventDetails>
          </Properties>
        </Device>
      </Devices>
    </Event>
    """
)


def _obstruction_event(value: bool, *, state: str = "closing") -> bytes:
    return frame_xml(
        '<Event type="CHANGE"><Devices><Device id="1"><Properties>'
        f"<DoorStatus>{state}</DoorStatus>"
        f"<Obstruct>{str(value).lower()}</Obstruct>"
        "</Properties></Device></Devices></Event>"
    )


def test_obstruction_parser_reports_state_without_claiming_a_new_event() -> None:
    """Only the stateful coordinator can decide whether a flag is a new event."""
    event = parse_nhk_event_frame(_obstruction_event(True))[0]
    assert event.obstruction is True
    assert event.category is NiceEventCategory.STATE_CHANGE


def _live_position_event(
    state_byte: int,
    *,
    door_status: str | None = None,
) -> bytes:
    """Build one encrypted CU_WIFI 04/40 event."""
    key = b"event-key"
    payload = bytearray.fromhex(
        "55 0f 00 ff 00 03 01 08 f5 04 40 00 00 4c ff ff 52 0f"
    )
    payload[11] = state_byte
    encrypted = xor_sha256(bytes(payload), key)
    properties = (
        f"<Devices><Device id=\"1\"><Properties><DoorStatus>{door_status}"
        "</DoorStatus></Properties></Device></Devices>"
        if door_status is not None
        else ""
    )
    return frame_xml(
        '<Event type="T4_EVENT" id="42">'
        + properties
        + '<Interface><T4 key="'
        + base64.b64encode(key).decode()
        + '">'
        + base64.b64encode(encrypted).decode()
        + "</T4></Interface></Event>"
    )


def _t4_event(*messages: str, xml: str = "", event_type: str = "T4_EVENT") -> bytes:
    """Build one encrypted CU_WIFI event around one or more inner 04/xx messages."""
    key = b"event-key"
    elements = ""
    for message in messages:
        inner = bytes.fromhex(message)
        body = bytes.fromhex("00 ff 00 03 01") + bytes([len(inner) + 1, 0]) + inner + b"\x00"
        plain = bytes([0x55, len(body)]) + body + bytes([len(body)])
        elements += (
            f'<T4 key="{base64.b64encode(key).decode()}">'
            + base64.b64encode(xor_sha256(plain, key)).decode()
            + "</T4>"
        )
    return frame_xml(
        f'<Event type="{event_type}" id="44">' + xml + "<Interface>" + elements + "</Interface></Event>"
    )


def _obstruct_xml(value: bool) -> str:
    return (
        '<Devices><Device id="1"><Properties>'
        f"<Obstruct>{str(value).lower()}</Obstruct></Properties></Device></Devices>"
    )


def test_event_parser_decodes_t4_state_change_and_command() -> None:
    """Live 04/02 and 04/82 events expose the decoded vendor names."""
    event = parse_nhk_event_frame(_t4_event("04 02 04 00 00 00"))[0]
    assert event.state == "open"
    assert event.t4_change_state == "stopped_in_opened"
    assert event.t4_cause == "none"
    assert event.obstruction is None
    assert event.t4_basic_diagnostic is None

    event = parse_nhk_event_frame(_t4_event("04 02 83 01 64 64"))[0]
    assert (event.t4_change_state, event.t4_cause) == ("opening_from_command", "radio")

    event = parse_nhk_event_frame(_t4_event("04 82 03 00"))[0]
    assert event.t4_payload_kind == "04/82"
    assert event.t4_command == "open"
    assert event.state is None


def test_event_parser_derives_obstruction_from_t4_diagnostics() -> None:
    """The 0x23 diagnostics block gives obstruction; XML Obstruct wins."""
    ok = parse_nhk_event_frame(_t4_event("04 02 04 00 00 00 23 00 00"))[0]
    assert ok.obstruction is False
    assert ok.t4_basic_diagnostic == "ok"
    assert ok.t4_advanced_diagnostic == "no_advanced_diagnostic"
    basic = parse_nhk_event_frame(_t4_event("04 02 04 00 00 00 23 03 00"))[0]
    assert basic.obstruction is True
    assert basic.t4_basic_diagnostic == "obstacle_detected"
    advanced = parse_nhk_event_frame(_t4_event("04 02 04 00 00 00 23 00 0c"))[0]
    assert advanced.obstruction is True
    xml_wins = parse_nhk_event_frame(
        _t4_event("04 02 04 00 00 00 23 03 00", xml=_obstruct_xml(False))
    )[0]
    assert xml_wins.obstruction is False
    assert xml_wins.obstruction_source == "nhk"


def test_event_parser_merges_payloads_in_one_frame() -> None:
    """Event fields from every payload are kept; the stateful payload is the base."""
    event = parse_nhk_event_frame(
        _t4_event("04 02 04 00 00 00 23 03 00", "04 40 00 00 4c")
    )[0]
    assert event.position == 76.0
    assert event.obstruction is True
    assert event.obstruction_source == "t4"
    assert event.t4_cause == "none"
    assert event.t4_event_class == "movement_in_progress"

    event = parse_nhk_event_frame(_t4_event("04 02 04 00", "04 82 03 00"))[0]
    assert event.state == "open"
    assert event.t4_command == "open"
    assert event.t4_event_class == "state_changed"


def test_event_only_payload_keeps_xml_event_kind() -> None:
    """A 04/82 payload does not turn an XML diagnostic into a live status."""
    event = parse_nhk_event_frame(
        _t4_event("04 82 03 00", event_type="DIAGNOSTIC", xml='<Devices><Device id="1"><Properties></Properties></Device></Devices>')
    )[0]
    assert event.kind is NiceEventKind.DIAGNOSTIC
    assert event.category is NiceEventCategory.DIAGNOSTIC
    assert event.t4_command == "open"


def test_photo_and_auto_close_movements_map_to_states() -> None:
    """EvtChangeState 0x81, 0x82 and 0x85 are movements."""
    for byte, state in (("81", "opening"), ("82", "closing"), ("85", "closing")):
        assert parse_nhk_event_frame(_t4_event(f"04 02 {byte} 00"))[0].state == state


def test_stateless_t4_events_do_not_enter_status_polls() -> None:
    """Event-only live frames never change the polled gate status."""
    for message in ("04 82 03 00", "04 02 7e 00 00 00"):
        frame = _t4_event(message)
        try:
            parse_nhk_status_frames([frame])
        except NiceProtocolError:
            continue
        raise AssertionError(message)


async def test_t4_diagnostic_obstacle_and_last_cause(hass) -> None:
    """A T4 obstacle diagnostic raises the obstruction category once."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="t4-obst")
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    coordinator.async_set_updated_data(make_status(obstacle=False))
    controller = coordinator.event_controller
    controller.ensure_registered()
    controller.reconcile_polled_obstacle(coordinator.data)

    controller._handle_raw_frame(_t4_event("04 02 04 00 00 00 23 03 00"))
    assert coordinator.latest_event.category is NiceEventCategory.OBSTRUCTION
    assert coordinator.last_t4_cause == "none"
    assert coordinator.last_t4_basic_diagnostic == "obstacle_detected"
    assert coordinator.data.registers["NHK/T4Obstruct"] == "true"
    assert coordinator.data.registers.get("NHK/Obstruct") != "true"

    controller._handle_raw_frame(_t4_event("04 82 03 00"))
    assert coordinator.last_t4_command == "open"
    assert coordinator.last_t4_basic_diagnostic == "obstacle_detected"

    controller._handle_raw_frame(_t4_event("04 02 83 01 64 64"))
    assert coordinator.last_t4_change_state == "opening_from_command"
    assert coordinator.last_t4_cause == "radio"
    assert coordinator.last_t4_basic_diagnostic is None
    await coordinator.async_shutdown()


def _live_scalar_position_event(raw_position: int) -> bytes:
    """Build one encrypted RBA4R10-style raw 04/40 event."""
    key = b"event-key"
    payload = bytearray.fromhex(
        "55 0e ff 01 00 03 01 07 fb 04 40 83 00 00 00 a4 0e"
    )
    payload[12:14] = raw_position.to_bytes(2, "big")
    encrypted = xor_sha256(bytes(payload), key)
    return frame_xml(
        '<Event type="T4_EVENT" id="43"><Interface><T4 key="'
        + base64.b64encode(key).decode()
        + '">'
        + base64.b64encode(encrypted).decode()
        + "</T4></Interface></Event>"
    )


def test_event_parser_normalizes_all_observed_app_fields() -> None:
    """All event fields consumed by the app have typed normalized equivalents."""
    received_at = datetime(2026, 7, 24, 10, 16, tzinfo=UTC)

    event = parse_nhk_event_frame(
        DIAGNOSTIC_EVENT,
        received_at=received_at,
    )[0]

    assert event.kind is NiceEventKind.DIAGNOSTIC
    assert event.category is NiceEventCategory.BLUEBUS_ERROR
    assert event.received_at is received_at
    assert event.event_id == "17"
    assert event.device_id == "1"
    assert event.state == "opening"
    assert event.obstruction is True
    assert event.protocol_timestamp == "2026-07-24T10:15:00Z"
    assert event.basic_diagnostic_code == "B01"
    assert event.cause_code == "C02"
    assert event.advanced_diagnostic_code == "A03"
    assert event.relative_timestamp == "44"
    assert event.bluebus_error_status == "04"
    assert event.battery_device_type == "photocell"
    assert event.battery_level_code == "low"
    assert event.manoeuvre_count == 7001
    assert event.manoeuvre_threshold == 7000
    assert event.manoeuvre_average_current == 2.75
    assert event.reset_device_class == "control_unit"
    assert event.reset_cause == "watchdog"
    assert "AA:BB:CC:DD:EE:FF" not in repr(event.as_event_attributes())
    assert "AA:BB:CC:DD:EE:FF" not in repr(event.as_diagnostics())


def test_event_parser_filters_other_devices_and_bounds_text() -> None:
    """Events for another automation are ignored and text cannot grow unbounded."""
    long_code = "X" * 200
    frame = frame_xml(
        f"""
        <Event type="DIAGNOSTIC">
          <Devices>
            <Device id="2"><Properties><CauseCode>other</CauseCode></Properties></Device>
            <Device id="1"><Properties><CauseCode>{long_code}</CauseCode></Properties></Device>
          </Devices>
        </Event>
        """
    )

    events = parse_nhk_event_frame(frame, device_id=1)

    assert len(events) == 1
    assert events[0].device_id == "1"
    assert events[0].cause_code == "X" * 96

    only_other_device = frame_xml(
        """
        <Event type="CHANGE">
          <Devices>
            <Device id="2"><Properties><DoorStatus>open</DoorStatus></Properties></Device>
          </Devices>
        </Event>
        """
    )
    assert parse_nhk_event_frame(only_other_device, device_id=1) == ()


def test_event_parser_normalizes_live_t4_position() -> None:
    """Validated CU_WIFI live frames feed the same normalized event path."""
    event = parse_nhk_event_frame(_live_position_event(0x00))[0]

    assert event.kind is NiceEventKind.LIVE_STATUS
    assert event.category is NiceEventCategory.STATE_CHANGE
    assert event.position == 76.0
    assert event.t4_payload_kind == "04/40"
    assert event.t4_raw_position == 76
    assert event.t4_position_scale == "percent"


def test_event_parser_keeps_terminal_04_40_state_position_only() -> None:
    """A coarse 04/40 endpoint byte cannot create a terminal state transition."""
    event = parse_nhk_event_frame(_live_position_event(0x04))[0]

    assert event.position == 76.0
    assert event.state is None
    assert event.t4_state == "open"


def test_event_parser_prefers_04_40_motion_over_stale_door_status() -> None:
    """A live movement byte wins over a stale terminal DoorStatus value."""
    event = parse_nhk_event_frame(
        _live_position_event(0x83, door_status="open")
    )[0]

    assert event.raw_state == "open"
    assert event.t4_state == "opening"
    assert event.state == "opening"


async def test_event_controller_keeps_motion_during_position_only_event(
    hass,
) -> None:
    """A coarse position event cannot make a moving cover flicker open."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-event-precedence",
    )
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    client = FakeClient()
    coordinator.client = client
    coordinator.async_set_updated_data(make_status(state="opening", position=50.0))
    coordinator.event_controller.ensure_registered()

    client.emit_event(_live_position_event(0x04))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert coordinator.data.state == "opening"
    assert coordinator.data.position == 76.0
    assert coordinator.data.registers["NHK/T4Status"] == "open"
    assert coordinator.data.registers["NHK/T4StatusIgnored"] == "04/40_position_only"
    assert coordinator.data.registers["NHK/T4InstantPosition"] == "76"
    await coordinator.async_shutdown()


async def test_event_controller_applies_live_scalar_calibration_immediately(
    hass,
) -> None:
    """Raw live events use calibration bounds without waiting for a poll."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-event-live-scalar",
    )
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    client = FakeClient()
    coordinator.client = client
    coordinator.calibration_profile = {
        "mode": "live_scalar",
        "bounds": {
            "live_scalar_closed_raw": 1000,
            "live_scalar_open_raw": 5000,
        },
    }
    coordinator.async_set_updated_data(make_status(state="closed", position=0.0))
    coordinator.event_controller.ensure_registered()

    client.emit_event(_live_scalar_position_event(3000))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert coordinator.data.state == "opening"
    assert coordinator.data.position == 50.0
    assert coordinator.data.registers["NHK/T4InstantPositionRaw"] == "3000"
    assert coordinator.data.registers["NHK/T4CalibratedPosition"] == "50.0"
    await coordinator.async_shutdown()


async def test_event_controller_updates_existing_status_fields_and_fallback(
    hass,
) -> None:
    """Push events update existing entities while polling remains the fallback."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-events",
    )
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    client = FakeClient()
    coordinator.client = client
    coordinator.async_set_updated_data(
        make_status(
            state="closed",
            obstacle=False,
            maintenance_count=10,
            total_maneuver_count=10,
        )
    )
    coordinator.event_controller.ensure_registered()

    client.emit_event(DIAGNOSTIC_EVENT)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert coordinator.data.state == "opening"
    assert coordinator.data.obstacle is True
    assert coordinator.data.maintenance_count == 7001
    assert coordinator.data.total_maneuver_count == 7001
    assert coordinator.data.maintenance_threshold == 7000
    assert coordinator.protocol_event_count == 1
    assert coordinator.latest_event.category is NiceEventCategory.BLUEBUS_ERROR
    assert coordinator.event_stream_state == "active"

    refreshes = 0

    async def request_refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    coordinator.async_request_refresh = request_refresh
    client.fail_event_stream(OSError("sensitive peer detail"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert coordinator.event_stream_state == "fallback_polling"
    assert coordinator.event_stream_error == "OSError"
    assert refreshes == 1
    await coordinator.async_shutdown()


async def test_obstruction_event_requires_a_new_true_observation(hass) -> None:
    """A latched true flag does not create another obstruction notification."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="obstruct-edge")
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    coordinator.async_set_updated_data(make_status(obstacle=False))
    controller = coordinator.event_controller
    controller.ensure_registered()
    controller.reconcile_polled_obstacle(coordinator.data)

    controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.OBSTRUCTION
    assert coordinator.data.obstacle is True

    controller._handle_raw_frame(_obstruction_event(True, state="closed"))
    assert coordinator.latest_event.category is NiceEventCategory.STATE_CHANGE
    assert coordinator.data.obstacle is True

    controller._handle_raw_frame(_obstruction_event(False))
    assert coordinator.data.obstacle is False
    controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.OBSTRUCTION
    await coordinator.async_shutdown()


async def test_unknown_poll_keeps_obstacle_until_reconnect(hass) -> None:
    """Unknown DMP data cannot erase a fresh live obstacle observation."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="obstruct-poll")
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    client = FakeClient()
    coordinator.client = client
    coordinator.async_set_updated_data(make_status(obstacle=False))
    coordinator.event_controller.ensure_registered()
    coordinator.event_controller.reconcile_polled_obstacle(coordinator.data)
    coordinator.event_controller._handle_raw_frame(_obstruction_event(True))

    client.read_status_result = make_status(obstacle=None)
    assert (await coordinator._async_update_data()).obstacle is True
    assert (await coordinator._async_update_data()).obstacle is True

    # A polled obstacle value never overrides the live Obstruct state.
    client.read_status_result = make_status(obstacle=False)
    assert (await coordinator._async_update_data()).obstacle is True
    coordinator.event_controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.STATE_CHANGE

    coordinator.event_controller.mark_reconnecting()
    client.read_status_result = make_status(obstacle=None)
    assert (await coordinator._async_update_data()).obstacle is None
    coordinator.event_controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.STATE_CHANGE
    coordinator.event_controller._handle_raw_frame(_obstruction_event(False))
    coordinator.event_controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.OBSTRUCTION
    await coordinator.async_shutdown()


async def test_internal_reconnect_does_not_replay_latched_obstruction(hass) -> None:
    """A successful client retry still starts a new obstruction baseline."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="obstruct-retry")
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    client = FakeClient()
    coordinator.client = client
    controller = coordinator.event_controller
    controller.ensure_registered()
    controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.OBSTRUCTION

    client.reconnect_count += 1
    controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.STATE_CHANGE
    controller._handle_raw_frame(_obstruction_event(False))
    controller._handle_raw_frame(_obstruction_event(True))
    assert coordinator.latest_event.category is NiceEventCategory.OBSTRUCTION
    await coordinator.async_shutdown()


async def test_event_controller_drops_malformed_frames(hass) -> None:
    """Malformed unsolicited payloads are counted without replacing good state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-malformed",
    )
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    coordinator.async_set_updated_data(make_status(state="closed"))
    coordinator.event_controller.ensure_registered()

    coordinator.event_controller._handle_raw_frame(frame_xml("<broken"))

    assert coordinator.malformed_protocol_event_count == 1
    assert coordinator.data.state == "closed"
    await coordinator.async_shutdown()


def test_event_entity_triggers_stable_categories() -> None:
    """The EventEntity publishes the normalized category and bounded attributes."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entity = NiceProtocolEventEntity(coordinator, entry)
    entity.async_write_ha_state = lambda: None
    received_at = datetime.now(UTC)
    event = NiceEvent(
        kind=NiceEventKind.CHANGE,
        category=NiceEventCategory.OBSTRUCTION,
        received_at=received_at,
        device_id="1",
        obstruction=True,
    )
    coordinator.latest_event = event
    coordinator.event_sequence = 1

    entity._handle_coordinator_update()

    assert entity._EventEntity__last_event_type == "obstruction"
    assert entity._EventEntity__last_event_attributes == {
        "kind": "change",
        "device_id": "1",
        "received_at": received_at.isoformat(),
        "obstruction": True,
    }


async def test_event_entity_is_omitted_only_when_explicitly_unsupported() -> None:
    """Unknown devices keep discovery room; explicit non-support removes it."""
    coordinator = FakeCoordinator()
    coordinator.capabilities = SimpleNamespace(local_events=False)
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    await async_setup_entry(None, entry, lambda entities: created.extend(entities))

    assert created == []


def test_event_payload_hex_is_kept_for_04_02_and_04_82_only() -> None:
    """The raw hex of the probe sample passes through; 04/40 adds none."""
    event = parse_nhk_event_frame(_t4_event("04 02 83 01 64 64"))[0]
    assert event.t4_payload_hex == "04 02 83 01 64 64"
    assert event.as_diagnostics()["t4_payload_hex"] == "04 02 83 01 64 64"
    assert parse_nhk_event_frame(_t4_event("04 82 03 00"))[0].t4_payload_hex == "04 82 03 00"
    assert parse_nhk_event_frame(_live_position_event(0x00))[0].t4_payload_hex is None
    merged = parse_nhk_event_frame(_t4_event("04 02 04 00", "04 40 00 00 4c"))[0]
    assert merged.t4_payload_hex == "04 02 04 00"


class _Clock:
    """Controllable monotonic clock for the event controller."""

    def __init__(self, monkeypatch, start: float = 1000.0) -> None:
        self.now = start
        monkeypatch.setattr(
            "custom_components.nice_bidiwifi.controllers.events.time",
            SimpleNamespace(monotonic=lambda: self.now),
        )


async def _abnormal_coordinator(hass, *, real_refresh=False, **status_values):
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="abnormal")
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    if not real_refresh:
        # a real scan would replace coordinator.data at a scheduler-dependent time
        coordinator.async_refresh = AsyncMock()
    coordinator.async_set_updated_data(make_status(**status_values))
    coordinator.event_controller.ensure_registered()
    return coordinator


async def test_unexpected_stop_without_command_fires_event(hass, monkeypatch) -> None:
    """Cycle 3: stopped_for_command_arrival with no recent command."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(hass)
    coordinator.last_command_monotonic = clock.now - 29.0

    coordinator.event_controller._handle_raw_frame(_t4_event("04 02 88 00"))
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["reason"] == "unexpected_stop"
    assert events[0].data["change_state"] == "stopped_for_command_arrival"
    assert events[0].data["seconds_since_command"] == 29.0
    assert coordinator.abnormal_stop_count == 1
    assert coordinator.last_abnormal_stop["reason"] == "unexpected_stop"
    assert coordinator.data.state == "opening"  # 04/02 136 carries no state
    await coordinator.async_shutdown()


async def test_stop_after_recent_command_or_partial_stop_is_normal(hass, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(hass)
    coordinator.last_command_monotonic = clock.now - 1.0
    # cycle 2: the HA command itself stopped the gate (cause 25, oview)
    coordinator.event_controller._handle_raw_frame(_t4_event("04 02 88 19"))
    coordinator.last_command_monotonic = None
    coordinator.event_controller._handle_raw_frame(_t4_event("04 02 10 00"))
    # stop by the radio remote (cause 1) with no HA command
    coordinator.event_controller._handle_raw_frame(_t4_event("04 02 88 01"))
    await hass.async_block_till_done()
    assert events == []
    assert coordinator.abnormal_stop_count == 0
    await coordinator.async_shutdown()


async def test_percent_scale_live_position_does_not_fake_implausible_travel(
    hass, monkeypatch
) -> None:
    """A percent 04/40 value (96) is not an encoder position; only 04/11 is used."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="open", current_position=4900, closed_position=0, open_position=5114
    )
    controller = coordinator.event_controller
    controller._handle_raw_frame(_t4_event("04 40 01 00 60"))
    assert coordinator.data.registers["NHK/T4InstantPositionScale"] == "percent"
    assert coordinator.data.registers["NHK/T4InstantPositionRaw"] == "96"

    controller._handle_raw_frame(_t4_event("04 02 83 00"))
    clock.now += 1.5
    controller._handle_raw_frame(_t4_event("04 02 04 00"))
    await hass.async_block_till_done()

    assert coordinator.latest_event.t4_implausible is None
    assert events == []
    await coordinator.async_shutdown()


async def test_short_fast_enough_travel_is_not_implausible(hass, monkeypatch) -> None:
    """More than 1000 units in 3.5 s is 1460 units/s: real, so no event."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="closed", current_position=0, closed_position=0, open_position=5114
    )
    controller = coordinator.event_controller
    controller._handle_raw_frame(_t4_event("04 02 83 00", xml=_nhk_xml("opening")))
    clock.now += 3.5
    controller._handle_raw_frame(_t4_event("04 02 04 00", xml=_nhk_xml("open")))
    await hass.async_block_till_done()
    assert events == []
    await coordinator.async_shutdown()


async def test_implausible_travel_is_flagged_and_state_kept(hass, monkeypatch) -> None:
    """Cycle 4: the gate did not move; the alert fires and the endpoint state is kept."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="open", current_position=5263, closed_position=0, open_position=5263
    )
    controller = coordinator.event_controller

    controller._handle_raw_frame(_t4_event("04 02 84 00"))
    clock.now += 1.23
    controller._handle_raw_frame(_t4_event("04 02 05 00"))
    assert coordinator.latest_event.t4_implausible is True
    assert coordinator.latest_event.as_event_attributes()["t4_implausible"] is True
    assert coordinator.data.state == "closed"

    coordinator.async_set_updated_data(replace(coordinator.data, current_position=0))
    clock.now += 10.11
    controller._handle_raw_frame(_t4_event("04 02 83 00"))
    clock.now += 1.73
    controller._handle_raw_frame(_t4_event("04 02 04 00"))
    await hass.async_block_till_done()

    assert [e.data["reason"] for e in events] == ["implausible_travel"] * 2
    assert coordinator.abnormal_stop_count == 2
    await coordinator.async_shutdown()


async def test_normal_full_travel_is_applied(hass, monkeypatch) -> None:
    """Cycle 2: a stop about 16 s after the command is real travel."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="closed", current_position=0, closed_position=0, open_position=5263
    )
    controller = coordinator.event_controller
    controller._handle_raw_frame(_t4_event("04 02 83 00"))
    clock.now += 16.0
    controller._handle_raw_frame(_t4_event("04 02 04 00"))
    await hass.async_block_till_done()
    assert events == []
    assert coordinator.latest_event.t4_implausible is None
    assert coordinator.data.state == "open"
    await coordinator.async_shutdown()


async def test_abnormal_stop_scans_at_most_once_per_30_seconds(hass, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    coordinator = await _abnormal_coordinator(hass, state="open")
    controller = coordinator.event_controller

    for offset in (0.0, 10.0, 31.0):
        clock.now = 1000.0 + offset
        controller._handle_raw_frame(_t4_event("04 02 88 00"))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.async_refresh.await_count == 2
    assert coordinator.abnormal_stop_count == 3
    await coordinator.async_shutdown()


def _nhk_change(state: str) -> str:
    return f'<Event type="CHANGE">{_nhk_xml(state)}</Event>'


def _nhk_xml(state: str) -> str:
    return (
        '<Devices><Device id="1"><Properties>'
        f"<DoorStatus>{state}</DoorStatus></Properties></Device></Devices>"
    )


async def test_cycle_4_open_stop_is_unexpected_even_after_a_command(hass, monkeypatch) -> None:
    """Cause none right after an HA command is still unexpected; it reports travel time."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="closed", current_position=39, closed_position=0, open_position=39
    )
    controller = coordinator.event_controller
    coordinator.last_command_monotonic = clock.now
    clock.now += 0.6
    controller._handle_raw_frame(_t4_event("04 02 83 00 00 00"))
    clock.now += 1.915
    controller._handle_raw_frame(
        _t4_event("04 02 88 00 00 00", xml=_nhk_xml("stopped"))
    )
    await hass.async_block_till_done()

    assert [e.data["reason"] for e in events] == ["unexpected_stop"]
    assert events[0].data["travel_seconds"] == 1.9
    assert events[0].data["seconds_since_command"] == 2.5
    await coordinator.async_shutdown()


async def test_abnormal_stop_after_command_only_forces_the_next_scan(hass, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    coordinator = await _abnormal_coordinator(hass, state="open")
    coordinator._extended_status_next_refresh_monotonic = 1e9
    coordinator.last_command_monotonic = clock.now - 1.0
    coordinator.event_controller._handle_raw_frame(_t4_event("04 02 88 00"))
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator.async_refresh.assert_not_called()
    assert coordinator._extended_status_next_refresh_monotonic == 0.0
    await coordinator.async_shutdown()


async def test_nhk_implausible_close_without_04_02(hass, monkeypatch) -> None:
    """Cycle 4 close: NHK closing then closed 0.96 s later, no 04/02 at all."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass,
        state="open",
        current_position=5114,
        closed_position=0,
        open_position=5114,
    )
    controller = coordinator.event_controller
    controller._handle_raw_frame(_t4_event("04 40 01 00 60"))  # real percent frame
    controller._handle_raw_frame(frame_xml(_nhk_change("closing")))
    clock.now += 0.96
    controller._handle_raw_frame(frame_xml(_nhk_change("closed")))
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in events] == ["implausible_travel"]
    assert events[0].data["travel_seconds"] == 1.0
    await coordinator.async_shutdown()


async def test_nhk_normal_travel_is_not_implausible(hass, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="open", current_position=5114, closed_position=0, open_position=5114
    )
    controller = coordinator.event_controller
    controller._handle_raw_frame(_t4_event("04 02 83 00", xml=_nhk_xml("closing")))
    clock.now += 83.0
    controller._handle_raw_frame(_t4_event("04 02 05 19", xml=_nhk_xml("closed")))
    coordinator.async_set_updated_data(replace(coordinator.data, current_position=0))
    controller._handle_raw_frame(_t4_event("04 40 00 00 00", xml=_nhk_xml("opening")))
    clock.now += 30.0
    controller._handle_raw_frame(_t4_event("04 02 04 00", xml=_nhk_xml("open")))
    await hass.async_block_till_done()
    assert events == []
    await coordinator.async_shutdown()


async def test_one_event_when_04_02_and_nhk_both_match(hass, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="open", current_position=5114, closed_position=0, open_position=5114
    )
    controller = coordinator.event_controller
    controller._handle_raw_frame(_t4_event("04 02 84 00", xml=_nhk_xml("closing")))
    clock.now += 1.0
    controller._handle_raw_frame(_t4_event("04 02 05 00", xml=_nhk_xml("closed")))
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in events] == ["implausible_travel"]
    assert coordinator.data.state == "closed"  # NHK state still applies
    await coordinator.async_shutdown()


async def test_repeated_door_status_frames_do_not_rearm_the_nhk_move(hass, monkeypatch) -> None:
    """A normal 30 s open with DoorStatus=opening every second is not implausible."""
    clock = _Clock(monkeypatch)
    events = async_capture_events(hass, "nice_bidiwifi_abnormal_stop")
    coordinator = await _abnormal_coordinator(
        hass, state="closed", current_position=0, closed_position=0, open_position=5114
    )
    controller = coordinator.event_controller
    for _ in range(30):
        controller._handle_raw_frame(frame_xml(_nhk_change("opening")))
        clock.now += 1.0
    controller._handle_raw_frame(frame_xml(_nhk_change("open")))
    await hass.async_block_till_done()
    assert events == []
    await coordinator.async_shutdown()
