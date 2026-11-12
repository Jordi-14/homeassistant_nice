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
        last_maneuvers=decode_last_maneuvers(dmp_bytes(registers.get("04/DA"))),
        motor_temperature=(
            int.from_bytes(diagnostic_parameters[14:16], "big") - 9
            if diagnostic_parameters
            and len(diagnostic_parameters) > 15
            and diagnostic_parameters[14] < 0x80  # vendor "not available"
            else None
        ),
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
