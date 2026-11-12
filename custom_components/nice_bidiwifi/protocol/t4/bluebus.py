"""Decode of 04/D0 (CTRL_DIAGNOSTICS_BLUEBUS_DEVICES)."""

from __future__ import annotations

from .enums import BLUEBUS_DEVICE_STATUS

# name, class, installed byte, installed mask, index (cmd) or ON mask (ft)
_SLOTS: tuple[tuple[str, str, int, int, int], ...] = (
    ("photo", "photo", 2, 0x01, 0),
    ("photo_ii", "photo", 2, 0x02, 0),
    ("photo_1", "photo", 2, 0x04, 0),
    ("photo_1_ii", "photo", 2, 0x08, 0),
    ("photo_2", "photo", 2, 0x10, 0),
    ("photo_2_ii", "photo", 2, 0x20, 0),
    ("photo_3", "photo", 2, 0x40, 0),
    ("fta_photo", "ft", 3, 0x01, 0x03),
    ("ftd_photo", "ft", 3, 0x02, 0x0C),
    ("ftb_photo", "ft", 3, 0x04, 0x30),
    ("ftc_photo", "ft", 3, 0x08, 0xC0),
    ("cmd_1", "cmd", 4, 0x01, 0),
    ("cmd_2", "cmd", 4, 0x02, 1),
    ("cmd_3", "cmd", 4, 0x04, 2),
    ("cmd_4", "cmd", 4, 0x08, 3),
    ("photo_open", "open", 4, 0x20, 0),
    ("photo_open_ii", "open", 4, 0x40, 0),
    ("s_sl", "ms", 5, 0x01, 0),
)
_CODES = {name: code for code, name in BLUEBUS_DEVICE_STATUS.items()}


def _status_code(b: bytes, kind: str, mask: int, extra: int) -> int:
    """Return the status code of one installed device."""
    if kind == "open":  # status bits for PHOTO OPEN are not known yet
        return -1
    if b[1] & 0x10:  # BlueBus line error
        return _CODES["stand_by"]
    if kind == "photo":
        if b[6] & mask:
            return _CODES["not_working"]
        if b[10] & mask and b[0] & 0x08:
            return _CODES["in_error"]
        return _CODES["on"] if b[11] & mask and b[19] & mask else _CODES["off"]
    if kind == "ft":
        if b[7] & mask:
            return _CODES["not_working"]
        return _CODES["on"] if b[20] & extra else _CODES["off"]
    if kind == "cmd":
        if b[8] & mask:
            return _CODES["not_working"]
        v = b[21 + extra]
        return _CODES["off"] if not v & 0x1F and v < 0x40 else _CODES["on"]
    if b[9] & mask:
        return _CODES["not_working"]
    return _CODES["sl_stopped"] + (b[17] & 0x07)


def decode_bluebus_devices(data: bytes | None) -> dict[str, str] | None:
    """Return the status of each installed BlueBus device, or None."""
    if data is None or len(data) <= 24:
        return None
    return {
        name: BLUEBUS_DEVICE_STATUS[_status_code(data, kind, mask, extra)]
        for name, kind, byte, mask, extra in _SLOTS
        if data[byte] & mask
    }
