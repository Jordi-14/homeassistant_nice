"""Live position sources must agree with the controller's known travel bounds."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from custom_components.nice_bidiwifi.models.events import (
    NiceEvent,
    NiceEventCategory,
    NiceEventKind,
)
from custom_components.nice_bidiwifi.models.position import resolve_position
from custom_components.nice_bidiwifi.protocol.nhk.codec import frame_xml
from custom_components.nice_bidiwifi.protocol.nhk.events import parse_nhk_event_frame
from custom_components.nice_bidiwifi.protocol.t4.codec import xor_sha256
from tests.conftest import FakeClient, config_entry_data, make_status


def _raw_event(raw: int) -> bytes:
    key = b"position-test"
    payload = bytearray.fromhex("55 0e ff 01 00 03 01 07 fb 04 40 83 00 00 00 a4 0e")
    payload[12:14] = raw.to_bytes(2, "big")
    encrypted = xor_sha256(bytes(payload), key)
    return frame_xml(
        '<Event type="T4_EVENT"><Interface><T4 key="'
        + base64.b64encode(key).decode()
        + '">'
        + base64.b64encode(encrypted).decode()
        + "</T4></Interface></Event>"
    )


def _coordinator(hass, *, closed=0, opened=4137, profile=None):
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data())
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    coordinator.calibration_profile = profile
    coordinator.async_set_updated_data(
        make_status(state="opening", position=0.0, current_position=100, closed_position=closed, open_position=opened)
    )
    coordinator.event_controller.ensure_registered()
    return coordinator


@pytest.mark.parametrize("profile", [None, {"mode": "encoder"}])
async def test_live_encoder_reading_and_next_poll_agree(hass, profile) -> None:
    """The captured 3987 reading should be 96.4%, rather than 57%."""
    coordinator = _coordinator(hass, profile=profile)
    coordinator.event_controller._handle_raw_frame(_raw_event(3987))

    assert coordinator.data.position == 96.4
    assert coordinator.display_position == 96.4
    assert coordinator.latest_event.position == 96.4
    assert coordinator.data.current_position == 100  # a live raw value is not a DMP read
    assert coordinator.data.registers["NHK/T4InstantPositionRaw"] == "3987"
    assert coordinator.data.registers["NHK/T4EncoderPosition"] == "96.4"
    assert resolve_position(coordinator.data, simulated=None, last_known=None).source == "t4_04_40"

    polled = coordinator._normalize_status_for_display(
        make_status(state="opening", position=96.4, current_position=3987, closed_position=0, open_position=4137)
    )
    assert polled.position == coordinator.latest_event.position
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("closed", "opened", "raw", "expected"),
    [(1000, 5000, 3000, 50.0), (5000, 1000, 3000, 50.0), (1000, 5000, 0, 0.0), (1000, 5000, 6000, 100.0)],
)
async def test_raw_encoder_bounds_support_offset_reverse_and_clamping(hass, closed, opened, raw, expected) -> None:
    coordinator = _coordinator(hass, closed=closed, opened=opened)
    coordinator.event_controller._handle_raw_frame(_raw_event(raw))
    assert coordinator.data.position == expected
    assert coordinator.latest_event.position == expected
    await coordinator.async_shutdown()


@pytest.mark.parametrize(("closed", "opened"), [(None, None), (0, None), (100, 100), (0, 100000)])
async def test_unusable_encoder_bounds_preserve_legacy_fallback(hass, closed, opened) -> None:
    coordinator = _coordinator(hass, closed=closed, opened=opened)
    coordinator.event_controller._handle_raw_frame(_raw_event(3500))
    assert coordinator.data.position == 50.0
    assert coordinator.latest_event.position == 50.0
    assert "NHK/T4EncoderPosition" not in coordinator.data.registers
    await coordinator.async_shutdown()


async def test_explicit_live_scalar_calibration_has_priority(hass) -> None:
    coordinator = _coordinator(hass, profile={
        "mode": "live_scalar",
        "bounds": {"live_scalar_closed_raw": 1000, "live_scalar_open_raw": 5000},
    })
    coordinator.event_controller._handle_raw_frame(_raw_event(3000))
    assert coordinator.data.position == 50.0
    assert coordinator.latest_event.position == 50.0
    assert resolve_position(coordinator.data, simulated=None, last_known=None).source == "t4_live_scalar_calibrated"
    assert "NHK/T4EncoderPosition" not in coordinator.data.registers

    coordinator.event_controller._apply_event(NiceEvent(
        kind=NiceEventKind.LIVE_STATUS, category=NiceEventCategory.STATE_CHANGE,
        received_at=datetime.now(UTC), state="opening", position=76.0,
        t4_payload_kind="04/40", t4_raw_position=76, t4_position_scale="percent",
    ))
    assert coordinator.data.position == 76.0
    assert coordinator.latest_event.position == 76.0
    assert resolve_position(coordinator.data, simulated=None, last_known=None).source == "t4_04_40"
    assert "NHK/T4CalibratedPosition" not in coordinator.data.registers
    await coordinator.async_shutdown()


async def test_high_resolution_live_value_survives_the_legacy_range(hass) -> None:
    event = parse_nhk_event_frame(_raw_event(9000))[0]
    assert event.position is None  # the old fallback cannot safely convert it
    assert event.t4_raw_position == 9000
    coordinator = _coordinator(hass, opened=10000)
    coordinator.event_controller._handle_raw_frame(_raw_event(9000))
    assert coordinator.data.position == 90.0
    assert coordinator.latest_event.position == 90.0
    await coordinator.async_shutdown()


async def test_stop_override_does_not_restore_an_old_live_position(hass) -> None:
    coordinator = _coordinator(hass)
    status = replace(coordinator.data, state="stopped", position=None, registers={
        "NHK/RecentStopOverride": "opening",
        "NHK/T4InstantPositionRaw": "3987",
        "NHK/T4InstantPositionScale": "raw_0_7000",
    })
    normalized = coordinator._normalize_status_for_display(status)
    assert normalized.position is None
    await coordinator.async_shutdown()
