"""Decode DMP INFO replies (value format of one register).

Layout from MyNice Pro decompileInfo (libnative-lib.so @0x22ba8), checked against INFO replies of a ROBUSR10:
byte 0 = value size (bits 0-6) and little-endian flag (bit 7), byte 1 = data format, byte 2 = function,
byte 3 = flags. Flag bit 7 adds an index of (flags & 3) bytes. Flag bit 6 = a list follows (count, then one
byte per entry); with bit 5 the list holds the allowed option codes. Without bit 6: min, max, step
(size bytes each) and an optional 2-byte scale.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

DMP_INFO_FLAGS = 0x89  # req | ack | fin (GET is 0x99: also get)


@dataclass(frozen=True, slots=True)
class DmpInfo:
    """Value format of one DMP register."""

    size: int
    data_format: int
    function: int
    flags: int
    minimum: int | None = None
    maximum: int | None = None
    step: int | None = None
    scale: int | None = None
    options: tuple[int, ...] | None = None


def decode_dmp_info(data: bytes | None) -> DmpInfo | None:
    """Decode the data of an INFO reply, or return None when it is too short."""
    if not data or len(data) < 4:
        return None
    size = data[0] & 0x7F
    order = "little" if data[0] & 0x80 else "big"
    flags = data[3]
    offset = 4 + (flags & 0x03 if flags & 0x80 else 0)
    info = DmpInfo(size=size, data_format=data[1], function=data[2], flags=flags)
    if flags & 0x40:
        if not flags & 0x20 or len(data) <= offset:
            return info
        count = data[offset]
        options = tuple(data[offset + 1 : offset + 1 + count])
        if len(options) != count:
            # truncated list: never restrict a control with incomplete metadata
            return None
        return replace(info, options=options or None)
    if size < 1 or len(data) < offset + 3 * size:
        return info

    def number(index: int) -> int:
        start = offset + index * size
        return int.from_bytes(data[start : start + size], order)

    scale_at = offset + 3 * size
    scale = int.from_bytes(data[scale_at : scale_at + 2], "big") if len(data) >= scale_at + 2 else None
    return replace(info, minimum=number(0), maximum=number(1), step=number(2), scale=scale)
