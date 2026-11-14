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
from .maneuvers import decode_last_maneuvers
from .options import (
    INPUT_FUNCTIONS,
    MAINTENANCE_MODES,
    OUTPUT_FUNCTIONS,
    STANDBY_MODES,
    STEP_STEP_MODES,
    option_name,
)


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


def status_from_dmp_registers(
    registers: dict[str, DmpResponse],
) -> NiceStatus:
    """Build normalized status from parsed controller and OXI registers."""
    state = status_from_register(registers.get("04/01"))
    current = dmp_uint(registers.get("04/11"))
    opened = dmp_uint(registers.get("04/18"))
    closed = dmp_uint(registers.get("04/19"))
    position: float | None = None
    if current is not None and opened is not None and closed is not None and opened != closed:
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
        value in {0x00, 0xFF}
        for value in diagnostic_parameters
    ):
        diagnostic_parameters = None

    return NiceStatus(
        state=state,
        position=round(position, 1) if position is not None else None,
        current_position=current,
        closed_position=closed,
        open_position=opened,
        registers={
            key: value.value.hex(" ") if value.value is not None else ""
            for key, value in registers.items()
        },
        max_open_position=dmp_uint(registers.get("04/12")),
        partial_open_1_position=dmp_uint(registers.get("04/21")),
        partial_open_2_position=dmp_uint(registers.get("04/22")),
        partial_open_3_position=dmp_uint(registers.get("04/23")),
        opening_speed=dmp_uint(registers.get("04/42")),
        closing_speed=dmp_uint(registers.get("04/43")),
        opening_force=dmp_uint(registers.get("04/4A")),
        closing_force=dmp_uint(registers.get("04/4B")),
        pause_time=dmp_uint(registers.get("04/81")),
        photo_close_time=dmp_uint(registers.get("04/85")),
        photo_close_mode=dmp_uint(registers.get("04/86")),
        always_close_time=dmp_uint(registers.get("04/89")),
        always_close_mode=dmp_uint(registers.get("04/8A")),
        maintenance_threshold=dmp_uint(registers.get("04/B1")),
        maintenance_count=dmp_uint(registers.get("04/B2")),
        total_maneuver_count=dmp_uint(registers.get("04/B3")),
        input_1_function=option_name(INPUT_FUNCTIONS, dmp_uint(registers.get("04/71"))),
        input_2_function=option_name(INPUT_FUNCTIONS, dmp_uint(registers.get("04/72"))),
        input_3_function=option_name(INPUT_FUNCTIONS, dmp_uint(registers.get("04/73"))),
        input_4_function=option_name(INPUT_FUNCTIONS, dmp_uint(registers.get("04/74"))),
        input_1=dmp_bool(registers.get("04/71")),
        input_2=dmp_bool(registers.get("04/72")),
        input_3=dmp_bool(registers.get("04/73")),
        input_4=dmp_bool(registers.get("04/74")),
        auto_close=dmp_bool(registers.get("04/80")),
        photo_close=dmp_bool(registers.get("04/84")),
        always_close=dmp_bool(registers.get("04/88")),
        standby=dmp_bool(registers.get("04/8C")),
        pre_flash=dmp_bool(registers.get("04/94")),
        key_lock=dmp_bool(registers.get("04/9C")),
        limit_open=bool(io_byte & 0x01) if io_byte is not None else None,
        limit_closed=bool(io_byte & 0x02) if io_byte is not None else None,
        diagnostics_io_byte=io_byte,
        bluebus_devices=decode_bluebus_devices(dmp_bytes(registers.get("04/D0"))),
        controller_errors=decode_controller_errors(diagnostics_io),
        opening_slowdown_position=dmp_uint(registers.get("04/24")),
        closing_slowdown_position=dmp_uint(registers.get("04/25")),
        opening_slowdown_speed=dmp_uint(registers.get("04/45")),
        closing_slowdown_speed=dmp_uint(registers.get("04/46")),
        output_1_function=option_name(OUTPUT_FUNCTIONS, dmp_uint(registers.get("04/51"))),
        output_2_function=option_name(OUTPUT_FUNCTIONS, dmp_uint(registers.get("04/52"))),
        courtesy_light_time=dmp_uint(registers.get("04/5B")),
        step_step_mode=option_name(STEP_STEP_MODES, dmp_uint(registers.get("04/61"))),
        standby_time=dmp_uint(registers.get("04/8D")),
        standby_mode=option_name(STANDBY_MODES, dmp_uint(registers.get("04/8E"))),
        pre_flash_open_time=dmp_uint(registers.get("04/95")),
        pre_flash_close_time=dmp_uint(registers.get("04/99")),
        automation_blocked=dmp_bool(registers.get("04/9A")),
        slowdown=dmp_bool(registers.get("04/A2")),
        maintenance_mode=option_name(MAINTENANCE_MODES, dmp_uint(registers.get("04/B0"))),
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
        oxi_detected=any(key.startswith("0A/") for key in registers),
        oxi_product=dmp_ascii(registers.get("0A/09@00.0A")),
        oxi_hardware_version=dmp_ascii(registers.get("0A/0A@00.0A")),
        oxi_firmware_version=dmp_ascii(registers.get("0A/0B@00.0A")),
        oxi_description=dmp_ascii(registers.get("0A/0C@00.0A")),
    )
