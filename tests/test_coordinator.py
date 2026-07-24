"""Coordinator tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from itertools import count
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi import calibration as calibration_module
from custom_components.nice_bidiwifi import coordinator as coordinator_module
from custom_components.nice_bidiwifi import position as position_module
from custom_components.nice_bidiwifi.client import (
    DEP_ACTION_COURTESY_LIGHT,
    DEP_ACTION_PARTIAL_OPEN_1,
    NiceBidiAuthError,
    NiceBidiConnectionError,
)
from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from tests.conftest import FakeClient, config_entry_data, make_device_info, make_status


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
    assert client.read_status_include_extended == [True, False]


async def test_update_data_reuses_cached_extended_status_between_broad_reads(
    hass: HomeAssistant,
) -> None:
    """Test broad BusT4 diagnostics are cached between slower refreshes."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client

    first = await instance._async_update_data()
    assert first.opening_speed == 60

    client.read_status_result = make_status(
        state="open",
        position=100.0,
        current_position=1000,
        opening_speed=None,
        opening_force=None,
        auto_close=None,
        limit_open=None,
        oxi_detected=None,
        oxi_product=None,
        last_stop_reason=None,
    )

    second = await instance._async_update_data()

    assert client.read_status_include_extended == [True, False]
    assert second.position == 100.0
    assert second.opening_speed == 60
    assert second.auto_close is True
    assert second.limit_open is True
    assert second.oxi_product == "OXI"


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


async def test_update_data_uses_nhk_status_when_dmp_status_is_unsupported(
    hass: HomeAssistant,
) -> None:
    """Test CU_WIFI devices can use NHK DoorStatus when DMP status returns Code 14."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_status_error = NiceBidiConnectionError(
        '<Response type="T4_REQUEST"><Error><Code>14</Code></Error></Response>'
    )
    client.read_info_result = make_device_info(nhk_status=True)
    client.read_nhk_status_result = make_status(
        state="closing",
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    instance.client = client

    result = await instance._async_update_data()

    assert result.state == "closing"
    assert result.position is None
    assert instance.device_info is client.read_info_result
    assert instance.status_polling_supported is True
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert client.info_reads == 1
    assert client.nhk_status_reads == 1

    await instance._async_update_data()
    assert client.nhk_status_reads == 2
    assert client.info_reads == 1


async def test_update_data_keeps_cuwifi_unknown_door_status_available(
    hass: HomeAssistant,
) -> None:
    """Test transient CU_WIFI unknown DoorStatus is not treated as update failure."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_nhk_status_result = make_status(
        state=None,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    instance.client = client
    instance._use_nhk_status = True

    result = await instance._async_update_data()

    assert result.state is None
    assert result.position is None
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_error is None
    assert instance.last_update_success is True


async def test_motion_status_uses_nhk_status_after_fallback(
    hass: HomeAssistant,
) -> None:
    """Test target-position tracking uses the selected NHK status reader."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_nhk_status_result = make_status(
        state="opening",
        position=42.0,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    instance.client = client
    instance._use_nhk_status = True

    result = await instance._async_read_motion_status()

    assert result.state == "opening"
    assert result.position == 42.0
    assert client.nhk_status_reads == 1


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


async def test_stop_preserves_in_flight_estimated_position(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Stop freezes the optimistic position instead of snapping to an endpoint."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    clock = 0.0
    monkeypatch.setattr(position_module.time, "monotonic", lambda: clock)
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock)
    instance.async_set_updated_data(make_status(state="closed", position=0.0, current_position=0))

    await instance._async_send_action("open", refresh=False)
    clock = 12.0
    assert instance.display_position == 12.0

    await instance._async_send_action("stop", refresh=False)

    assert client.actions == ["open", "stop"]
    assert instance._last_known_position == 12.0
    assert instance.position_simulation_action is None


async def test_recent_stop_command_masks_stale_open_status(hass: HomeAssistant) -> None:
    """Test stale CU_WIFI endpoint status is held as stopped after a local stop."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="closing", position=55.0, current_position=None))
    instance._last_known_position = 55.0

    await instance._async_send_action("stop", refresh=False)

    client.read_status_result = make_status(state="open", position=100.0, current_position=None)
    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.state == "stopped"
    assert result.position is None
    assert instance.display_position == 55.0
    assert instance.display_position_estimated is True
    assert result.registers["NHK/RecentStopOverride"] == "open"


