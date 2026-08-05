"""Pure conversion of parsed DMP registers into normalized status."""

from __future__ import annotations

from ...models.status import NiceStatus
from .dmp import (
    DmpResponse,
    dmp_ascii,
    dmp_bool,
    dmp_bytes,
    dmp_uint,
    status_from_register,
)

STOP_REASON_BY_BYTE = {
    0x00: "normal",
    0x01: "obstacle_by_encoder",
    0x02: "obstacle_by_force",
    0x03: "photo_intervention",
    0x04: "halt",
    0x05: "emergency",
    0x06: "electrical_anomaly",
    0x07: "blocked",
    0x08: "timeout",
}


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
    stop_reason_value = dmp_bytes(registers.get("04/D0"))
    stop_reason_code = stop_reason_value[0] if stop_reason_value else None
    diagnostic_parameters = dmp_bytes(registers.get("04/D2"))
    if diagnostic_parameters and all(
        value in {0x00, 0xFF}
        for value in diagnostic_parameters
    ):
        diagnostic_parameters = None
    total_maneuver_count = dmp_uint(registers.get("04/B3"))
    alternate_movement_count = dmp_uint(registers.get("04/D4"))

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
        total_maneuver_count=(
            total_maneuver_count
            if total_maneuver_count is not None
            else alternate_movement_count
        ),
        alternate_movement_count=alternate_movement_count,
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
        limit_closed=bool(io_byte & 0x01) if io_byte is not None else None,
        limit_open=bool(io_byte & 0x02) if io_byte is not None else None,
        photocell=bool(io_byte & 0x04) if io_byte is not None else None,
        obstacle=(
            stop_reason_code in {0x01, 0x02}
            if stop_reason_code is not None
            else None
        ),
        diagnostics_io_byte=io_byte,
        motor_temperature=(
            diagnostic_parameters[15] - 9
            if diagnostic_parameters
            and len(diagnostic_parameters) > 15
            else None
        ),
        service_voltage=(
            diagnostic_parameters[9]
            if diagnostic_parameters
            and len(diagnostic_parameters) > 9
            else None
        ),
        last_stop_reason=(
            STOP_REASON_BY_BYTE.get(stop_reason_code)
            if stop_reason_code is not None
            else None
        ),
        last_stop_reason_code=stop_reason_code,
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
