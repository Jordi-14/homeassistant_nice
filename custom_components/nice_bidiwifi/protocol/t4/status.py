"""Pure conversion of parsed DMP registers into normalized status."""

from __future__ import annotations

from ...models.status import NiceStatus
from .bluebus import decode_bluebus_devices
from .dmp import (
    DmpResponse,
    dmp_ascii,
    dmp_bool,
    dmp_bytes,
    dmp_uint,
    status_from_register,
)
from .enums import RADIO_RECEIVER_TYPES
from .maneuvers import decode_last_maneuvers
from .options import (
    INPUT_FUNCTIONS,
    MAINTENANCE_MODES,
    OUTPUT_FUNCTIONS,
    STANDBY_MODES,
    COMMAND_MODES,
    option_name,
)


# Canonical field-to-register bindings shared by decoding and cache merging.
UINT_FIELDS = {
    "max_open_position": "04/12",
    "partial_open_1_position": "04/21",
    "partial_open_2_position": "04/22",
    "partial_open_3_position": "04/23",
    "opening_speed": "04/42",
    "closing_speed": "04/43",
    "opening_force": "04/4A",
    "closing_force": "04/4B",
    "pause_time": "04/81",
    "photo_close_time": "04/85",
    "photo_close_mode": "04/86",
    "always_close_time": "04/89",
    "always_close_mode": "04/8A",
    "maintenance_threshold": "04/B1",
    "maintenance_count": "04/B2",
    "total_maneuver_count": "04/B3",
    "transformation_ratio": "04/10",
    "min_closing_position": "04/13",
    "opening_slowdown_position": "04/24",
    "closing_slowdown_position": "04/25",
    "opening_slowdown_speed": "04/45",
    "closing_slowdown_speed": "04/46",
    "courtesy_light_time": "04/5B",
    "standby_time": "04/8D",
    "pre_flash_open_time": "04/95",
    "pre_flash_close_time": "04/99",
    "electric_lock_time": "04/5A",
    "suction_cup_time": "04/5C",
    "starting_torque_time": "04/91",
}
BOOL_FIELDS = {
    "input_1": "04/71",
    "input_2": "04/72",
    "input_3": "04/73",
    "input_4": "04/74",
    "auto_close": "04/80",
    "photo_close": "04/84",
    "always_close": "04/88",
    "standby": "04/8C",
    "pre_flash": "04/94",
    "key_lock": "04/9C",
    "automation_blocked": "04/9A",
    "slowdown": "04/A2",
    "starting_torque": "04/90",
    "slave_mode": "04/98",
}
OPTION_FIELDS = {
    "input_1_function": ("04/71", INPUT_FUNCTIONS),
    "input_2_function": ("04/72", INPUT_FUNCTIONS),
    "input_3_function": ("04/73", INPUT_FUNCTIONS),
    "input_4_function": ("04/74", INPUT_FUNCTIONS),
    "output_1_function": ("04/51", OUTPUT_FUNCTIONS),
    "output_2_function": ("04/52", OUTPUT_FUNCTIONS),
    "step_step_mode": ("04/61", COMMAND_MODES),
    "standby_mode": ("04/8E", STANDBY_MODES),
    "partial_open_mode": ("04/62", COMMAND_MODES),
    "open_mode": ("04/63", COMMAND_MODES),
    "close_mode": ("04/64", COMMAND_MODES),
    "stop_mode": ("04/65", COMMAND_MODES),
    "photo_closing_reaction": ("04/68", COMMAND_MODES),
    "photo_opening_reaction": ("04/69", COMMAND_MODES),
    "photo_3_reaction": ("04/6A", COMMAND_MODES),
    "alt_opening_reaction": ("04/6B", COMMAND_MODES),
    "alt_closing_reaction": ("04/6C", COMMAND_MODES),
    "obstacle_opening_reaction": ("04/78", COMMAND_MODES),
    "obstacle_closing_reaction": ("04/79", COMMAND_MODES),
    "maintenance_mode": ("04/B0", MAINTENANCE_MODES),
}
STATUS_FIELD_REGISTERS = {
    **UINT_FIELDS,
    **BOOL_FIELDS,
    **{k: v[0] for k, v in OPTION_FIELDS.items()},
    **{
        k: "04/D1"
        for k in (
            "limit_open",
            "limit_closed",
            "diagnostics_io_byte",
            "controller_errors",
        )
    },
    **{
        k: "04/D2"
        for k in ("motor_temperature", "service_voltage", "diagnostics_parameters")
    },
    "bluebus_devices": "04/D0",
    "last_maneuvers": "04/DA",
}

CORE_FIELD_REGISTERS = {"state": "04/01", "current_position": "04/11", "open_position": "04/18", "closed_position": "04/19"}


def fields_for_registers(registers) -> frozenset[str]:
    observed = {name for name, key in {**STATUS_FIELD_REGISTERS, **CORE_FIELD_REGISTERS}.items() if key in registers}
    if {"04/11", "04/18", "04/19"}.issubset(registers):
        observed.add("position")
    if any("@00.0A" in key for key in registers):
        observed.update({"oxi_detected", "oxi_product", "oxi_hardware_version", "oxi_firmware_version", "oxi_description", "oxi_receiver_type", "oxi_coding_type"})
    return frozenset(observed)

