"""Animation should extrapolate conservatively and retain newer live readings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi import position as position_module
from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from custom_components.nice_bidiwifi.models.events import (
    NiceEvent,
    NiceEventCategory,
    NiceEventKind,
)
from tests.conftest import FakeClient, config_entry_data, make_status


def _coordinator(hass, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(position_module.time, "monotonic", lambda: clock[0])
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data())
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator.client = FakeClient()
    coordinator.calibration_profile = {
        "mode": "encoder",
        "travel_speed": {"open": {"speed_percent_per_second": 4.33}, "close": {"speed_percent_per_second": 3.75}},
    }
    coordinator.async_set_updated_data(make_status(state="closed", position=0.0, current_position=0, open_position=4133))
    return coordinator, clock


@pytest.mark.parametrize(("state", "anchor", "next_position", "seconds"), [
    ("opening", 83.1, 85.6, 2.31),
    ("closing", 14.1, 12.5, 2.17),
])
async def test_recorded_slowdown_keeps_prediction_behind_next_reading(hass, monkeypatch, state, anchor, next_position, seconds):
    coordinator, clock = _coordinator(hass, monkeypatch)
    action = "open" if state == "opening" else "close"
    coordinator._observed_position_speed_percent_per_second[action] = 6.7
    coordinator._sync_position_simulation_from_status(make_status(state=state, position=anchor))
    clock[0] += seconds
    predicted = coordinator.display_position
    if action == "open":
        assert anchor < predicted <= next_position
    else:
        assert next_position <= predicted < anchor
    coordinator._sync_position_simulation_from_status(make_status(state=state, position=next_position))
    assert coordinator.display_position == next_position
    await coordinator.async_shutdown()


async def test_both_speed_sources_keep_a_conservative_margin(hass, monkeypatch):
    coordinator, _ = _coordinator(hass, monkeypatch)
    assert coordinator._position_simulation_speed("open") == pytest.approx(3.464)
    coordinator._observed_position_speed_percent_per_second["open"] = 10.0
    assert coordinator._position_simulation_speed("open") == pytest.approx(3.464)
    coordinator._observed_position_speed_percent_per_second["open"] = 2.0
    assert coordinator._position_simulation_speed("open") == pytest.approx(1.6)
    await coordinator.async_shutdown()


async def test_a_fast_burst_does_not_erase_recent_slower_motion(hass, monkeypatch):
    coordinator, clock = _coordinator(hass, monkeypatch)
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=20.0))
    clock[0] += 5
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=30.0))
    clock[0] += 0.25
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=31.0))
    clock[0] += 0.25
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=32.0))
    assert coordinator.position_simulation_speed_percent_per_second <= 1.6
    await coordinator.async_shutdown()


async def test_prediction_continues_past_real_reading_but_eases_as_data_ages(hass, monkeypatch):
    coordinator, clock = _coordinator(hass, monkeypatch)
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=50.0))
    clock[0] += 1
    first = coordinator.display_position
    clock[0] += 3
    second = coordinator.display_position
    assert 50.0 < first < second < 53.0
    await coordinator.async_shutdown()


async def test_animation_waits_for_controller_motion_confirmation(hass, monkeypatch):
    coordinator, clock = _coordinator(hass, monkeypatch)
    coordinator._start_position_simulation("open")
    clock[0] += 1
    assert coordinator.display_position == 0.0
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=0.0))
    clock[0] += 1
    assert coordinator.display_position > 0.0
    await coordinator.async_shutdown()


async def test_position_target_uses_reported_value_instead_of_lagging_display(hass, monkeypatch):
    coordinator, _ = _coordinator(hass, monkeypatch)
    coordinator.async_set_updated_data(make_status(state="opening", position=90.0, current_position=3720, open_position=4133))
    coordinator._rebase_position_simulation("open", 70.0, confirmed_moving=True, keep_target=False)
    assert coordinator.display_position == 70.0
    await coordinator.async_set_position(80)
    assert coordinator.client.actions == ["close"]
    assert coordinator.display_position == 90.0
    await coordinator._async_cancel_position_target()
    await coordinator.async_shutdown()


@pytest.mark.parametrize(("live_state", "live_position"), [("opening", 27.3), ("closing", 35.0)])
async def test_poll_started_before_live_update_cannot_replace_it(hass, monkeypatch, live_state, live_position):
    coordinator, _ = _coordinator(hass, monkeypatch)
    coordinator.async_set_updated_data(make_status(state="opening", position=20.0, current_position=800, open_position=4133))
    original = hass.async_add_executor_job
    old_poll = make_status(state="opening", position=26.5, current_position=1095, open_position=4133)

    async def read_with_intervening_live_update(function, *args):
        if function == coordinator._read_status_and_maybe_info:
            coordinator.event_controller._apply_event(NiceEvent(
                kind=NiceEventKind.CHANGE, category=NiceEventCategory.STATE_CHANGE,
                received_at=datetime.now(UTC), state=live_state, raw_state=live_state,
                position=live_position,
            ))
            return old_poll
        return await original(function, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", read_with_intervening_live_update)
    result = await coordinator._async_update_data()
    assert result.state == live_state
    assert result.position == live_position
    assert result.registers["NHK/PollPositionIgnored"] == "newer_live_update"
    await coordinator.async_shutdown()


async def test_a_later_poll_can_report_a_real_reversal(hass, monkeypatch):
    coordinator, _ = _coordinator(hass, monkeypatch)
    coordinator.async_set_updated_data(make_status(state="opening", position=50.0))
    coordinator.client.read_status_result = make_status(state="closing", position=45.0)
    result = await coordinator._async_update_data()
    assert result.state == "closing"
    assert result.position == 45.0
    assert "NHK/PollPositionIgnored" not in result.registers
    await coordinator.async_shutdown()
