"""Passive learning, calibration and persistence share measured band data."""

import pytest

from custom_components.nice_bidiwifi import async_remove_entry
from custom_components.nice_bidiwifi.calibration_constants import (
    CALIBRATION_STATE_RUNNING,
)
from custom_components.nice_bidiwifi.position import movement_profile_store
from tests.conftest import make_status
from tests.test_conservative_animation import _coordinator
from tests.test_coordinator import FakeStore
from tests.test_movement_profile import train


async def test_learning_survives_reload_without_changing_calibration(hass, monkeypatch, hass_storage):
    instance, clock = _coordinator(hass, monkeypatch)
    for p, t in ((0, 100), (5, 101), (10, 102)):
        clock[0] = t
        status = make_status(state="opening", position=p)
        instance._store_successful_status(status)
        instance.async_set_updated_data(status)
    instance._sync_position_simulation_from_status(make_status(state="stopped", position=10))
    original_calibration = instance.calibration_profile
    key = instance.config_entry.entry_id
    await instance.async_shutdown()

    restored, _ = _coordinator(hass, monkeypatch)
    restored._movement_profile_store = movement_profile_store(hass, key)
    await restored.async_load_movement_profile()
    assert restored.movement_speed_profile["open"][0]["speed_percent_per_second"] == 5
    assert restored.movement_speed_profile["open"][0]["samples"] == 1
    assert instance.calibration_profile is original_calibration
    assert instance.client.actions == []
    await restored.async_shutdown()
    await async_remove_entry(hass, instance.config_entry)
    assert f"nice_bidiwifi.movement_profile.{key}" not in hass_storage


@pytest.mark.parametrize("mode", ["encoder", "live_scalar"])
async def test_calibration_collects_the_same_real_samples_while_animation_is_disabled(hass, monkeypatch, mode):
    instance, clock = _coordinator(hass, monkeypatch)
    instance._calibration_store = FakeStore()
    instance.calibration_state = CALIBRATION_STATE_RUNNING

    async def build():
        for p, t in ((0, 100), (5, 101), (10, 102)):
            clock[0] = t
            instance._store_successful_status(make_status(state="opening", position=p))
            assert instance.position_simulation_action is None
        return {"version": 5, "mode": mode, "bounds": {"open": 5000}, "targets": [],
                "samples": {"open": [], "close": []}, "travel_speed": {}}

    instance._async_build_position_calibration = build
    await instance._async_run_position_calibration()
    expected = [5.0] if mode == "encoder" else []
    assert instance.calibration_profile["movement_speed_profile"]["samples"]["open"][0] == expected
    assert instance._calibration_store.saved["movement_speed_profile"]["samples"]["open"][0] == expected
    assert instance.client.actions == []
    await instance.async_shutdown()


async def test_display_crosses_speed_bands_without_a_new_reading(hass, monkeypatch):
    instance, clock = _coordinator(hass, monkeypatch)
    train(instance._movement_profile, "open", (1, 2, 6, 6, 2, 1), start=0)
    status = make_status(state="opening", position=0)
    instance.async_set_updated_data(status)
    instance._sync_position_simulation_from_status(status)
    initial_speed = instance.position_simulation_speed_percent_per_second
    previous = 0
    for _ in range(140):
        clock[0] += 0.25
        current = instance.display_position
        assert current >= previous
        previous = current
    assert instance.data.position == 0
    assert instance.position_simulation_speed_percent_per_second != initial_speed
    assert 50 < previous < 99
    await instance.async_shutdown()

