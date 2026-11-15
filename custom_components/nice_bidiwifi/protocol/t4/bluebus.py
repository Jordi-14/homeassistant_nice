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
    ("photo_open", "cmd", 4, 0x20, 0),  # same status byte 0x15 as CMD 1 (@0xf380)
    ("photo_open_ii", "cmd", 4, 0x40, 0),  # @0xf41c
    ("s_sl", "ms", 5, 0x01, 0),
)


def _status(b: bytes, kind: str, mask: int, extra: int) -> str:
    """Return the status name of one installed device."""
    if b[1] & 0x10:  # BlueBus line error
        return "stand_by"
    if kind == "photo":
        if b[12] & 0x80:  # setDiagPhotoStatus @0x211f8: signed byte 12 < 0
            return "stand_by"
        if b[6] & mask:
            return "not_working"
        if b[10] & mask and b[0] & 0x08:
            return "in_error"
        return "on" if b[11] & mask and b[19] & mask else "off"
    if kind == "ft":
        if b[7] & mask:
            return "not_working"
        return "on" if b[20] & extra else "off"
    if kind == "cmd":
        if b[8] & mask:
            return "not_working"
        v = b[21 + extra]
        return "off" if not v & 0x1F and v < 0x40 else "on"
    if b[9] & mask:
        return "not_working"
    return BLUEBUS_DEVICE_STATUS[6 + (b[17] & 0x07)]


def decode_bluebus_devices(data: bytes | None) -> dict[str, str] | None:
    """Return the status of each installed BlueBus device, or None."""
    if data is None or len(data) <= 24 or all(b == 0xFF for b in data):  # all 0xFF = no data
        return None
    return {
        name: _status(data, kind, mask, extra)
        for name, kind, byte, mask, extra in _SLOTS
        if data[byte] & mask
    }


# 04/D0 byte 12 bits 0-3: the gate state as the controller puts it on the bus; 0 = stopped between the ends.
# SEEN on a ROBUSR10 (2026-11-15): every fresh read matched 04/01, also closed after 330 s at rest. The app uses
# only bit 7 (stand-by, see _status). Byte 0 bit 0 was 1 at rest and 0 in every sample while moving.
_GATE_STATES = {0x00: "stopped", 0x01: "closed", 0x02: "open", 0x04: "closing", 0x08: "opening"}


def decode_bluebus_gate(data: bytes | None, info: object = None) -> dict[str, str | bool | int] | None:
    """Return the 04/D0 gate state, moving flag and stand-by flag, or None without a full reply.

    It repeats 04/01 on a healthy controller; a difference between the two shows a controller that
    reports one state over BusT4 and another to its BlueBus devices.
    """
    if data is None or len(data) <= 24 or all(b == 0xFF for b in data):
        return None
    code = data[12] & 0x0F
    return {
        "state": _GATE_STATES.get(code, f"code_{code}"),
        "moving": not data[0] & 0x01,
        "stand_by": bool(data[12] & 0x80),
        "raw": data[12],
    }