# 04/D1 byte 10, bits 0-6 = MyNice Pro T4InputOutputDiagnostic ids 37-43 (1 = ERR, "Err positions" @0x114b0).
CONTROLLER_ERROR_BITS = (
    "positions",
    "bluebus",
    "alt",
    "functions",
    "regulations",
    "map_1",
    "map_2",
)


def decode_controller_errors(data: bytes | None) -> dict[str, bool] | None:
    """Return the controller error flags of 04/D1, or None when not read."""
    if not data or len(data) < 11 or all(byte == 0xFF for byte in data):
        return None
    return {
        name: bool(data[10] >> bit & 1)
        for bit, name in enumerate(CONTROLLER_ERROR_BITS)
    }


# Below this raw value the field is not a real temperature.
MIN_RAW_TEMPERATURE = -50
# FF FF and 80 00 are "no value" fillers, not -1 or -32768
NO_TEMPERATURE = frozenset({b"\xff\xff", b"\x80\x00"})


def _motor_temperature(data: bytes | None) -> int | None:
    """04/D2 offset 14: signed 16-bit (MyNice Pro reads it with ldrsh), so frost stays visible."""
    if not data or len(data) < 16 or data[14:16] in NO_TEMPERATURE:
        return None
    raw = int.from_bytes(data[14:16], "big", signed=True)
    return raw - 9 if raw >= MIN_RAW_TEMPERATURE else None


def _coding_type(value: int | None) -> str | None:
    """RadioCfgCodingType.getCodingType: active-low bits, 7 = defined, 4 = FLO, 6 = HCS, 5 = FLOR."""
    if value is None or value > 0xFF or value & 0x80:
        return None
    for bit, name in ((0x10, "flo"), (0x40, "hcs"), (0x20, "flor")):
        if not value & bit:
            return name
    return None


def _receiver_type(code: int | None) -> str | None:
    """Return the OXI receiver type name, or code_<n> for an unknown code."""
    return None if code is None else RADIO_RECEIVER_TYPES.get(code, f"code_{code}")


def status_from_dmp_registers(
    registers: dict[str, DmpResponse],
) -> NiceStatus:
    """Build normalized status from parsed controller and OXI registers."""
    state = status_from_register(registers.get("04/01"))
    current = dmp_uint(registers.get("04/11"))
    opened = dmp_uint(registers.get("04/18"))
    closed = dmp_uint(registers.get("04/19"))
    position: float | None = None
    if (
        current is not None
        and opened is not None
        and closed is not None
        and opened != closed
    ):
        position = max(0.0, min(100.0, ((current - closed) / (opened - closed)) * 100))

    diagnostics_io = dmp_bytes(registers.get("04/D1"))
    io_byte = (
        diagnostics_io[2]
        if diagnostics_io and len(diagnostics_io) >= 3
        else diagnostics_io[0]
        if diagnostics_io
        else None
    )
    diagnostic_parameters = dmp_bytes(registers.get("04/D2"))
    if diagnostic_parameters and all(
        value in {0x00, 0xFF} for value in diagnostic_parameters
    ):
        diagnostic_parameters = None

    values = {name: dmp_uint(registers.get(reg)) for name, reg in UINT_FIELDS.items()}
    values.update(
        {name: dmp_bool(registers.get(reg)) for name, reg in BOOL_FIELDS.items()}
    )
    values.update(
        {
            name: option_name(table, dmp_uint(registers.get(reg)))
            for name, (reg, table) in OPTION_FIELDS.items()
        }
    )
    return NiceStatus(
        observed_fields=fields_for_registers(registers),
        fresh_registers=frozenset(registers),
        **values,
        state=state,
        position=round(position, 1) if position is not None else None,
        current_position=current,
        closed_position=closed,
        open_position=opened,
        registers={
            key: value.value.hex(" ") if value.value is not None else ""
            for key, value in registers.items()
        },
        limit_open=bool(io_byte & 0x01) if io_byte is not None else None,
        limit_closed=bool(io_byte & 0x02) if io_byte is not None else None,
        diagnostics_io_byte=io_byte,
        bluebus_devices=decode_bluebus_devices(dmp_bytes(registers.get("04/D0"))),
        controller_errors=decode_controller_errors(diagnostics_io),
        last_maneuvers=decode_last_maneuvers(dmp_bytes(registers.get("04/DA"))),
        motor_temperature=_motor_temperature(diagnostic_parameters),
        service_voltage=(
            int.from_bytes(diagnostic_parameters[8:10], "big")
            if diagnostic_parameters
            and len(diagnostic_parameters) > 9
            and diagnostic_parameters[8] < 0x80  # vendor "not available"
            else None
        ),
        diagnostics_parameters=(
            diagnostic_parameters.hex(" ")
            if diagnostic_parameters is not None
            else None
        ),
        oxi_detected=any(key.endswith("@00.0A") for key in registers),
        oxi_product=dmp_ascii(registers.get("00/09@00.0A")),
        oxi_hardware_version=dmp_ascii(registers.get("00/0A@00.0A")),
        oxi_firmware_version=dmp_ascii(registers.get("00/0B@00.0A")),
        oxi_description=dmp_ascii(registers.get("00/0C@00.0A")),
        oxi_receiver_type=_receiver_type(dmp_uint(registers.get("0A/00@00.0A"))),
        oxi_coding_type=_coding_type(dmp_uint(registers.get("0A/41@00.0A"))),
    )
