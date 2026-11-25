"""Read-only CTRL_AUTOMATION_STATUS layout audited against the supplied ARM64 library.

T4AutomationState_init @0x141a8 and its Java wrapper define byte fields, not
16-bit integers. These are the app's reported interpretations, not proof of a
physical cause, installed hardware or permission to start a procedure.
"""

from __future__ import annotations

from .enums import ADVANCED_DIAGNOSTIC, AUTOMATION_STATUS_DIAGNOSTICS, EVT_CHANGE_STATE

FLAG_FIELDS = (
    "search_devices", "search_positions", "first_maneuvers",
    "no_programming", "no_eeprom_errors",
)


def decode_automation_state(data: bytes | None) -> dict | None:
    """Decode only present bytes; unknown codes and future layouts stay unknown."""
    if not data or len(data) > 8 or all(value == 0xFF for value in data):
        return None
    result = {
        "status_code": data[0],
        "reported_status": EVT_CHANGE_STATE.get(data[0]),
    }
    if len(data) > 1:
        flags = data[1]
        result["flags_raw"] = flags
        # An all-one field occurs on working controllers. Keep it raw rather
        # than present simultaneous search flags as verified procedure state.
        if flags != 0xFF:
            result.update({name: bool(flags & (1 << bit)) for bit, name in enumerate(FLAG_FIELDS)})
            # Java explicitly compares native fields 7/8/9 to "n", not "y".
            result.update(
                psu_detected=not bool(flags & 0x20),
                built_in_radio=not bool(flags & 0x40),
                modular_controller=not bool(flags & 0x80),
            )
    if len(data) > 2:
        result.update(
            diagnostic_code=data[2],
            reported_diagnostic=AUTOMATION_STATUS_DIAGNOSTICS.get(data[2]),
        )
    if len(data) > 3:
        result.update(
            advanced_diagnostic_code=data[3],
            reported_advanced_diagnostic=ADVANCED_DIAGNOSTIC.get(data[3]),
        )
    for offset, name in enumerate(("installed_modules_mask", "modules_error_mask", "modules_boot_mask"), 4):
        if len(data) > offset:
            result[name] = data[offset]
    # Native emits byte 7, but the supplied Java consumer does not use it.
    if len(data) == 8:
        result["uninterpreted_tail_byte"] = data[7]
    return result
