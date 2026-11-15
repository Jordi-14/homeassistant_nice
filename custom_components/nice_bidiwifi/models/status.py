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
    obstacle: bool | None = None
    diagnostics_io_byte: int | None = None
    bluebus_devices: dict[str, str] | None = None
    controller_errors: dict[str, bool] | None = None
    transformation_ratio: int | None = None
    min_closing_position: int | None = None
    opening_slowdown_position: int | None = None
    closing_slowdown_position: int | None = None
    opening_slowdown_speed: int | None = None
    closing_slowdown_speed: int | None = None
    output_1_function: str | None = None
    output_2_function: str | None = None
    courtesy_light_time: int | None = None
    step_step_mode: str | None = None
    standby_time: int | None = None
    standby_mode: str | None = None
    pre_flash_open_time: int | None = None
    pre_flash_close_time: int | None = None
    automation_blocked: bool | None = None
    slowdown: bool | None = None
    maintenance_mode: str | None = None
    input_1_function: str | None = None
    input_2_function: str | None = None
    input_3_function: str | None = None
    input_4_function: str | None = None
    partial_open_mode: str | None = None
    open_mode: str | None = None
    close_mode: str | None = None
    stop_mode: str | None = None
    photo_closing_reaction: str | None = None
    photo_opening_reaction: str | None = None
    photo_3_reaction: str | None = None
    alt_opening_reaction: str | None = None
    alt_closing_reaction: str | None = None
    obstacle_opening_reaction: str | None = None
    obstacle_closing_reaction: str | None = None
    electric_lock_time: int | None = None
    suction_cup_time: int | None = None
    starting_torque_time: int | None = None
    starting_torque: bool | None = None
    slave_mode: bool | None = None
    last_maneuvers: tuple[str, ...] | None = None
    # LOCAL-DIAGNOSTIC(pr55): stale maneuver-log flag; see coordinator.py:_cache_extended_status.
    last_maneuvers_stale: bool | None = None
    motor_temperature: int | None = None
    service_voltage: int | None = None
    diagnostics_parameters: str | None = None
    oxi_detected: bool | None = None
    oxi_product: str | None = None
    oxi_hardware_version: str | None = None
    oxi_firmware_version: str | None = None
    oxi_description: str | None = None
    oxi_receiver_type: str | None = None
    oxi_coding_type: str | None = None

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
