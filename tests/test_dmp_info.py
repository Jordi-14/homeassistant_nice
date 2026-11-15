"""Tests for DMP INFO decoding, with INFO replies read from a ROBUSR10."""

import pytest

from custom_components.nice_bidiwifi.protocol.t4.dmp import (
    build_dmp_read_frame,
    decode_dmp_response,
)
from custom_components.nice_bidiwifi.protocol.t4.info import (
    DMP_INFO_FLAGS,
    DmpInfo,
    decode_dmp_info,
    scaled_value,
)


@pytest.mark.parametrize(
    ("hex_data", "expected"),
    [
        # 04/42 opening speed
        ("01000a01006401", DmpInfo(1, 0x00, 0x0A, 0x01, 0, 100, 1, None)),
        # 04/81 pause time
        ("01001101 00fa01", DmpInfo(1, 0x00, 0x11, 0x01, 0, 250, 1, None)),
        # 04/5A electric lock time: divide flag, scale 10
        ("010011110164 01000a", DmpInfo(1, 0x00, 0x11, 0x11, 1, 100, 1, 10)),
        # 04/B1 maintenance threshold: 32-bit
        ("04000401 00000000 fffffffe 0000000a", DmpInfo(4, 0x00, 0x04, 0x01, 0, 0xFFFFFFFE, 10, None)),
        # 04/86 reclose-after-photo mode: option list
        ("0100f561 02 1011", DmpInfo(1, 0x00, 0xF5, 0x61, options=(0x10, 0x11))),
        # 04/D1: list without bit 5 is a mask, not options
        ("0181e1410e 0f074300 0dffffff ff3c1fff 0100", DmpInfo(1, 0x81, 0xE1, 0x41)),
        # 04/03: indexed (bit 7), too short for min/max
        ("0903078103", DmpInfo(9, 0x03, 0x07, 0x81)),
    ],
)
def test_decode_dmp_info_robusr10_replies(hex_data, expected) -> None:
    """Test the INFO layout on real replies."""
    assert decode_dmp_info(bytes.fromhex(hex_data.replace(" ", ""))) == expected


def test_decode_dmp_info_rejects_a_truncated_option_list() -> None:
    """Review of #58: a list that declares 3 options but carries 2 is not used."""
    assert decode_dmp_info(bytes.fromhex("0100f561031011")) is None
    assert decode_dmp_info(bytes.fromhex("0100f56103101112")).options == (0x10, 0x11, 0x12)


def test_decode_dmp_info_rejects_short_data() -> None:
    """Test a reply without the 4-byte header."""
    assert decode_dmp_info(b"\x01\x00") is None
    assert decode_dmp_info(None) is None


def test_info_reply_frame_carries_the_info_data() -> None:
    """Test a full INFO reply frame of 04/86 (op 0x09) from a ROBUSR10."""
    plain = bytes.fromhex("5514509100030 80dc70486090700 0100f561021011 1a14".replace(" ", ""))
    parsed = decode_dmp_response(plain)

    assert (parsed.group, parsed.parameter, parsed.operation) == (0x04, 0x86, 0x09)
    assert decode_dmp_info(parsed.value).options == (0x10, 0x11)


def test_info_read_frame_uses_info_flags() -> None:
    """Test the INFO request differs from GET only in the flags byte."""
    get = build_dmp_read_frame(0x00, 0x03, 0x04, 0x86)
    info = build_dmp_read_frame(0x00, 0x03, 0x04, 0x86, flags=DMP_INFO_FLAGS)

    assert get[11] == 0x99
    assert info[11] == 0x89
    assert [i for i, (a, b) in enumerate(zip(get, info, strict=True)) if a != b] == [11, 14]


@pytest.mark.parametrize(
    ("raw", "hex_info", "expected"),
    [
        (20, "01001111016401000a", 2.0),  # 04/5A divide, scale 10
        (20, "01001121016401000a", 200),  # multiply
        (30, "01001101 00fa01", 30),  # no scale
        (20, "01001111016401", None),  # divide flag but the scale bytes are missing
        (17, "0100f561021011", 17),  # option list: never scaled
        (20, None, None),  # INFO unknown
    ],
)
def test_scaled_value(raw, hex_info, expected) -> None:
    """Test the app's scaling (StepCounterView.getScaledNum)."""
    info = decode_dmp_info(bytes.fromhex(hex_info.replace(" ", ""))) if hex_info else None
    assert scaled_value(raw, info) == expected