async def test_recent_stop_command_masks_stale_closed_status(hass: HomeAssistant) -> None:
    """Test a stale CU_WIFI closed endpoint is held as stopped after a local stop."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="closing", position=35.0, current_position=None))
    instance._last_known_position = 35.0

    await instance._async_send_action("stop", refresh=False)

    client.read_status_result = make_status(state="closed", position=0.0, current_position=None)
    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.state == "stopped"
    assert result.position is None
    assert instance.display_position == 35.0
    assert instance.display_position_estimated is True
    assert result.registers["NHK/RecentStopOverride"] == "closed"


async def test_movement_command_clears_recent_stop_hint(hass: HomeAssistant) -> None:
    """Test movement commands clear the local stop-state override."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="closing", position=55.0, current_position=None))

    await instance._async_send_action("stop", refresh=False)
    await instance._async_send_action("open", refresh=False)

    client.read_status_result = make_status(state="opening", position=60.0, current_position=None)
    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.state == "opening"
    assert "NHK/RecentStopOverride" not in result.registers

    await instance._async_cancel_position_simulation()


async def test_display_position_uses_last_known_sparse_position(hass: HomeAssistant) -> None:
    """Test sparse CU_WIFI position updates keep the last displayed position."""
    instance = _coordinator(hass)
    instance._store_successful_status(make_status(state="stopped", position=42.0))
    instance.async_set_updated_data(make_status(state="closing", position=None, current_position=None))

    assert instance.display_position == 42.0
    assert instance.display_position_estimated is True


async def test_stopped_status_uses_last_known_position(hass: HomeAssistant) -> None:
    """Test stopped CU_WIFI status without position behaves like a mid-travel stop."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.read_status_result = make_status(state="stopped", position=None, current_position=None)
    instance.client = client
    instance._last_known_position = 44.0

    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.state == "stopped"
    assert result.position is None
    assert instance.display_position == 44.0
    assert instance.display_position_estimated is True
    assert result.registers["NHK/LastKnownPositionFallback"] == "44.0"


@pytest.mark.parametrize(
    ("action", "terminal_state", "seed_position", "expected_position"),
    [
        ("open", "open", 20.0, 100.0),
        ("close", "closed", 80.0, 0.0),
    ],
)
async def test_matching_terminal_status_stops_simulation_and_confirms_endpoint(
    hass: HomeAssistant,
    action: str,
    terminal_state: str,
    seed_position: float,
    expected_position: float,
) -> None:
    """Test a terminal BiDi state immediately ends matching display movement."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    client.read_status_result = make_status(
        state="stopped",
        position=seed_position,
        current_position=None,
    )
    seed = await instance._async_update_data()
    instance.async_set_updated_data(seed)
    instance._start_position_simulation(action)

    client.read_status_result = make_status(
        state=terminal_state,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.position == expected_position
    assert result.registers["NHK/ConfirmedEndpointPosition"] == terminal_state
    assert instance.display_position == expected_position
    assert instance.display_position_estimated is False
    assert instance.position_simulation_action is None
    assert instance.position_source == "confirmed_endpoint"


@pytest.mark.parametrize("terminal_state", ["open", "closed"])
async def test_state_only_terminal_status_never_creates_position(
    hass: HomeAssistant,
    terminal_state: str,
) -> None:
    """Test endpoint state alone does not create position support."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    client.read_status_result = make_status(
        state=terminal_state,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
    )

    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.position is None
    assert instance.position_reporting_observed is False
    assert instance.display_position is None
    assert instance.position_source is None
    assert "NHK/ConfirmedEndpointPosition" not in result.registers


@pytest.mark.parametrize("terminal_state", ["stopped", "partially_open"])
async def test_unobserved_external_movement_invalidates_stale_position(
    hass: HomeAssistant,
    terminal_state: str,
) -> None:
    """Test a movement missed between idle polls does not retain its old endpoint."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    client.read_status_result = make_status(
        state="closed",
        position=0.0,
        current_position=None,
    )
    seed = await instance._async_update_data()
    instance.async_set_updated_data(seed)

    client.read_status_result = make_status(
        state=terminal_state,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    result = await instance._async_update_data()
    instance.async_set_updated_data(result)

    assert result.position is None
    assert result.registers["NHK/LastKnownPositionInvalidated"] == (
        "unobserved_external_movement"
    )
    assert instance.position_reporting_observed is True
    assert instance.display_position is None
    assert instance.display_position_estimated is False
    assert instance.position_source is None


async def test_set_position_uses_display_position_when_status_position_is_sparse(
    hass: HomeAssistant,
) -> None:
    """Test set-position commands can start from cached CU_WIFI display position."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="stopped", position=None, current_position=None))
    instance._last_known_position = 55.0
    instance._position_reporting_observed = True
    refreshes = 0

    async def fake_request_refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    instance.async_request_refresh = fake_request_refresh

    await instance.async_set_position(100)

    assert client.actions == ["open"]
    assert refreshes == 1
    assert instance.position_simulation_action == "open"

    await instance._async_cancel_position_simulation()
    await instance._async_cancel_post_command_refresh()


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
    instance.async_set_updated_data(make_status(state="closed", position=0.0, current_position=0))

    await instance._async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_1, refresh=False)

    assert client.dep_actions == [DEP_ACTION_PARTIAL_OPEN_1]
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_command == DEP_ACTION_PARTIAL_OPEN_1
    assert isinstance(instance.last_command_latency_ms, int)
    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL
    assert instance.display_position_estimated is True
    assert instance.position_simulation_action == "open"

    await instance._async_cancel_position_simulation()


async def test_partial_open_without_reported_position_never_simulates(
    hass: HomeAssistant,
) -> None:
    """Test a state-only gate never exposes an invented partial-open position."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(
        make_status(
            state="closed",
            position=None,
            current_position=None,
            closed_position=None,
            open_position=None,
        )
    )

    await instance._async_send_dep_action(DEP_ACTION_PARTIAL_OPEN_1, refresh=False)

    assert client.dep_actions == [DEP_ACTION_PARTIAL_OPEN_1]
    assert instance.position_reporting_observed is False
    assert instance.display_position is None
    assert instance.display_position_estimated is False
    assert instance.position_simulation_action is None


