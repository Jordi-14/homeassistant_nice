"""Coordinator tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi import coordinator as coordinator_module
from custom_components.nice_bidiwifi.client import (
    DEP_ACTION_COURTESY_LIGHT,
    DEP_ACTION_PARTIAL_OPEN_1,
    NiceBidiAuthError,
    NiceBidiConnectionError,
)
from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from tests.conftest import FakeClient, config_entry_data, make_status


class FakeStore:
    """Storage fake for calibration tests."""

    def __init__(self, value: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.saved: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        """Return stored value or raise configured error."""
        if self.error is not None:
            raise self.error
        return self.value

    async def async_save(self, data: dict[str, Any]) -> None:
        """Record saved data."""
        self.saved = data


def _coordinator(
    hass: HomeAssistant,
    *,
    data: dict[str, Any] | None = None,
) -> NiceBidiDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=data or config_entry_data(), entry_id="entry-1")
    entry.add_to_hass(hass)
    return NiceBidiDataUpdateCoordinator(hass, entry)


async def test_update_data_reads_status_and_caches_device_info(
    hass: HomeAssistant,
) -> None:
    """Test successful coordinator updates."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client

    result = await instance._async_update_data()

    assert result is client.read_status_result
    assert instance.device_info is client.read_info_result
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_error is None
    assert isinstance(instance.last_successful_update, datetime)
    assert client.info_reads == 1

    await instance._async_update_data()
    assert client.info_reads == 1


async def test_update_data_ignores_device_info_read_errors(
    hass: HomeAssistant,
) -> None:
    """Test that INFO metadata failure does not fail status updates."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_info_error = NiceBidiConnectionError("info unavailable")
    instance.client = client

    result = await instance._async_update_data()

    assert result is client.read_status_result
    assert instance.device_info is None
    assert client.info_reads == 1


async def test_update_data_falls_back_to_command_only_when_dmp_status_is_unsupported(
    hass: HomeAssistant,
) -> None:
    """Test devices with writable DoorAction can set up without DMP status."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_status_error = NiceBidiConnectionError(
        '<Response type="T4_REQUEST"><Error><Code>14</Code></Error></Response>'
    )
    instance.client = client

    result = await instance._async_update_data()

    assert result.state is None
    assert result.position is None
    assert result.registers == {}
    assert instance.device_info is client.read_info_result
    assert instance.status_polling_supported is False
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_error is None

    await instance._async_update_data()
    assert client.info_reads == 1


async def test_update_data_does_not_fallback_to_command_only_for_other_status_errors(
    hass: HomeAssistant,
) -> None:
    """Test non-Code 14 status errors still fail setup/update."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_status_error = NiceBidiConnectionError(
        '<Response type="T4_REQUEST"><Error><Code>4</Code></Error></Response>'
    )
    instance.client = client

    with pytest.raises(UpdateFailed):
        await instance._async_update_data()

    assert instance.status_polling_supported is True
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_FAILED


async def test_update_data_maps_auth_failure(hass: HomeAssistant) -> None:
    """Test auth error handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_status_error = NiceBidiAuthError("denied")
    instance.client = client

    with pytest.raises(ConfigEntryAuthFailed):
        await instance._async_update_data()

    assert instance.connection_state == coordinator_module.CONNECTION_STATE_AUTH_FAILED
    assert instance.last_error == "denied"
    assert client.closed is True


async def test_update_data_maps_connection_failure(hass: HomeAssistant) -> None:
    """Test connection error handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_status_error = NiceBidiConnectionError("offline")
    instance.client = client

    with pytest.raises(UpdateFailed):
        await instance._async_update_data()

    assert instance.connection_state == coordinator_module.CONNECTION_STATE_FAILED
    assert instance.last_error == "offline"
    assert instance.update_interval == coordinator_module.ERROR_UPDATE_INTERVAL
    assert client.closed is True


async def test_send_action_records_command_metadata(hass: HomeAssistant) -> None:
    """Test command success handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="closed", position=0.0, current_position=0))

    await instance._async_send_action("open", refresh=False)

    assert client.actions == ["open"]
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_command == "open"
    assert isinstance(instance.last_command_latency_ms, int)
    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL
    assert instance.display_position_estimated is True

    await instance._async_cancel_position_simulation()


