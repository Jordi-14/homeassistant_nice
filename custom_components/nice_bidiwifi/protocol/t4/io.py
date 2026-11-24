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
    # 45 seen 1 in all 489 samples of a ROBUSR10 (2026-11-15), with output 1 (flashing light) on and off,
    # so it is not "output active"; the app shows only ON/OFF. Meaning unknown.
    (12, 0, "overload_output_1"),  # 45-51
    (12, 1, "overload_output_2"),
    (12, 2, "overload_output_3"),
    (12, 4, "overtravel_low_m1"),
    (12, 5, "overtravel_high_m1"),
    (12, 6, "overtravel_low_m2"),
    (12, 7, "overtravel_high_m2"),
)

# 04/D1 byte 5 (app id 30 "State Alt", table @0x8370, Diagnostic1 SA_*): type of the STOP/Alt input
ALT_STATES = ("not_set", "b1", "b2", "nc", "no", "out_of_range", "sensitive_edge")
# Seen level_2 in every sample (rest, motion, photocell reversal) of a ROBUSR10: likely the set level, not an alarm.
# 04/D1 byte 11 & 3 (app id 44 "State limiter maneuver", table @0x838c, Diagnostic1 SLM_*: OK, L1, L2, Motor Alarm)
LIMITER_STATES = ("ok", "level_1", "level_2", "motor_alarm")

# Seen on a ROBUSR10 during motion (2026-11-15, ~1 sample/s): work_time_left 240 at the start, -1 per second, 0
# at the stop; pause_time_left 30 after a photocell reversal; courtesy light 60 after a stop, counting down;
# speed_m1 95-99 % opening and 71-84 % closing with settings 80/70, so a measured value, not the set point;
# 17-19 % in the slow zone; torque_m1 50-60 % at the start, 20-45 % running; service voltage 34 at rest, 30-33 moving.
# 04/D2 fields at offset 2 * index; "%" fields are scaled with M from INFO. None = already a sensor
# (service voltage, motor temperature).
HARDWARE_FIELDS: tuple[tuple[str | None, bool], ...] = (
    ("work_time_left", False),
    ("pause_time_left", False),
    ("courtesy_light_time_left", False),
    ("bus_current", True),
    (None, False),
    ("torque_m1", True),
    ("torque_m2", True),
    (None, False),
    ("voltage_m1", True),
    ("voltage_m2", True),
    ("speed_m1", True),
    ("speed_m2", True),
)


def _present(info: DmpInfo | None, byte: int, bit: int) -> bool:
    """Return true when INFO marks the item as installed (no usable INFO: show everything)."""
    if info is None or info.values is None or info.size != 1:
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
    if _present(info, 9, 4):  # 35 Freq: '5' (50 Hz) when clear, '6' (60 Hz) when set (@0x11344)
        out["mains_frequency"] = 60 if data[9] & 0x10 else 50
    if _present(info, 5, 0) and data[5] < len(ALT_STATES):  # 30 State Alt: whole byte, >= 7 unknown
        out["alt_input_type"] = ALT_STATES[data[5]]
    if _present(info, 6, 0):  # 52 Ing. Radio, 53 Ing. T4 mode I: active channels 1-4
        out["radio_inputs"] = [n + 1 for n in range(4) if data[6] >> n & 1]
    if _present(info, 7, 0):
        out["t4_mode_1_inputs"] = [n + 1 for n in range(4) if data[7] >> n & 1]
    if _present(info, 8, 0):  # 54 Ing. T4 mode II: a number ("%02d")
        out["t4_mode_2"] = data[8]
    if _present(info, 11, 0):  # 44 State limiter maneuver
        out["force_limiter_state"] = LIMITER_STATES[data[11] & 0x03]
    return out


def dgx_percent(value: int, full_scale: int) -> int:
    """MyNice Pro diag1DgxCalcPCT: 100 * value / full_scale with the app's 16-bit and 0-255 limits."""
    if full_scale == 0:
        return 0
    shift = 0
    while full_scale >> shift > 650:
        shift += 1
    return (((value >> shift) * 100) & 0xFFFC) // (full_scale >> shift) & 0xFF


def decode_hardware_diagnostic(data: bytes | None, info: DmpInfo | None, *, include_primary: bool = False) -> dict[str, int] | None:
    """Return the available 04/D2 fields by name; percent fields need INFO, others are raw values."""
    if not data or len(data) < 2 or all(b in (0x00, 0xFF) for b in data):
        return None
    out: dict[str, int] = {}
    for index, (name, percent) in enumerate(HARDWARE_FIELDS[: len(data) // 2]):
        if name is None:
            if not include_primary:
                continue
            name = {4: "service_voltage", 7: "motor_temperature"}.get(index)
            if name is None:
                continue
        scale = info.values[index] if info and info.values and index < len(info.values) else None
        if scale is not None and not scale & 0x8000:
            continue  # not available on this controller
        if data[2 * index] & 0x80:
            continue  # the app shows "-"
        value = int.from_bytes(data[2 * index : 2 * index + 2], "big")
        if percent:
            if scale is None:
                continue  # the full scale comes only from INFO
            value = dgx_percent(value, scale & 0x7FFF)
        out[name] = value
    return out
