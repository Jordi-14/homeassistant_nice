"""Tests for normalized unsolicited Nice events."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
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
    key = b"event-key"
    payload = bytes.fromhex(
        "55 0f 00 ff 00 03 01 08 f5 04 40 00 00 4c ff ff 52 0f"
    )
    encrypted = xor_sha256(payload, key)
    frame = frame_xml(
        '<Event type="T4_EVENT" id="42"><Interface><T4 key="'
        + base64.b64encode(key).decode()
        + '">'
        + base64.b64encode(encrypted).decode()
        + "</T4></Interface></Event>"
    )

    event = parse_nhk_event_frame(frame)[0]

    assert event.kind is NiceEventKind.LIVE_STATUS
    assert event.category is NiceEventCategory.STATE_CHANGE
    assert event.position == 76.0


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
