"""Diagnostics support for Nice."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .cloud_coordinator import NiceHub
from .const import (
    CONF_CLOUD_TOKEN,
    CONF_CONNECTION_METHOD,
    CONF_SOURCE_ID,
    CONF_TARGET_MAC,
    CONNECTION_METHOD_CLOUD,
)
from .runtime import get_coordinator

TO_REDACT = {
    CONF_CLOUD_TOKEN,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SOURCE_ID,
    CONF_TARGET_MAC,
    CONF_USERNAME,
    "configuration_url",
    "device_serial",
    "interface_serial",
    "serial_number",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    if entry.data.get(CONF_CONNECTION_METHOD) == CONNECTION_METHOD_CLOUD:
        hub = entry.runtime_data
        diagnostics: dict[str, Any] = {
            "entry": dict(entry.data),
            "connection": {
                "method": CONNECTION_METHOD_CLOUD,
                "door_count": len(hub.doors) if isinstance(hub, NiceHub) else None,
                "controllable_door_count": (
                    len([door for door in hub.doors if door.get("creds")])
                    if isinstance(hub, NiceHub)
                    else None
                ),
            },
        }
        return async_redact_data(diagnostics, TO_REDACT)

    coordinator = get_coordinator(entry)
    status = coordinator.data
    device_info = coordinator.device_info

    diagnostics: dict[str, Any] = {
        "entry": dict(entry.data),
        "connection": {
            "state": coordinator.connection_state,
            "status_polling_supported": coordinator.status_polling_supported,
            "last_error": coordinator.last_error,
            "last_successful_update": (
                coordinator.last_successful_update.isoformat()
                if coordinator.last_successful_update
                else None
            ),
            "reconnect_count": coordinator.client.reconnect_count,
        },
        "command": {
            "last_command": coordinator.last_command,
            "last_command_latency_ms": coordinator.last_command_latency_ms,
        },
        "status": {
            "state": status.state if status else None,
            "position": status.position if status else None,
            "current_position": status.current_position if status else None,
            "closed_position": status.closed_position if status else None,
            "open_position": status.open_position if status else None,
            "display_position": coordinator.display_position,
            "display_position_estimated": coordinator.display_position_estimated,
            "position_simulation_action": coordinator.position_simulation_action,
            "position_simulation_speed_percent_per_second": (
                coordinator.position_simulation_speed_percent_per_second
            ),
            "is_moving": status.is_moving if status else None,
            "bus_t4": {
                "max_open_position": status.max_open_position if status else None,
                "partial_open_1_position": status.partial_open_1_position if status else None,
                "partial_open_2_position": status.partial_open_2_position if status else None,
                "partial_open_3_position": status.partial_open_3_position if status else None,
                "opening_speed": status.opening_speed if status else None,
                "closing_speed": status.closing_speed if status else None,
                "opening_force": status.opening_force if status else None,
                "closing_force": status.closing_force if status else None,
                "pause_time": status.pause_time if status else None,
                "maintenance_threshold": status.maintenance_threshold if status else None,
                "maintenance_count": status.maintenance_count if status else None,
                "total_maneuver_count": status.total_maneuver_count if status else None,
                "input_1": status.input_1 if status else None,
                "input_2": status.input_2 if status else None,
                "input_3": status.input_3 if status else None,
                "input_4": status.input_4 if status else None,
                "auto_close": status.auto_close if status else None,
                "photo_close": status.photo_close if status else None,
                "always_close": status.always_close if status else None,
                "standby": status.standby if status else None,
                "pre_flash": status.pre_flash if status else None,
                "key_lock": status.key_lock if status else None,
                "limit_closed": status.limit_closed if status else None,
                "limit_open": status.limit_open if status else None,
                "photocell": status.photocell if status else None,
                "obstacle": status.obstacle if status else None,
                "last_stop_reason": status.last_stop_reason if status else None,
                "last_stop_reason_code": status.last_stop_reason_code if status else None,
                "diagnostics_io_byte": status.diagnostics_io_byte if status else None,
                "diagnostics_parameters": status.diagnostics_parameters if status else None,
                "oxi_detected": status.oxi_detected if status else None,
                "oxi_product": status.oxi_product if status else None,
                "oxi_hardware_version": status.oxi_hardware_version if status else None,
                "oxi_firmware_version": status.oxi_firmware_version if status else None,
                "oxi_description": status.oxi_description if status else None,
            },
        },
        "device_info": asdict(device_info) if device_info else None,
        "calibration": {
            "state": coordinator.calibration_state,
            "quality": coordinator.calibration_quality,
            "updated_at": coordinator.calibration_updated_at.isoformat()
            if coordinator.calibration_updated_at
            else None,
            "last_error": coordinator.calibration_last_error,
            "summary": coordinator.calibration_report_summary,
        },
    }
    return async_redact_data(diagnostics, TO_REDACT)