async def test_send_dep_action_uses_fast_poll_for_non_movement_actions(hass: HomeAssistant) -> None:
    """Test non-movement DEP actions still trigger post-command fast polling."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client

    await instance._async_send_dep_action(DEP_ACTION_COURTESY_LIGHT, refresh=False)

    assert client.dep_actions == [DEP_ACTION_COURTESY_LIGHT]
    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL


async def test_post_command_fast_poll_window_keeps_idle_status_fast(hass: HomeAssistant) -> None:
    """Test idle status continues fast polling shortly after a command."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client

    await instance._async_send_action("stop", refresh=False)

    client.read_status_result = make_status(state="open", position=100.0)
    await instance._async_update_data()

    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL


async def test_expired_post_command_fast_poll_window_returns_to_idle(hass: HomeAssistant) -> None:
    """Test idle status returns to idle polling after the command window."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance._post_command_fast_poll_until_monotonic = 0.0

    client.read_status_result = make_status(state="open", position=100.0)
    await instance._async_update_data()

    assert instance.update_interval == coordinator_module.IDLE_UPDATE_INTERVAL
    assert instance._post_command_fast_poll_until_monotonic is None


async def test_moving_status_stays_fast_after_command_window_expires(hass: HomeAssistant) -> None:
    """Test motion keeps fast polling after the command window expires."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance._post_command_fast_poll_until_monotonic = 0.0

    client.read_status_result = make_status(state="opening", position=None)
    await instance._async_update_data()

    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL
    assert instance._post_command_fast_poll_until_monotonic is None


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


async def test_write_dmp_register_records_command_metadata(hass: HomeAssistant) -> None:
    """Test DMP register write success handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance._extended_status_next_refresh_monotonic = 999999.0

    await instance._async_write_dmp_register(0x04, 0x80, 1, refresh=False)

    assert client.dmp_writes == [(0x04, 0x80, 1, 1)]
    assert instance.connection_state == coordinator_module.CONNECTION_STATE_CONNECTED
    assert instance.last_command == "dmp_04_80_set"
    assert isinstance(instance.last_command_latency_ms, int)
    assert instance.update_interval == coordinator_module.MOVING_UPDATE_INTERVAL
    assert instance._extended_status_next_refresh_monotonic == 0.0


async def test_write_dmp_register_blocks_while_gate_is_moving(hass: HomeAssistant) -> None:
    """Test BusT4 config writes are blocked during motion."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.async_set_updated_data(make_status(state="opening"))

    with pytest.raises(HomeAssistantError, match="blocked while the gate is moving"):
        await instance.async_write_dmp_register(0x04, 0x80, 1)

    assert client.dmp_writes == []


async def test_write_dmp_register_blocks_aria_clbox_speed_at_backend(
    hass: HomeAssistant,
) -> None:
    """Test the coordinator enforces the CLBOX safety rule independently of entities."""
    instance = _coordinator(hass)
    client = FakeClient()
    instance.client = client
    instance.device_info = make_device_info(
        device_product="ARIA200S",
        device_description="CLBOX",
    )
    instance.async_set_updated_data(make_status(state="open"))

    with pytest.raises(HomeAssistantError, match="ARIA200S / CLBOX"):
        await instance.async_write_dmp_register(0x04, 0x42, 75)

    assert client.dmp_writes == []


async def test_write_dmp_register_wraps_connection_errors(hass: HomeAssistant) -> None:
    """Test DMP register write connection error handling."""
    instance = _coordinator(hass)
    client = FakeClient()
    client.write_dmp_register_error = NiceBidiConnectionError("offline")
    instance.client = client

    with pytest.raises(HomeAssistantError, match="DMP write failed"):
        await instance._async_write_dmp_register(0x04, 0x80, 1, refresh=False)

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

    assert NiceBidiDataUpdateCoordinator._has_encoder_calibration_data(open_status)
    assert not NiceBidiDataUpdateCoordinator._has_encoder_calibration_data(
        make_status(position=100.0, current_position=None, closed_position=None, open_position=None)
    )


