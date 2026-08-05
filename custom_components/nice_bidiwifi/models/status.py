"""Normalized status models for Nice automations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

STATE_STOPPED = "stopped"
STATE_OPENING = "opening"
STATE_CLOSING = "closing"
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_PARTIALLY_OPEN = "partially_open"


@dataclass(frozen=True, slots=True)
class NiceStatus:
    """Current normalized automation and controller state."""

    state: str | None
    position: float | None
    current_position: int | None
    closed_position: int | None
    open_position: int | None
    registers: Mapping[str, str]
    max_open_position: int | None = None
    partial_open_1_position: int | None = None
    partial_open_2_position: int | None = None
    partial_open_3_position: int | None = None
    opening_speed: int | None = None
    closing_speed: int | None = None
    opening_force: int | None = None
    closing_force: int | None = None
    pause_time: int | None = None
    photo_close_time: int | None = None
    photo_close_mode: int | None = None
    always_close_time: int | None = None
    always_close_mode: int | None = None
    maintenance_threshold: int | None = None
    maintenance_count: int | None = None
    total_maneuver_count: int | None = None
    alternate_movement_count: int | None = None
    input_1: bool | None = None
    input_2: bool | None = None
    input_3: bool | None = None
    input_4: bool | None = None
    auto_close: bool | None = None
    photo_close: bool | None = None
    always_close: bool | None = None
    standby: bool | None = None
    pre_flash: bool | None = None
    key_lock: bool | None = None
    limit_closed: bool | None = None
    limit_open: bool | None = None
    photocell: bool | None = None
    obstacle: bool | None = None
    diagnostics_io_byte: int | None = None
    motor_temperature: int | None = None
    service_voltage: int | None = None
    last_stop_reason: str | None = None
    last_stop_reason_code: int | None = None
    diagnostics_parameters: str | None = None
    oxi_detected: bool | None = None
    oxi_product: str | None = None
    oxi_hardware_version: str | None = None
    oxi_firmware_version: str | None = None
    oxi_description: str | None = None

    def __post_init__(self) -> None:
        """Freeze register observations with the rest of the status."""
        object.__setattr__(
            self,
            "registers",
            MappingProxyType(dict(self.registers)),
        )

    @property
    def is_moving(self) -> bool:
        """Return true when the automation is moving."""
        return self.state in {STATE_OPENING, STATE_CLOSING}
