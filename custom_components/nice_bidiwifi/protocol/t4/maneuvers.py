"""Decode of 04/DA (CTRL_LOG_8_MANEUVERS)."""

from __future__ import annotations

from .enums import AUTOMATION_STATUS_DIAGNOSTICS


def decode_last_maneuvers(data: bytes | None) -> tuple[str, ...] | None:
    """Return the outcome names of the 8 maneuver slots, or None."""
    if data is None or len(data) < 8 or data[:8] == b"\xff" * 8:  # all 0xFF = no data
        return None
    return tuple(AUTOMATION_STATUS_DIAGNOSTICS.get(b, "undefined") for b in data[:8])