def test_calibration_source_detection_handles_live_percent_and_scalar(
    hass: HomeAssistant,
) -> None:
    """Test non-encoder live position source detection."""
    instance = _coordinator(hass)
    percent_status = replace(
        make_status(
            state="opening",
            position=76.0,
            current_position=None,
            closed_position=None,
            open_position=None,
        ),
        registers={
            "NHK/T4InstantPosition": "76",
            "NHK/T4InstantPositionRaw": "76",
            "NHK/T4InstantPositionScale": "percent",
        },
    )

    percent_source = instance._calibration_position_source_for_status(percent_status)

    assert percent_source.mode == "live_percent"
    assert instance._calibration_percent_for_status(percent_status, percent_source) == 76.0

    scalar_status = replace(
        make_status(
            state="opening",
            position=50.0,
            current_position=None,
            closed_position=None,
            open_position=None,
        ),
        registers={
            "NHK/T4InstantPosition": "50",
            "NHK/T4InstantPositionRaw": "3500",
            "NHK/T4InstantPositionScale": "raw_0_7000",
        },
    )

    scalar_source = instance._calibration_position_source_for_status(scalar_status)
    scalar_source.scalar_closed_raw = 0
    scalar_source.scalar_open_raw = 7000

    assert scalar_source.mode == "live_scalar"
    assert instance._calibration_percent_for_status(scalar_status, scalar_source) == 50.0
    assert instance._raw_for_percent_from_source(scalar_source, 25.0) == 1750

    reverse_source = instance._calibration_position_source_for_status(scalar_status)
    instance._learn_live_scalar_bounds_from_travel(reverse_source, "open", 5000, 1000)
    assert reverse_source.scalar_closed_raw == 5000
    assert reverse_source.scalar_open_raw == 1000
    reverse_midpoint = replace(
        scalar_status,
        registers={
            "NHK/T4InstantPosition": "50",
            "NHK/T4InstantPositionRaw": "3000",
            "NHK/T4InstantPositionScale": "raw_0_7000",
        },
    )
    assert instance._calibration_percent_for_status(reverse_midpoint, reverse_source) == 50.0


