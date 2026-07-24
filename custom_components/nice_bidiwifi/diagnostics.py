"""Diagnostics support for Nice."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_SOURCE_ID, CONF_TARGET_MAC
from .controllers.administration import ADMINISTRATION_HISTORY_LIMIT
from .protocol.nhk.administration import (
    LOG_EVENTS_PER_SCOPE,
    MAX_ADMINISTRATION_XML_BYTES,
    MAX_GROUPS,
    MAX_GROUP_RULES,
    MAX_LOG_EVENTS,
)
from .redaction import (
    SENSITIVE_CONFIG_KEYS,
    allowed_config_diagnostics,
    bounded_protocol_observations,
    configured_secrets,
    redact_text,
)
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
    *SENSITIVE_CONFIG_KEYS,
}


def _capability_diagnostics(capability) -> dict[str, Any]:
    """Serialize one advertised capability through an explicit allowlist."""
    return {
        "owner": capability.owner,
        "owner_id": capability.owner_id,
        "name": capability.name,
        "path": capability.path,
        "value_type": capability.value_type,
        "permission": capability.permission,
        "values": capability.values,
    }


def _device_info_diagnostics(device_info) -> dict[str, Any] | None:
    """Serialize INFO metadata through an explicit allowlist."""
    if device_info is None:
        return None
    return {
        "interface_hw_version": device_info.interface_hw_version,
        "interface_fw_version": device_info.interface_fw_version,
        "interface_manufacturer": device_info.interface_manufacturer,
        "interface_product": device_info.interface_product,
        "interface_serial": device_info.interface_serial,
        "device_type": device_info.device_type,
        "device_manufacturer": device_info.device_manufacturer,
        "device_product": device_info.device_product,
        "device_description": device_info.device_description,
        "device_hw_version": device_info.device_hw_version,
        "device_fw_version": device_info.device_fw_version,
        "device_serial": device_info.device_serial,
        "device_product_detail": device_info.device_product_detail,
        "interface_name_present": device_info.interface_name is not None,
        "interface_date": device_info.interface_date,
        "interface_zone": device_info.interface_zone,
        "interface_dst": device_info.interface_dst,
        "interface_commands": device_info.interface_commands,
        "protocol_version": device_info.protocol_version,
        "services": [
            _capability_diagnostics(capability) for capability in device_info.services
        ],
        "properties": [
            _capability_diagnostics(capability) for capability in device_info.properties
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = get_coordinator(entry)
    status = coordinator.data
    device_info = coordinator.device_info
    command_result = getattr(coordinator, "last_command_result", None)
    capabilities = getattr(coordinator, "capabilities", None)
    log_snapshot = getattr(coordinator, "interface_log_snapshot", None)
    group_snapshot = getattr(coordinator, "access_group_snapshot", None)
    administration_history = getattr(
        coordinator,
        "administration_history",
        (),
    )
    secrets = configured_secrets(entry.data)

    diagnostics: dict[str, Any] = {
        "entry": allowed_config_diagnostics(entry.data),
        "connection": {
            "state": coordinator.connection_state,
            "active_route": coordinator.active_connection_route,
            "local_state": coordinator.local_connection_state,
            "cloud_state": coordinator.cloud_connection_state,
            "status_polling_supported": coordinator.status_polling_supported,
            "last_error": redact_text(coordinator.last_error, secrets),
            "last_successful_update": (
                coordinator.last_successful_update.isoformat()
                if coordinator.last_successful_update
                else None
            ),
            "reconnect_count": coordinator.client.reconnect_count,
            "selected_route": getattr(
                coordinator.client,
                "selected_route",
                coordinator.active_connection_route,
            ),
            "local_failure_count": getattr(
                coordinator.client,
                "local_failure_count",
                0,
            ),
            "local_recovery_success_count": getattr(
                coordinator.client,
                "local_recovery_success_count",
                0,
            ),
            "local_probe_interval_seconds": getattr(
                coordinator.client,
                "local_probe_interval_seconds",
                None,
            ),
        },
        "command": {
            "last_command": coordinator.last_command,
            "last_command_latency_ms": coordinator.last_command_latency_ms,
            "acknowledgement": (
                command_result.acknowledgement if command_result is not None else None
            ),
            "error_code": (
                command_result.error_code if command_result is not None else None
            ),
        },
        "event_stream": {
            "state": coordinator.event_stream_state,
            "error": coordinator.event_stream_error,
            "last_event_at": (
                coordinator.last_event_at.isoformat()
                if coordinator.last_event_at is not None
                else None
            ),
            "event_count": coordinator.protocol_event_count,
            "malformed_event_count": coordinator.malformed_protocol_event_count,
            "history_limit": coordinator.event_history.maxlen,
            "history": [event.as_diagnostics() for event in coordinator.event_history],
        },
        "status": {
            "state": status.state if status else None,
            "position": status.position if status else None,
            "current_position": status.current_position if status else None,
            "closed_position": status.closed_position if status else None,
            "open_position": status.open_position if status else None,
            "display_position": coordinator.display_position,
            "display_position_estimated": coordinator.display_position_estimated,
            "position_reporting_observed": coordinator.position_reporting_observed,
            "state_source": coordinator.state_source,
            "position_source": coordinator.position_source,
            "position_confidence": coordinator.position_confidence,
            "position_simulation_action": coordinator.position_simulation_action,
            "position_simulation_speed_percent_per_second": (
                coordinator.position_simulation_speed_percent_per_second
            ),
            "is_moving": status.is_moving if status else None,
            "protocol_observations": (
                bounded_protocol_observations(status.registers) if status else {}
            ),
            "bus_t4": {
                "max_open_position": status.max_open_position if status else None,
                "partial_open_1_position": status.partial_open_1_position
                if status
                else None,
                "partial_open_2_position": status.partial_open_2_position
                if status
                else None,
                "partial_open_3_position": status.partial_open_3_position
                if status
                else None,
                "opening_speed": status.opening_speed if status else None,
                "closing_speed": status.closing_speed if status else None,
                "opening_force": status.opening_force if status else None,
                "closing_force": status.closing_force if status else None,
                "pause_time": status.pause_time if status else None,
                "maintenance_threshold": status.maintenance_threshold
                if status
                else None,
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
                "last_stop_reason_code": status.last_stop_reason_code
                if status
                else None,
                "diagnostics_io_byte": status.diagnostics_io_byte if status else None,
                "diagnostics_parameters": status.diagnostics_parameters
                if status
                else None,
                "oxi_detected": status.oxi_detected if status else None,
                "oxi_product": status.oxi_product if status else None,
                "oxi_hardware_version": status.oxi_hardware_version if status else None,
                "oxi_firmware_version": status.oxi_firmware_version if status else None,
                "oxi_description": status.oxi_description if status else None,
            },
        },
        "device_info": _device_info_diagnostics(device_info),
        "capabilities": (
            {
                "family": capabilities.family,
                "profile_key": capabilities.profile_key,
                "device_id": capabilities.device_id,
                "high_level_actions": capabilities.high_level_actions,
                "readable_status": capabilities.readable_status,
                "obstruction": capabilities.obstruction,
                "t4_allowed_advertised": (capabilities.t4_allowed.advertised),
                "t4_allowed_valid": capabilities.t4_allowed_valid,
                "t4_allowed_mask": capabilities.t4_allowed_mask,
                "supported_t4_action_codes": (
                    sorted(capabilities.supported_t4_action_codes)
                    if capabilities.supported_t4_action_codes is not None
                    else None
                ),
                "observed_dmp_registers": sorted(capabilities.observed_dmp_registers),
                "status_sources": sorted(capabilities.status_sources),
                "position_sources": sorted(capabilities.position_sources),
                "local_available": capabilities.local_available,
                "relay_available": capabilities.relay_available,
                "local_events": capabilities.local_events,
                "diagnostic_events": capabilities.diagnostic_events,
                "logs": capabilities.logs,
                "groups": capabilities.groups,
                "tables": capabilities.tables,
                "interface_name_write": capabilities.interface_name_write,
                "time_sync": capabilities.time_sync,
                "reboot": capabilities.reboot,
            }
            if capabilities is not None
            else None
        ),
        "calibration": {
            "state": coordinator.calibration_state,
            "quality": coordinator.calibration_quality,
            "updated_at": coordinator.calibration_updated_at.isoformat()
            if coordinator.calibration_updated_at
            else None,
            "last_error": coordinator.calibration_last_error,
            "cancel_reason": coordinator.calibration_cancel_reason,
            "cancel_stop_requested": coordinator.calibration_cancel_stop_requested,
            "cancel_stop_sent": coordinator.calibration_cancel_stop_sent,
            "summary": coordinator.calibration_report_summary,
        },
        "administration": {
            "capability_basis": (
                "inferred_from_shared_wifi_product_family"
                if capabilities is not None
                and any(
                    value is True
                    for value in (
                        capabilities.logs,
                        capabilities.groups,
                        capabilities.interface_name_write,
                        capabilities.time_sync,
                        capabilities.reboot,
                    )
                )
                else "not_identified"
            ),
            "mutation_blocked_while_moving": True,
            "currently_moving": bool(status and status.is_moving),
            "writes_replayed_after_ambiguous_failure": False,
            "name_round_trip_verification": True,
            "clock_round_trip_verification": True,
            "group_mutation_exposed": False,
            "limits": {
                "events_per_scope_requested": LOG_EVENTS_PER_SCOPE,
                "maximum_events_retained": MAX_LOG_EVENTS,
                "maximum_groups_retained": MAX_GROUPS,
                "maximum_rules_counted_per_owner": MAX_GROUP_RULES,
                "maximum_administration_response_bytes": (
                    MAX_ADMINISTRATION_XML_BYTES
                ),
                "operation_history": getattr(
                    administration_history,
                    "maxlen",
                    ADMINISTRATION_HISTORY_LIMIT,
                ),
            },
            "logs": (
                {
                    "retrieved_at": (
                        log_snapshot.retrieved_at.isoformat()
                        if log_snapshot.retrieved_at is not None
                        else None
                    ),
                    "retained_count": log_snapshot.count,
                    "events": [
                        event.as_dict() for event in log_snapshot.events
                    ],
                }
                if log_snapshot is not None
                else None
            ),
            "groups": (
                {
                    "retrieved_at": (
                        group_snapshot.retrieved_at.isoformat()
                        if group_snapshot.retrieved_at is not None
                        else None
                    ),
                    "retained_count": group_snapshot.count,
                    "summaries": [
                        group.as_dict() for group in group_snapshot.groups
                    ],
                }
                if group_snapshot is not None
                else None
            ),
            "operation_history": [
                operation.as_diagnostics()
                for operation in administration_history
            ],
        },
    }
    return async_redact_data(diagnostics, TO_REDACT)
