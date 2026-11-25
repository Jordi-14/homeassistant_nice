"""Decode of 04/DA (CTRL_LOG_8_MANEUVERS)."""

from __future__ import annotations

from .enums import AUTOMATION_STATUS_DIAGNOSTICS


def decode_last_maneuvers(data: bytes | None) -> tuple[str, ...] | None:
    """Return the outcome names of the 8 maneuver slots, or None."""
    if data is None or len(data) < 8 or data[:8] == b"\xff" * 8:  # all 0xFF = no data
        return None
    return tuple(AUTOMATION_STATUS_DIAGNOSTICS.get(b, "undefined") for b in data[:8])


def maneuver_evidence(data: bytes | None, *, stale=False) -> dict:
    """Vendor labels are reported slots, not eight independently proven moves."""
    names = decode_last_maneuvers(data)
    if names is None:
        return {"state": "unavailable", "reason": "missing_or_truncated_history"}
    all_zero = data[:8] == bytes(8)
    return {
        "state": "stale"
        if stale
        else "all_zero_unverified"
        if all_zero
        else "reported_slots",
        "slots": [
            {"index": i, "code": data[i], "reported_outcome": name}
            for i, name in enumerate(names)
        ],
        "fresh_maneuver_count": None,
        "confirmed_success_count": None,
        "reported_failure_count": None
        if all_zero or stale
        else sum(name not in {"ok", "undefined"} for name in names),
        "slot_order": "vendor_index_order_newest_unknown",
        "decoder_basis": "native_0x1609c_and_Java_consumer_static_audit",
        "confidence": "reported_codes_firmware_validity_unverified",
        "physical_cause": "unknown",
    }