async def test_position_simulation_uses_calibrated_travel_speed(
    hass: HomeAssistant,
) -> None:
    """Test display animation uses 80% of full-travel calibration speed."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="closed", position=0.0, current_position=0))
    instance.calibration_profile = {
        "travel_speed": {
            "open": {
                "speed_percent_per_second": 5.0,
            }
        }
    }

    await instance._async_send_action("open", refresh=False)

    assert instance.position_simulation_action == "open"
    assert instance.position_simulation_speed_percent_per_second == 4.0

    await instance._async_cancel_position_simulation()


async def test_position_simulation_falls_back_without_calibration(
    hass: HomeAssistant,
) -> None:
    """Test display animation falls back to 1% per second."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="open", position=100.0, current_position=1000))

    await instance._async_send_action("close", refresh=False)

    assert instance.position_simulation_action == "close"
    assert instance.position_simulation_speed_percent_per_second == 1.0

    await instance._async_cancel_position_simulation()


async def test_send_action_wraps_connection_errors(hass: HomeAssistant) -> None:
    """Test command connection error handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.send_action_error = NiceBidiConnectionError("offline")
    instance.client = client

    with pytest.raises(HomeAssistantError, match="command failed"):
        await instance._async_send_action("stop", refresh=False)

    assert instance.connection_state == coordinator_module.CONNECTION_STATE_FAILED
    assert instance.last_error == "offline"
    assert client.closed is True


async def test_send_dep_action_records_command_metadata(hass: HomeAssistant) -> None:
    """Test DEP command success handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client

    await instance._async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_1, refresh=False)

    assert client.dep_actions == [DEP_ACTION_PARTIAL_OPEN_1]
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_command == DEP_ACTION_PARTIAL_OPEN_1
    assert isinstance(instance.last_command_latency_ms, int)
    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL
    assert instance.display_position_estimated is False


async def test_send_dep_action_uses_idle_interval_for_non_movement_actions(hass: HomeAssistant) -> None:
    """Test non-movement DEP actions do not switch to moving polling."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client

    await instance._async_send_dep_action(DEP_ACTION_COURTESY_LIGHT, refresh=False)

    assert client.dep_actions == [DEP_ACTION_COURTESY_LIGHT]
    assert instance.update_interval == coordinator_module.IDLE_UPDATE_INTERVAL


async def test_send_dep_action_wraps_connection_errors(hass: HomeAssistant) -> None:
    """Test DEP command connection error handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.send_dep_action_error = NiceBidiConnectionError("offline")
    instance.client = client

    with pytest.raises(HomeAssistantError, match="command failed"):
        await instance._async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_1, refresh=False)

    assert instance.connection_state == coordinator_module.CONNECTION_STATE_FAILED
    assert instance.last_error == "offline"
    assert client.closed is True


def test_encoder_position_helpers_support_forward_and_reverse_bounds(
    hass: HomeAssistant,
) -> None:
    """Test raw/percent conversion helpers."""
    forward = make_status(position=50.0, current_position=500, closed_position=0, open_position=1000)
    reverse = make_status(position=50.0, current_position=500, closed_position=1000, open_position=0)

    assert NiceBidiDataUpdateCoordinator._raw_for_percent(forward, 25) == 250
    assert NiceBidiDataUpdateCoordinator._percent_for_raw(forward, 750) == 75.0
    assert NiceBidiDataUpdateCoordinator._raw_reached("open", forward, 600, 500)
    assert NiceBidiDataUpdateCoordinator._raw_reached("close", forward, 400, 500)

    assert NiceBidiDataUpdateCoordinator._raw_for_percent(reverse, 25) == 750
    assert NiceBidiDataUpdateCoordinator._percent_for_raw(reverse, 250) == 75.0
    assert NiceBidiDataUpdateCoordinator._raw_reached("open", reverse, 400, 500)
    assert NiceBidiDataUpdateCoordinator._raw_reached("close", reverse, 600, 500)


