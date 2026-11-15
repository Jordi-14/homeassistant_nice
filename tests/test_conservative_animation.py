"""Animation should extrapolate conservatively and retain newer live readings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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


@pytest.mark.parametrize(("state", "position", "direction"), [
    ("opening", 40.0, 1), ("closing", 60.0, -1),
])
async def test_duplicate_and_lagging_readings_keep_movement_continuous(hass, monkeypatch, state, position, direction):
    coordinator, clock = _coordinator(hass, monkeypatch)
    coordinator._sync_position_simulation_from_status(make_status(state=state, position=position))
    previous = position
    for _ in range(12):
        clock[0] += 0.25
        predicted = coordinator.display_position
        assert 0 < direction * (predicted - previous) <= 1
        coordinator.async_set_updated_data(make_status(state=state, position=position))
        coordinator._sync_position_simulation_from_status(coordinator.data)
        assert coordinator.data.position == position
        assert coordinator.display_position == predicted
        previous = predicted
    ahead = previous + direction * 5
    coordinator.async_set_updated_data(make_status(state=state, position=ahead))
    coordinator._sync_position_simulation_from_status(coordinator.data)
    assert coordinator.display_position == pytest.approx(ahead)
    coordinator.async_set_updated_data(make_status(state="stopped", position=position))
    coordinator._sync_position_simulation_from_status(coordinator.data)
    assert coordinator.display_position == position
    assert coordinator.display_position_estimated is False
    await coordinator.async_shutdown()


async def test_recorded_cycle_has_no_backwards_corrections_while_moving(hass, monkeypatch):
    coordinator, clock = _coordinator(hass, monkeypatch)
    samples = json.loads((Path(__file__).parent / "fixtures/position_cycle_b13.json").read_text())
    previous_state = "closed"
    previous = 0.0
    for sample in samples:
        end = 100 + sample["seconds"]
        while clock[0] + 0.25 < end:
            clock[0] += 0.25
            current = coordinator.display_position
            if previous_state == "opening":
                assert current >= previous
            elif previous_state == "closing":
                assert current <= previous
            previous = current
        clock[0] = end
        status = make_status(state=sample["state"], position=sample["position"])
        coordinator.async_set_updated_data(status)
        coordinator._sync_position_simulation_from_status(status)
        current = coordinator.display_position
        if sample["state"] == previous_state == "opening":
            assert current >= previous
        elif sample["state"] == previous_state == "closing":
            assert current <= previous
        assert coordinator.data.position == sample["position"]
        previous, previous_state = current, sample["state"]
    await coordinator.async_shutdown()


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
async def test_recorded_slowdown_does_not_rewind_the_display(hass, monkeypatch, state, anchor, next_position, seconds):
    coordinator, clock = _coordinator(hass, monkeypatch)
    action = "open" if state == "opening" else "close"
    coordinator._observed_position_speed_percent_per_second[action] = 6.7
    coordinator._sync_position_simulation_from_status(make_status(state=state, position=anchor))
    clock[0] += seconds
    predicted = coordinator.display_position
    if action == "open":
        assert anchor < predicted
    else:
        assert predicted < anchor
    coordinator._sync_position_simulation_from_status(make_status(state=state, position=next_position))
    expected = max(predicted, next_position) if action == "open" else min(predicted, next_position)
    assert coordinator.display_position == expected
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


async def test_prediction_continues_at_a_steady_speed_between_readings(hass, monkeypatch):
    coordinator, clock = _coordinator(hass, monkeypatch)
    coordinator._sync_position_simulation_from_status(make_status(state="opening", position=50.0))
    clock[0] += 1
    first = coordinator.display_position
    clock[0] += 3
    second = coordinator.display_position
    assert first == pytest.approx(53.5)
    assert second == pytest.approx(63.9)
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
