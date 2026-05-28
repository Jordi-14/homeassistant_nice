"""Diagnostics support for Nice BiDi-WiFi."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_SOURCE_ID, CONF_TARGET_MAC
from .runtime import get_coordinator

TO_REDACT = {
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
    coordinator = get_coordinator(entry)
    status = coordinator.data
    device_info = coordinator.device_info

    diagnostics: dict[str, Any] = {
        "entry": dict(entry.data),
        "connection": {
            "state": coordinator.connection_state,
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
            "is_moving": status.is_moving if status else None,
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
