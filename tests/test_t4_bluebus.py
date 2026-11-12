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


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_data(b2=1, b1=0x10), {"photo": "stand_by"}),
        (_data(b2=1, b6=1), {"photo": "not_working"}),
        (_data(b2=1, b10=1, b0=0x08), {"photo": "in_error"}),
        (_data(b2=1), {"photo": "off"}),
        (_data(b4=0x20, b6=0x20, b10=0x20, b0=0x08), {"photo_open": "undefined"}),
        (_data(b4=0x40, b1=0x10), {"photo_open_ii": "undefined"}),
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
    assert enums.EVT_STATE_CAUSE[17] == "photo_open"
    assert enums.EVT_CHANGE_STATE[129] == "opening_for_photo_intervent"
    assert enums.ADVANCED_DIAGNOSTIC[48] == "motor_disconnected"


def test_decode_last_maneuvers() -> None:
    assert decode_last_maneuvers(bytes([0, 3, 10, 0xFF, 0, 0, 0, 2])) == (
        "ok", "obstacle_detected", "detected_obstacle_by_encoder", "undefined",
        "ok", "ok", "ok", "photo_intervention",
    )
    assert decode_last_maneuvers(bytes(7)) is None
    assert decode_last_maneuvers(None) is None
    assert decode_last_maneuvers(bytes([3] * 8 + [3])) == ("obstacle_detected",) * 8