def test_position_bounds_validation_and_endpoint_helpers() -> None:
    """Test position validation, clamping, and endpoint detection."""
    open_status = make_status(state="open", position=100.0, current_position=1000)
    closed_status = make_status(state="closed", position=0.0, current_position=0)

    NiceBidiDataUpdateCoordinator._validate_position_bounds(open_status)
    assert NiceBidiDataUpdateCoordinator._clamp_raw(open_status, -50) == 0
    assert NiceBidiDataUpdateCoordinator._clamp_raw(open_status, 1200) == 1000
    assert NiceBidiDataUpdateCoordinator._is_at_endpoint(open_status, "open")
    assert NiceBidiDataUpdateCoordinator._is_at_endpoint(closed_status, "close")

    with pytest.raises(HomeAssistantError, match="identical"):
        NiceBidiDataUpdateCoordinator._validate_position_bounds(
            make_status(closed_position=10, open_position=10)
        )


def test_calibrated_stop_raw_interpolates_valid_samples(
    hass: HomeAssistant,
) -> None:
    """Test calibrated stop interpolation."""
    instance = _coordinator(hass)
    instance.calibration_profile = {
        "samples": {
            "open": [
                {
                    "valid": True,
                    "target_percent": 50,
                    "corrected_stop_percent": 45,
                    "selected_abs_error_percent": 0.5,
                }
            ]
        }
    }

    stop_raw = instance._calibrated_stop_raw(
        25,
        "open",
        make_status(position=25.0, current_position=250, closed_position=0, open_position=1000),
    )

    assert stop_raw == 225


async def test_load_calibration_handles_empty_stored_profile(
    hass: HomeAssistant,
) -> None:
    """Test loading with no stored calibration."""
    instance = _coordinator(hass)
    instance._calibration_store = FakeStore()

    await instance.async_load_calibration()

    assert instance.calibration_profile is None
    assert instance.calibration_report is None
    assert instance.calibration_state == coordinator_module.CALIBRATION_STATE_NOT_CALIBRATED
    assert instance.calibration_last_error is None


async def test_load_calibration_builds_report_from_stored_profile(
    hass: HomeAssistant,
) -> None:
    """Test loading a stored calibration profile."""
    profile = _calibration_profile()
    instance = _coordinator(hass)
    instance._calibration_store = FakeStore(profile)

    await instance.async_load_calibration()

    assert instance.calibration_profile is profile
    assert instance.calibration_state == coordinator_module.CALIBRATION_STATE_CALIBRATED
    assert instance.calibration_report["point_count"] == 2
    assert instance.calibration_updated_at.isoformat() == profile["updated_at"]


async def test_load_calibration_records_storage_error(
    hass: HomeAssistant,
) -> None:
    """Test stored calibration load error handling."""
    instance = _coordinator(hass)
    instance._calibration_store = FakeStore(error=ValueError("bad json"))

    await instance.async_load_calibration()

    assert instance.calibration_profile is None
    assert instance.calibration_state == coordinator_module.CALIBRATION_STATE_NOT_CALIBRATED
    assert "bad json" in instance.calibration_last_error


def _sample(
    *,
    target_percent: int,
    error_percent: float,
    successful: bool = True,
    valid: bool = True,
) -> dict[str, Any]:
    """Build one calibration sample."""
    return {
        "action": "open",
        "endpoint_action": "close",
        "valid": valid,
        "failure_reason": None if valid else "settle_timeout",
        "attempt": 2,
        "target_percent": target_percent,
        "start_raw": 0,
        "start_percent": 0.0,
        "target_raw": target_percent * 10,
        "requested_stop_raw": target_percent * 10,
        "requested_stop_percent": float(target_percent),
        "stop_command_raw": target_percent * 10,
        "stop_command_percent": float(target_percent),
        "corrected_stop_raw": target_percent * 10 - 5,
        "corrected_stop_percent": float(target_percent) - 0.5,
        "final_raw": target_percent * 10 + round(error_percent * 10),
        "final_percent": target_percent + error_percent,
        "error_raw": round(error_percent * 10),
        "error_percent": error_percent,
        "move_duration_ms": 1200,
        "speed_raw_per_second": 100.0,
        "stop_command_latency_ms": 20,
        "successful": successful,
        "successful_attempts": 2 if successful else 0,
        "stability_attempts": 2,
        "attempts_used": 3,
        "selection_strategy": "stable_window" if successful else "best_attempt",
        "selected_attempt": 2,
        "selected_attempts": [1, 2],
        "selected_window_avg_abs_error_percent": abs(error_percent),
        "selected_abs_error_percent": abs(error_percent),
        "ignored_outlier_attempts": [],
        "ignored_invalid_attempts": [] if valid else [1],
        "outlier_error_percent": 15.0,
        "attempts": [
            {
                "attempt": 1,
                "valid": valid,
                "failure_reason": None if valid else "settle_timeout",
                "requested_stop_percent": float(target_percent),
                "stop_command_percent": float(target_percent),
                "final_percent": target_percent + error_percent,
                "error_percent": error_percent,
                "move_duration_ms": 1200,
                "stop_command_latency_ms": 20,
                "speed_raw_per_second": 100.0,
            }
        ],
    }


