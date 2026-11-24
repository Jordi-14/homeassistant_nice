"""Reviewed u32 counter decoding, shared by fresh and cached diagnostic views."""

from __future__ import annotations

from .info import DmpInfo, scaled_value


def decode_counter(
    raw: bytes | None, info: DmpInfo | None, *, fallback: int | None = None
):
    if info is not None and (
        info.indexed
        or info.size != 4
        or info.data_format not in {0, 0x25}
        or info.byte_order not in {"big", "little"}
        or not info.flags & 0x40
        and info.flags & 0x30 == 0x30
        or info.values is not None
        and info.scalar_options is None
    ):
        return None, None
    if raw is not None:
        if len(raw) != 4 or raw == b"\xff" * 4:
            return None, None
        value = int.from_bytes(raw, info.byte_order if info else "big")
    elif (
        isinstance(fallback, int)
        and not isinstance(fallback, bool)
        and 0 <= fallback < 0xFFFFFFFF
    ):
        value = fallback
    else:
        return None, None
    return value, scaled_value(value, info) if info else value
