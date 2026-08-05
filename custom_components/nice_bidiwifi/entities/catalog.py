"""Frozen compatibility catalog for existing Nice entities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProtectedEntity:
    """One protected entity-registry definition."""

    platform: str
    key: str
    unique_id_suffix: str
    enabled_default: bool
    visible_default: bool


def _entity(
    platform: str,
    key: str,
    enabled: bool = True,
    visible: bool = False,
) -> ProtectedEntity:
    return ProtectedEntity(platform, key, key, enabled, visible)


PROTECTED_ENTITY_CATALOG = (
    _entity("cover", "cover", visible=True),
    _entity("switch", "cover_switch", visible=True),
    _entity("button", "partial_open_1", visible=True),
    _entity("button", "partial_open_2", visible=True),
    _entity("button", "partial_open_3", visible=True),
    _entity("button", "step_step", visible=True),
    _entity("button", "courtesy_light"),
    _entity("button", "courtesy_light_timer"),
    _entity("button", "lock", False),
    _entity("button", "unlock", False),
    _entity("button", "refresh_status"),
    _entity("button", "reconnect"),
    _entity("button", "calibrate_positions", False),
    _entity("switch", "bus_t4_auto_close", visible=True),
    _entity("switch", "bus_t4_photo_close", visible=True),
    _entity("switch", "bus_t4_always_close", visible=True),
    _entity("switch", "bus_t4_standby"),
    _entity("switch", "bus_t4_pre_flash"),
    _entity("switch", "bus_t4_key_lock"),
    _entity("number", "bus_t4_pause_time", visible=True),
    _entity("number", "bus_t4_opening_force", visible=True),
    _entity("number", "bus_t4_closing_force", visible=True),
    _entity("number", "bus_t4_opening_speed", visible=True),
    _entity("number", "bus_t4_closing_speed", visible=True),
    _entity("number", "bus_t4_photo_close_time", visible=True),
    _entity("number", "bus_t4_photo_close_mode", False),
    _entity("number", "bus_t4_always_close_time", visible=True),
    _entity("number", "bus_t4_always_close_mode", False),
    _entity("number", "bus_t4_partial_open_1_position", visible=True),
    _entity("number", "bus_t4_partial_open_2_position", visible=True),
    _entity("number", "bus_t4_partial_open_3_position", visible=True),
    _entity("number", "bus_t4_maintenance_threshold", False),
    _entity("sensor", "connection_state", visible=True),
    _entity("sensor", "last_successful_update"),
    _entity("sensor", "last_error"),
    _entity("sensor", "reconnect_count"),
    _entity("sensor", "last_command", False),
    _entity("sensor", "last_command_latency", False),
    _entity("sensor", "position_calibration_state"),
    _entity("sensor", "last_position_calibration"),
    _entity("sensor", "position_calibration_error"),
    _entity("sensor", "position_calibration_quality"),
    _entity("sensor", "position_calibration_report", False),
    _entity("sensor", "gate_position", visible=True),
    _entity("sensor", "current_encoder_position"),
    _entity("sensor", "closed_encoder_position"),
    _entity("sensor", "open_encoder_position"),
    _entity("sensor", "max_open_encoder_position"),
    _entity("sensor", "partial_open_1_position"),
    _entity("sensor", "partial_open_2_position"),
    _entity("sensor", "partial_open_3_position"),
    _entity("sensor", "opening_speed"),
    _entity("sensor", "closing_speed"),
    _entity("sensor", "opening_force"),
    _entity("sensor", "closing_force"),
    _entity("sensor", "pause_time"),
    _entity("sensor", "maintenance_threshold"),
    _entity("sensor", "maintenance_count"),
    _entity("sensor", "total_maneuver_count"),
    _entity("sensor", "last_stop_reason"),
    _entity("sensor", "motor_temperature"),
    _entity("sensor", "service_voltage", False),
    _entity("sensor", "diagnostics_io_byte", False),
    _entity("sensor", "diagnostics_parameters", False),
    _entity("sensor", "oxi_product", False),
    _entity("sensor", "oxi_firmware", False),
    _entity("sensor", "oxi_hardware", False),
    _entity("sensor", "oxi_description", False),
    _entity("sensor", "interface_firmware"),
    _entity("sensor", "interface_hardware"),
    _entity("sensor", "interface_serial"),
    _entity("sensor", "control_unit_firmware"),
    _entity("sensor", "control_unit_hardware"),
    _entity("sensor", "control_unit_serial"),
    _entity("sensor", "control_unit_product_detail"),
    _entity("binary_sensor", "gate_open", visible=True),
    _entity("binary_sensor", "limit_closed", False),
    _entity("binary_sensor", "limit_open", False),
    _entity("binary_sensor", "photocell", False),
    _entity("binary_sensor", "obstacle"),
    _entity("binary_sensor", "input_1", False),
    _entity("binary_sensor", "input_2", False),
    _entity("binary_sensor", "input_3", False),
    _entity("binary_sensor", "input_4", False),
    _entity("binary_sensor", "auto_close"),
    _entity("binary_sensor", "photo_close"),
    _entity("binary_sensor", "always_close"),
    _entity("binary_sensor", "standby"),
    _entity("binary_sensor", "pre_flash"),
    _entity("binary_sensor", "key_lock"),
    _entity("binary_sensor", "oxi_detected"),
)

assert len(PROTECTED_ENTITY_CATALOG) == 91
