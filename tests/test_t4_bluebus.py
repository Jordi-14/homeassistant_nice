"""Tests for T4 BlueBus decoding and enum tables."""

from __future__ import annotations

import pytest

from custom_components.nice_bidiwifi.protocol.t4 import enums
from custom_components.nice_bidiwifi.protocol.t4.bluebus import decode_bluebus_devices
from custom_components.nice_bidiwifi.protocol.t4.maneuvers import decode_last_maneuvers

SAMPLE = bytes.fromhex("1100810000000000000000" "7f000000000000000100c00000000000")


def _data(**bytes_: int) -> bytes:
    data = bytearray(27)
    for key, value in bytes_.items():
        data[int(key[1:])] = value
    return bytes(data)


def test_real_sample() -> None:
    assert decode_bluebus_devices(SAMPLE) == {"photo": "on"}


def test_short_or_missing() -> None:
    assert decode_bluebus_devices(SAMPLE[:24]) is None
    assert decode_bluebus_devices(None) is None
    assert decode_bluebus_devices(b"\xff" * 27) is None


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_data(b2=1, b1=0x10), {"photo": "stand_by"}),
        (_data(b2=1, b6=1), {"photo": "not_working"}),
        (_data(b2=1, b10=1, b0=0x08), {"photo": "in_error"}),
        (_data(b2=1), {"photo": "off"}),
        # PHOTO OPEN / II: installed byte 4 bits 5-6, not-working mask on byte 8, status byte 0x15 (@0xf31c, @0xf3b8)
        (_data(b4=0x60, b8=0x20), {"photo_open": "not_working", "photo_open_ii": "off"}),
        (_data(b2=1, b12=0x80), {"photo": "stand_by"}),  # signed byte 12 < 0 (@0x211f8)
        (_data(b4=1, b21=0), {"cmd_1": "off"}),
        (_data(b4=1, b21=1), {"cmd_1": "on"}),
        (_data(b4=1, b21=0x40), {"cmd_1": "on"}),
        (_data(b4=1, b8=1), {"cmd_1": "not_working"}),
        (_data(b3=1, b20=3), {"fta_photo": "on"}),
        (_data(b3=1, b20=0), {"fta_photo": "off"}),
        (_data(b5=1, b17=0), {"s_sl": "sl_stopped"}),
        (_data(b5=1, b17=7), {"s_sl": "sl_anomaly"}),
    ],
)
def test_device_status(data: bytes, expected: dict[str, str]) -> None:
    assert decode_bluebus_devices(data) == expected


def test_enum_tables() -> None:
    for table in (
        enums.EVT_CHANGE_STATE,
        enums.EVT_STATE_CAUSE,
        enums.AUTOMATION_STATUS_DIAGNOSTICS,
        enums.ADVANCED_DIAGNOSTIC,
        enums.EVT_MOV_COMMAND,
        enums.EVT_ERROR,
        enums.LOG_REPLY_STATUS,
        enums.BLUEBUS_DEVICE_STATUS,
    ):
        assert len(set(table.values())) == len(table)
    assert enums.EVT_ERROR[2] == "auth_failed"
    assert enums.EVT_ERROR[99] == "max_number_rules_reached"
    assert enums.EVT_STATE_CAUSE[17] == "photo_open"
    assert enums.EVT_CHANGE_STATE[129] == "opening_for_photo_intervent"
    assert enums.ADVANCED_DIAGNOSTIC[48] == "motor_disconnected"


def test_decode_last_maneuvers() -> None:
    assert decode_last_maneuvers(bytes([0, 3, 10, 0xFF, 0, 0, 0, 2])) == (
        "ok", "obstacle_detected", "detected_obstacle_by_encoder", "undefined",
        "ok", "ok", "ok", "photo_intervention",
    )
    assert decode_last_maneuvers(bytes(7)) is None
    assert decode_last_maneuvers(b"\xff" * 8) is None
    assert decode_last_maneuvers(b"\x00\xff\x03\x00\x00\x00\x00\x00") == (
        "ok", "undefined", "obstacle_detected", "ok", "ok", "ok", "ok", "ok",
    )
    assert decode_last_maneuvers(None) is None
    assert decode_last_maneuvers(bytes([3] * 8 + [3])) == ("obstacle_detected",) * 8


@pytest.mark.parametrize(
    ("byte0", "byte12", "state", "moving"),
    [
        # ROBUSR10 replies, 2026-11-15 (cycles 11-13): byte 0 / byte 12 with 04/01 in the same poll
        (0x11, 0x01, "closed", False),
        (0x11, 0x02, "open", False),
        (0x10, 0x04, "closing", True),
        (0x10, 0x08, "opening", True),
        (0x11, 0x00, "stopped", False),
        (0x11, 0x03, "code_3", False),
    ],
)
def test_gate_state(byte0: int, byte12: int, state: str, moving: bool) -> None:
    from custom_components.nice_bidiwifi.protocol.t4.bluebus import decode_bluebus_gate

    decoded = decode_bluebus_gate(_data(b0=byte0, b12=byte12))
    assert decoded == {"state": state, "moving": moving, "stand_by": False, "raw": byte12}


def test_gate_state_stand_by_and_missing() -> None:
    from custom_components.nice_bidiwifi.protocol.t4.bluebus import decode_bluebus_gate

    closed = bytes.fromhex("1100810000000000000000" "7f010000000000000100c00000000000")  # cycle 12, closed
    assert decode_bluebus_gate(closed)["state"] == "closed"
    assert decode_bluebus_devices(closed) == {"photo": "on"}
    assert decode_bluebus_gate(_data(b0=0x11, b12=0x81))["stand_by"] is True
    assert decode_bluebus_gate(SAMPLE[:24]) is None
    assert decode_bluebus_gate(b"\xff" * 27) is None