def test_live_position_calibration_requires_dense_monotonic_coverage(
    hass: HomeAssistant,
) -> None:
    """Test coarse position events cannot be mistaken for target-grade feedback."""
    instance = _coordinator(hass)
    seed = make_status(
        state="opening",
        position=0.0,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    seed = replace(
        seed,
        registers={
            **seed.registers,
            "NHK/T4InstantPositionScale": "percent",
        },
    )
    source = instance._calibration_position_source_for_status(seed)
    source.observed_values = {0.0, 25.0, 50.0, 75.0, 100.0}
    source.monotonic_transitions = 4

    assert instance._calibration_source_can_measure_targets(source) is False
    assert instance._calibration_source_quality(source)["max_gap_percent"] == 25.0

    source.observed_values = {float(value) for value in range(0, 101, 10)}
    source.monotonic_transitions = 10

    assert instance._calibration_source_can_measure_targets(source) is True


def test_live_scalar_status_uses_calibration_bounds_for_display(
    hass: HomeAssistant,
) -> None:
    """Test calibrated live-scalar bounds replace the static 0..7000 display scale."""
    instance = _coordinator(hass)
    instance.calibration_profile = {
        "mode": "live_scalar",
        "bounds": {
            "live_scalar_closed_raw": 1000,
            "live_scalar_open_raw": 5000,
        },
    }
    status = replace(
        make_status(
            state="opening",
            position=28.6,
            current_position=None,
            closed_position=None,
            open_position=None,
        ),
        registers={
            "NHK/T4InstantPosition": "29",
            "NHK/T4InstantPositionRaw": "3000",
            "NHK/T4InstantPositionScale": "raw_0_7000",
        },
    )

    normalized = instance._normalize_status_for_display(status)

    assert normalized.position == 50.0
    assert normalized.registers["NHK/T4CalibratedPosition"] == "50.0"


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


def test_calibrated_stop_delay_uses_full_travel_speed(hass: HomeAssistant) -> None:
    """Test any calibrated profile can calculate set-position stop delays."""
    instance = _coordinator(hass)
    instance.calibration_profile = {
        "mode": "time",
        "travel_speed": {
            "open": {
                "speed_percent_per_second": 5.0,
            },
            "close": {
                "speed_percent_per_second": 4.0,
            },
        },
    }

    assert instance._calibrated_stop_delay_seconds(20.0, 70.0, "open") == 10.0
    assert instance._calibrated_stop_delay_seconds(70.0, 20.0, "close") == 12.5

    instance.calibration_profile = {
        "travel_speed": {
            "open": {
                "speed_percent_per_second": 5.0,
            }
        }
    }
    assert instance._calibrated_stop_delay_seconds(20.0, 70.0, "open") == 10.0


async def test_time_based_calibration_builds_full_travel_profile(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test calibration can fall back to timing when encoder data is missing."""
    instance = _coordinator(hass)
    actions: list[str] = []
    status_samples = [
        make_status(state="closed", position=0.0, current_position=None, closed_position=None, open_position=None),
    ]
    for _attempt in range(3):
        status_samples.extend(
            [
                make_status(state="closed", position=0.0, current_position=None, closed_position=None, open_position=None),
                make_status(state="opening", position=None, current_position=None, closed_position=None, open_position=None),
                make_status(state="open", position=100.0, current_position=None, closed_position=None, open_position=None),
                make_status(state="open", position=100.0, current_position=None, closed_position=None, open_position=None),
                make_status(state="closing", position=None, current_position=None, closed_position=None, open_position=None),
                make_status(state="closed", position=0.0, current_position=None, closed_position=None, open_position=None),
            ]
        )
    statuses = iter(status_samples)
    clock = count(0)

    async def fake_sleep(_delay: float) -> None:
        return None

    async def fake_read_motion_status() -> Any:
        return next(statuses)

    async def fake_send_action(action: str, **_kwargs: Any) -> None:
        actions.append(action)

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: next(clock))
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    profile = await instance._async_build_time_position_calibration(
        datetime.now(coordinator_module.UTC),
        make_status(state="closed", position=0.0, current_position=None, closed_position=None, open_position=None),
    )

    assert profile["mode"] == "time"
    assert profile["version"] == 6
    assert actions == ["open", "close", "open", "close", "open", "close"]
    assert profile["travel_speed"]["open"]["mode"] == "time"
    assert profile["travel_speed"]["open"]["selection_strategy"] == "median_duration"
    assert profile["travel_speed"]["open"]["measurement_count"] == 3
    assert len(profile["travel_speed"]["open"]["samples"]) == 3
    assert profile["travel_speed"]["open"]["speed_percent_per_second"] > 0
    assert profile["travel_speed"]["close"]["selection_strategy"] == "median_duration"
    assert profile["travel_speed"]["close"]["measurement_count"] == 3
    assert len(profile["travel_speed"]["close"]["samples"]) == 3
    assert profile["travel_speed"]["close"]["speed_percent_per_second"] > 0


def test_time_calibration_selects_median_duration_sample(hass: HomeAssistant) -> None:
    """Test time calibration selects the median full-travel duration."""
    instance = _coordinator(hass)
    selected = instance._select_time_travel_sample(
        "open",
        [
            {"attempt": 1, "duration_ms": 20000, "speed_percent_per_second": 5.0},
            {"attempt": 2, "duration_ms": 17000, "speed_percent_per_second": 5.88},
            {"attempt": 3, "duration_ms": 23000, "speed_percent_per_second": 4.35},
        ],
    )

    assert selected["attempt"] == 1
    assert selected["duration_ms"] == 20000
    assert selected["selected_attempt"] == 1
    assert selected["measurement_count"] == 3
    assert selected["duration_samples_ms"] == [20000, 17000, 23000]


async def test_time_full_travel_accepts_plausible_stopped_endpoint(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test time calibration can accept CU_WIFI stopped-at-endpoint reports."""
    instance = _coordinator(hass)
    actions: list[str] = []
    now = 0.0
    reads = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        now += delay

    def fake_monotonic() -> float:
        return now

    async def fake_read_motion_status() -> Any:
        nonlocal reads
        reads += 1
        if reads == 1:
            return make_status(
                state="open",
                position=100.0,
                current_position=None,
                closed_position=None,
                open_position=None,
            )
        if now < 20.0:
            return make_status(
                state="closing",
                position=None,
                current_position=None,
                closed_position=None,
                open_position=None,
            )
        return make_status(
            state="stopped",
            position=None,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_send_action(action: str, **_kwargs: Any) -> None:
        actions.append(action)

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module.time, "monotonic", fake_monotonic)
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    result = await instance._async_measure_full_travel_time("close", expected_duration_ms=20000)

    assert actions == ["close"]
    assert result["end_state"] == "stopped"
    assert result["endpoint_inferred_from_stopped"] is True
    assert result["stopped_duration_ratio"] >= coordinator_module.CALIBRATION_STOPPED_ENDPOINT_MIN_DURATION_RATIO


async def test_time_full_travel_rejects_early_stopped_endpoint(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test time calibration still rejects early stopped reports."""
    instance = _coordinator(hass)
    now = 0.0
    reads = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        now += delay

    def fake_monotonic() -> float:
        return now

    async def fake_read_motion_status() -> Any:
        nonlocal reads
        reads += 1
        if reads == 1:
            return make_status(
                state="open",
                position=100.0,
                current_position=None,
                closed_position=None,
                open_position=None,
            )
        if now < 5.0:
            return make_status(
                state="closing",
                position=None,
                current_position=None,
                closed_position=None,
                open_position=None,
            )
        return make_status(
            state="stopped",
            position=None,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_send_action(_action: str, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module.time, "monotonic", fake_monotonic)
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    with pytest.raises(HomeAssistantError, match="Position calibration stopped during full close"):
        await instance._async_measure_full_travel_time("close", expected_duration_ms=20000)


async def test_encoder_move_to_end_accepts_endpoint_after_stopped_confirmation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test encoder endpoint movement tolerates a delayed endpoint report."""
    instance = _coordinator(hass)
    actions: list[str] = []
    now = 0.0
    reads = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        now += delay

    def fake_monotonic() -> float:
        return now

    async def fake_read_motion_status() -> Any:
        nonlocal reads
        reads += 1
        if reads == 1:
            return make_status(
                state="stopped",
                position=53.9,
                current_position=2163,
                closed_position=0,
                open_position=4016,
            )
        if reads == 2:
            return make_status(
                state="opening",
                position=60.0,
                current_position=2410,
                closed_position=0,
                open_position=4016,
            )
        if reads == 3:
            return make_status(
                state="stopped",
                position=80.0,
                current_position=3213,
                closed_position=0,
                open_position=4016,
            )
        return make_status(
            state="open",
            position=100.0,
            current_position=4016,
            closed_position=0,
            open_position=4016,
        )

    async def fake_send_action(action: str, **_kwargs: Any) -> None:
        actions.append(action)

    monkeypatch.setattr(calibration_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(calibration_module.time, "monotonic", fake_monotonic)
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    result = await instance._async_move_to_end("open")

    assert actions == ["open"]
    assert result.state == "open"
    assert [event["message"] for event in instance._calibration_events] == [
        "Moving to open endpoint",
        "open endpoint reported stopped before endpoint confirmation",
        "Reached open endpoint after stopped confirmation",
    ]
    assert instance._calibration_events[1]["details"]["current_raw"] == 3213


async def test_encoder_move_to_end_rejects_stopped_before_endpoint(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test encoder endpoint movement still rejects real mid-travel stops."""
    instance = _coordinator(hass)
    now = 0.0
    reads = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        now += delay

    def fake_monotonic() -> float:
        return now

    async def fake_read_motion_status() -> Any:
        nonlocal reads
        reads += 1
        if reads == 1:
            return make_status(
                state="stopped",
                position=53.9,
                current_position=2163,
                closed_position=0,
                open_position=4016,
            )
        if reads == 2:
            return make_status(
                state="opening",
                position=60.0,
                current_position=2410,
                closed_position=0,
                open_position=4016,
            )
        return make_status(
            state="stopped",
            position=70.0,
            current_position=2811,
            closed_position=0,
            open_position=4016,
        )

    async def fake_send_action(_action: str, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(calibration_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(calibration_module.time, "monotonic", fake_monotonic)
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    with pytest.raises(
        HomeAssistantError,
        match="Position calibration stopped before reaching open endpoint",
    ):
        await instance._async_move_to_end("open")

    assert [event["message"] for event in instance._calibration_events] == [
        "Moving to open endpoint",
        "open endpoint reported stopped before endpoint confirmation",
    ]
    assert instance._calibration_events[1]["details"]["current_percent"] == 70.0


async def test_timed_set_position_ignores_stale_position_until_delay(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test time-calibrated set-position does not stop on stale endpoint position."""
    instance = _coordinator(hass)
    actions: list[str] = []
    reads = 0
    clock = count(0)

    async def fake_sleep(_delay: float) -> None:
        return None

    async def fake_read_motion_status() -> Any:
        nonlocal reads
        reads += 1
        return make_status(
            state="opening",
            position=100.0,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_send_action(action: str, **_kwargs: Any) -> None:
        actions.append(action)

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: next(clock))
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    await instance._async_stop_at_position(50, "open", stop_delay_seconds=2.0)

    assert actions == ["stop"]
    assert reads == 1


@pytest.mark.parametrize("reported_state", ["stopped", "open", "closed", None])
async def test_timed_set_position_stops_even_if_status_is_stale(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    reported_state: str | None,
) -> None:
    """Test the hard stop timer is not cancelled by a stale terminal state."""
    instance = _coordinator(hass)
    actions: list[str] = []
    clock = 0.0

    async def fake_sleep(delay: float) -> None:
        nonlocal clock
        clock += delay

    async def fake_read_motion_status() -> Any:
        return make_status(
            state=reported_state,
            position=None,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_send_action(action: str, **_kwargs: Any) -> None:
        actions.append(action)

    monkeypatch.setattr(position_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(position_module.time, "monotonic", lambda: clock)
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    await instance._async_stop_at_position(50, "open", stop_delay_seconds=2.0)

    assert actions == ["stop"]
    assert clock == pytest.approx(2.0)


async def test_timed_set_position_rebases_deadline_from_live_position(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test calibrated timing is corrected by plausible live position updates."""
    instance = _coordinator(hass)
    actions: list[str] = []
    clock = 0.0
    reads = 0
    instance.calibration_profile = {
        "travel_speed": {
            "open": {
                "speed_percent_per_second": 10.0,
            }
        }
    }

    async def fake_sleep(delay: float) -> None:
        nonlocal clock
        clock += delay

    async def fake_read_motion_status() -> Any:
        nonlocal reads
        reads += 1
        return make_status(
            state="opening",
            position=10.0,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_send_action(action: str, **_kwargs: Any) -> None:
        actions.append(action)

    monkeypatch.setattr(position_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(position_module.time, "monotonic", lambda: clock)
    instance._async_read_motion_status = fake_read_motion_status
    instance._async_send_action = fake_send_action

    await instance._async_stop_at_position(
        80,
        "open",
        stop_delay_seconds=8.0,
        start_position=0.0,
        stop_position=80.0,
    )

    assert actions == ["stop"]
    assert reads == 15
    assert clock == pytest.approx(7.5)


async def test_position_calibration_falls_back_to_time_profile_without_encoder(
    hass: HomeAssistant,
) -> None:
    """Test the main calibration builder uses the non-encoder standardized path."""
    instance = _coordinator(hass)

    async def fake_read_motion_status() -> Any:
        return make_status(
            state="closed",
            position=0.0,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_non_encoder_calibration(
        _started_at: datetime,
        status: Any,
        source: Any,
    ) -> dict[str, Any]:
        assert status.current_position is None
        assert source.mode == "time"
        return {"mode": "time", "travel_speed": {}}

    instance._async_read_motion_status = fake_read_motion_status
    instance._async_build_non_encoder_position_calibration = fake_non_encoder_calibration

    profile = await instance._async_build_position_calibration()

    assert profile["mode"] == "time"


async def test_standardized_calibration_promotes_live_percent_to_target_sequence(
    hass: HomeAssistant,
) -> None:
    """Test live-percent devices get the same target calibration sequence."""
    instance = _coordinator(hass)
    start_status = make_status(
        state="closed",
        position=0.0,
        current_position=None,
        closed_position=None,
        open_position=None,
    )
    source = instance._calibration_position_source_for_status(start_status)
    measured_actions: list[str] = []
    calibrated_targets: list[tuple[str, int]] = []

    async def fake_move(action: str, _source: Any) -> Any:
        return make_status(
            state="open" if action == "open" else "closed",
            position=100.0 if action == "open" else 0.0,
            current_position=None,
            closed_position=None,
            open_position=None,
        )

    async def fake_measure(action: str, source_arg: Any, **_kwargs: Any) -> dict[str, Any]:
        source_arg.mode = "live_percent"
        source_arg.observed_values.update({0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0})
        source_arg.monotonic_transitions = 10
        measured_actions.append(action)
        return {
            "action": action,
            "mode": source_arg.mode,
            "start_percent": 0.0 if action == "open" else 100.0,
            "end_percent": 100.0 if action == "open" else 0.0,
            "duration_ms": 20000,
            "distance_percent": 100.0,
            "speed_percent_per_second": 5.0,
        }

    async def fake_calibrate(target: int, action: str, _endpoint_action: str, source_arg: Any) -> dict[str, Any]:
        calibrated_targets.append((action, target))
        return {
            "action": action,
            "mode": source_arg.mode,
            "valid": True,
            "failure_reason": None,
            "target_percent": target,
            "corrected_stop_percent": float(target),
            "final_percent": float(target),
            "error_percent": 0.0,
            "stop_command_latency_ms": 20,
            "move_duration_ms": 1000,
            "successful": True,
            "successful_attempts": 2,
            "stability_attempts": 2,
            "attempts_used": 2,
            "selection_strategy": "stable_window",
            "selected_attempt": 2,
            "selected_attempts": [1, 2],
            "selected_window_avg_abs_error_percent": 0.0,
            "selected_abs_error_percent": 0.0,
            "ignored_outlier_attempts": [],
            "ignored_invalid_attempts": [],
            "outlier_error_percent": 15.0,
            "attempts": [],
        }

    instance._async_move_to_end_with_position_source = fake_move
    instance._async_measure_full_travel_with_position_source = fake_measure
    instance._async_calibrate_target_from_endpoint_with_position_source = fake_calibrate

    profile = await instance._async_build_non_encoder_position_calibration(
        datetime.now(coordinator_module.UTC),
        start_status,
        source,
    )

    assert profile["mode"] == "live_percent"
    assert measured_actions == ["open", "close"]
    assert calibrated_targets == [
        ("open", 20),
        ("open", 40),
        ("open", 60),
        ("open", 80),
        ("close", 80),
        ("close", 60),
        ("close", 40),
        ("close", 20),
    ]
    assert profile["targets"] == [20, 40, 60, 80]
    assert len(profile["samples"]["open"]) == 4
    assert len(profile["samples"]["close"]) == 4


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


async def test_calibration_cancellation_records_reason_and_final_outcome(
    hass: HomeAssistant,
) -> None:
    """Test cancellation produces a useful report instead of a generic error."""
    instance = _coordinator(hass)
    started = calibration_module.asyncio.Event()
    never_finishes = calibration_module.asyncio.Event()
    logged: list[tuple[dict[str, Any], str]] = []

    async def fake_build_position_calibration() -> dict[str, Any]:
        started.set()
        await never_finishes.wait()
        raise AssertionError("unreachable")

    instance._async_build_position_calibration = fake_build_position_calibration
    instance._log_calibration_report = lambda report, reason: logged.append((report, reason))

    await instance.async_start_position_calibration()
    await started.wait()
    await instance._async_cancel_calibration(reason="reconnect", stop=False)

    assert instance.calibration_state == calibration_module.CALIBRATION_STATE_CANCELLED
    assert instance.calibration_last_error == "cancelled: reconnect"
    assert instance.calibration_cancel_reason == "reconnect"
    assert instance.calibration_cancel_stop_requested is False
    assert instance.calibration_cancel_stop_sent is False
    assert instance.calibration_report["events"][-1]["details"] == {
        "cancellation_reason": "reconnect",
        "stop_requested": False,
        "stop_sent": False,
        "stop_error": None,
    }
    assert logged[-1][1] == "cancelled (reconnect)"


async def test_load_calibration_builds_report_from_stored_profile(
    hass: HomeAssistant,
) -> None:
    """Test loading a stored calibration profile."""
    profile = _calibration_profile()
    instance = _coordinator(hass)
    instance._calibration_store = FakeStore(profile)

    await instance.async_load_calibration()

    assert instance.calibration_profile is not profile
    assert instance.calibration_profile["mode"] == "encoder"
    assert instance._calibration_store.saved == instance.calibration_profile
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


def test_time_calibration_report_summary_attributes_and_formatting(
    hass: HomeAssistant,
) -> None:
    """Test report generation for time-based calibration profiles."""
    instance = _coordinator(hass)
    instance.calibration_state = coordinator_module.CALIBRATION_STATE_CALIBRATED
    profile = {
        "version": 6,
        "mode": "time",
        "updated_at": "2026-07-08T10:05:00+00:00",
        "poll_seconds": 0.5,
        "settle_seconds": 2.0,
        "command_pause_seconds": 0.5,
        "max_attempts": 1,
        "stability_attempts": 1,
        "target_tolerance_percent": 2.0,
        "bounds": {"mode": "time"},
        "travel_speed": {
            "open": {
                "mode": "time",
                "speed_percent_per_second": 4.0,
                "duration_ms": 25000,
                "start_percent": 0.0,
                "end_percent": 100.0,
                "measurement_count": 3,
                "selected_attempt": 2,
                "duration_samples_ms": [26000, 25000, 27000],
            },
            "close": {
                "mode": "time",
                "speed_percent_per_second": 5.0,
                "duration_ms": 20000,
                "start_percent": 100.0,
                "end_percent": 0.0,
                "measurement_count": 3,
                "selected_attempt": 1,
                "duration_samples_ms": [20000, 21000, 19000],
            },
        },
        "samples": {"open": [], "close": []},
        "events": [],
    }

    report = instance._build_calibration_report(profile)
    instance.calibration_report = report

    assert report["quality"] == "time_based"
    assert report["profile_mode"] == "time"
    assert report["point_count"] == 0
    assert instance.calibration_report_summary.startswith("time_based:")
    assert "samples open=3 close=3" in instance.calibration_report_summary

    attrs = instance.calibration_report_attributes
    assert attrs["profile_mode"] == "time"
    assert attrs["travel_speed"]["close"]["speed_percent_per_second"] == 5.0
    assert attrs["travel_speed"]["close"]["measurement_count"] == 3

    formatted = instance._format_calibration_report(report)
    assert "Profile mode: time" in formatted
    assert "Full-travel speed:" in formatted
    assert "measurements=3 selected_attempt=2 duration_samples=[26000, 25000, 27000]" in formatted


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