def _calibration_profile() -> dict[str, Any]:
    """Build a stored calibration profile."""
    return {
        "version": 5,
        "created_at": "2026-05-28T10:00:00+00:00",
        "updated_at": "2026-05-28T10:05:00+00:00",
        "poll_seconds": 0.5,
        "settle_seconds": 2.0,
        "command_pause_seconds": 0.5,
        "max_attempts": 5,
        "stability_attempts": 2,
        "target_tolerance_percent": 2.0,
        "targets": [20, 40],
        "bounds": {"initial_closed_raw": 0, "initial_open_raw": 1000},
        "travel_speed": {
            "open": {
                "action": "open",
                "start_percent": 0.0,
                "end_percent": 100.0,
                "duration_ms": 25000,
                "speed_percent_per_second": 4.0,
            },
            "close": {
                "action": "close",
                "start_percent": 100.0,
                "end_percent": 0.0,
                "duration_ms": 20000,
                "speed_percent_per_second": 5.0,
            },
        },
        "samples": {
            "open": [_sample(target_percent=20, error_percent=0.4)],
            "close": [_sample(target_percent=40, error_percent=-0.8)],
        },
        "events": [
            {
                "index": 1,
                "timestamp": "2026-05-28T10:00:00+00:00",
                "stage": "run",
                "message": "started",
                "details": {"target": 20},
            }
        ],
    }


def test_calibration_report_summary_attributes_and_formatting(
    hass: HomeAssistant,
) -> None:
    """Test calibration report generation."""
    instance = _coordinator(hass)
    instance.calibration_state = coordinator_module.CALIBRATION_STATE_CALIBRATED

    report = instance._build_calibration_report(_calibration_profile())
    instance.calibration_report = report

    assert report["quality"] == "good"
    assert report["point_count"] == 2
    assert report["successful_points"] == 2
    assert report["max_abs_error_percent"] == 0.8
    assert instance.calibration_quality == "good"
    assert instance.calibration_report_summary.startswith("good: 2/2")

    attrs = instance.calibration_report_attributes
    assert attrs["quality"] == "good"
    assert attrs["point_count"] == 2
    assert attrs["event_count"] == 1
    assert attrs["travel_speed"]["open"]["speed_percent_per_second"] == 4.0
    assert attrs["points"][0]["target_percent"] == 20

    formatted = instance._format_calibration_report(report)
    assert "Nice position calibration report" in formatted
    assert "Full-travel speed:" in formatted
    assert "Calibration points:" in formatted
    assert "Event log:" in formatted


def test_live_calibration_report_and_event_recording(
    hass: HomeAssistant,
) -> None:
    """Test live calibration event handling."""
    instance = _coordinator(hass)
    instance.calibration_state = coordinator_module.CALIBRATION_STATE_RUNNING

    instance._add_calibration_event("run", "Calibration started", target=20)

    assert len(instance._calibration_events) == 1
    assert instance.calibration_report["state"] == coordinator_module.CALIBRATION_STATE_RUNNING
    assert instance.calibration_report["summary"] == "Calibration started"
    assert instance.calibration_report_attributes["last_events"][0]["message"] == "Calibration started"


def test_select_calibration_sample_prefers_stable_window() -> None:
    """Test calibration sample selection."""
    attempts = [
        {"attempt": 1, "valid": True, "error_percent": 6.0},
        {"attempt": 2, "valid": True, "error_percent": 0.8},
        {"attempt": 3, "valid": True, "error_percent": -0.4},
        {"attempt": 4, "valid": False, "error_percent": 0.0},
    ]

    selected = NiceBidiDataUpdateCoordinator._select_calibration_sample(attempts)

    assert selected["strategy"] == "stable_window"
    assert selected["selected_attempt"] == 3
    assert selected["selected_attempts"] == [2, 3]
    assert selected["ignored_invalid_attempts"] == [4]
