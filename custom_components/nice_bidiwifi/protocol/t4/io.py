"""Decode 04/D1 (inputs and outputs) and 04/D2 (hardware values) like MyNice Pro.

04/D1: T4InputOutputDiagnostic_init (libnative-lib.so @0xf7d4). Each item is one bit of the GET reply; the
same bit of the INFO list says whether the controller has it ("installed"). Names follow the app rows.
04/D2: HardwareDiagnostic::init (@0x217b0). Big-endian u16 fields; the INFO list gives per field
bit 15 = available and bits 0-14 = M; percent fields are ((value * 100) & 0xFFFC) // M (diag1DgxCalcPCT).
"""

from __future__ import annotations

from .info import DmpInfo

# (GET byte, bit, name); True = bit set. App ids in comments.
IO_BITS: tuple[tuple[int, int, str], ...] = (
    (0, 0, "alt_ok"),  # 1 Ing Alt: 0 = KO, 1 = OK
    (0, 1, "input_1"),  # 2 Ing1 PP
    (0, 2, "input_2"),  # 3 Ing2 AP
    (0, 3, "input_3"),  # 4 Ing3 CH
    (0, 4, "loop_1"),  # 5
    (0, 5, "loop_2"),  # 6
    (1, 0, "button_1"),  # 7-9
    (1, 1, "button_2"),
    (1, 2, "button_3"),
    (2, 0, "limit_open_m1"),  # 10-13 Fca/Fcc M1/M2
    (2, 1, "limit_close_m1"),
    (2, 2, "limit_open_m2"),
    (2, 3, "limit_close_m2"),
    (2, 4, "unlocked_m1"),  # 14-15
    (2, 5, "unlocked_m2"),
    (3, 0, "encoder_m1_fault"),  # 18-19 State Enc: 0 = OK, 1 = KO
    (3, 1, "encoder_m2_fault"),
    (3, 2, "encoder_input_m1"),  # 20-21
    (3, 3, "encoder_input_m2"),
    (4, 0, "motor_m1"),  # 22-29 outputs
    (4, 1, "motor_m2"),
    (4, 2, "output_1"),
    (4, 3, "output_2"),
    (4, 4, "output_3"),
    (4, 5, "fan"),
    (4, 6, "green_light"),
    (4, 7, "red_light"),
    (9, 0, "thermal"),  # 31-36
    (9, 1, "heating"),
    (9, 2, "standby"),
    (9, 3, "battery"),
    (9, 5, "automatic_opening"),
    (12, 0, "overload_output_1"),  # 45-51
    (12, 1, "overload_output_2"),
    (12, 2, "overload_output_3"),
    (12, 4, "overtravel_low_m1"),
    (12, 5, "overtravel_high_m1"),
    (12, 6, "overtravel_low_m2"),
    (12, 7, "overtravel_high_m2"),
)
# 04/D1 byte 11 & 3 (app id 44 "State limiter maneuver", Diagnostic1 SLM_*)
LIMITER_STATES = ("ok", "threshold_1", "threshold_2", "motor_alarm")

# 04/D2 fields at offset 2 * index; "%" fields are scaled with M from INFO
HARDWARE_FIELDS: tuple[tuple[str, bool], ...] = (
    ("work_time_left", False),
    ("pause_time_left", False),
    ("courtesy_light_time_left", False),
    ("bus_current", True),
    ("service_voltage", False),
    ("torque_m1", True),
    ("torque_m2", True),
    ("temperature_raw", False),
    ("voltage_m1", True),
    ("voltage_m2", True),
    ("speed_m1", True),
    ("speed_m2", True),
)


def _present(info: DmpInfo | None, byte: int, bit: int) -> bool:
    """Return true when INFO marks the item as installed (no INFO: show everything)."""
    if info is None or info.values is None:
        return True
    return byte < len(info.values) and bool(info.values[byte] >> bit & 1)


def decode_io_diagnostic(data: bytes | None, info: DmpInfo | None) -> dict[str, bool | str | int] | None:
    """Return the installed 04/D1 items by name, or None without a full reply."""
    if not data or len(data) < 13 or all(b == 0xFF for b in data):
        return None
    out: dict[str, bool | str | int] = {
        name: bool(data[byte] >> bit & 1)
        for byte, bit, name in IO_BITS
        if _present(info, byte, bit)
    }
    if _present(info, 2, 6):  # 16 Selection direction: SX when set (seen 0x40 on a left-hand ROBUSR10)
        out["direction"] = "left" if data[2] & 0x40 else "right"
    if _present(info, 2, 7):  # 17 Selection engine: sign of byte 2
        out["selected_motor"] = "m1" if data[2] & 0x80 else "m2"
    if _present(info, 11, 0):
        out["force_limiter_state"] = LIMITER_STATES[data[11] & 0x03]
    return out


def decode_hardware_diagnostic(data: bytes | None, info: DmpInfo | None) -> dict[str, int] | None:
    """Return the available 04/D2 fields by name; percent fields need INFO, others are raw values."""
    if not data or len(data) < 2 * len(HARDWARE_FIELDS):
        return None
    out: dict[str, int] = {}
    for index, (name, percent) in enumerate(HARDWARE_FIELDS):
        scale = info.values[index] if info and info.values and index < len(info.values) else None
        if scale is not None and not scale & 0x8000:
            continue  # not available on this controller
        if data[2 * index] & 0x80 and name != "temperature_raw":
            continue  # the app shows "-" (the temperature is signed)
        value = int.from_bytes(
            data[2 * index : 2 * index + 2], "big", signed=name == "temperature_raw"
        )
        if percent:
            if not scale or not scale & 0x7FFF:
                continue
            value = ((value * 100) & 0xFFFC) // (scale & 0x7FFF)
        out[name] = value
    return out
