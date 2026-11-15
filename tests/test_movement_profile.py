"""Directional profiles learn real band timings and blend without jumps."""

import json
import math

import pytest

from custom_components.nice_bidiwifi.movement_profile import (
    BANDS,
    MovementProfile,
    advance,
    speed_at,
)


def train(model, action, speeds, start=0.0, accepted=True):
    positions = list(range(101)) if action == "open" else list(range(100, -1, -1))
    now = start
    model.observe(action, positions[0], now)
    for previous, position in zip(positions, positions[1:], strict=False):
        middle = (previous + position) / 2
        i = next(i for i in range(6) if BANDS[i] <= middle < BANDS[i + 1])
        now += 1 / speeds[i]
        model.observe(action, position, now)
    assert model.finish() is accepted
    return now


def test_separate_opening_and_closing_bands():
    model = MovementProfile()
    train(model, "open", (1, 2, 5, 5, 2, 1))
    train(model, "close", (0.5, 1, 4, 4, 1, 0.5), 100)
    for action, expected in (("open", (1, 2, 5, 5, 2, 1)), ("close", (0.5, 1, 4, 4, 1, 0.5))):
        assert model.curve(action, 3, factor=1) == pytest.approx(expected)
        assert all(row["samples"] == 1 for row in model.summary()[action])


def test_slow_start_does_not_limit_cruising_and_margin_is_ninety_percent():
    model = MovementProfile()
    train(model, "open", (1, 2, 6, 6, 2, 1))
    curve = model.curve("open", 3)
    assert speed_at(5, curve) == pytest.approx(0.9)
    assert speed_at(35, curve) == pytest.approx(5.4)
    assert speed_at(10 - 1e-6, curve) == pytest.approx(speed_at(10 + 1e-6, curve), abs=1e-6)


def test_one_sample_per_movement_and_median_rejects_single_bad_pass():
    model = MovementProfile()
    for number, speed in enumerate((2, 2, 2, 20, 2)):
        train(model, "open", (speed,) * 6, start=number * 200, accepted=speed != 20)
    assert model.curve("open", 1, factor=1) == pytest.approx((2,) * 6)
    assert all(row["samples"] == 4 for row in model.summary()["open"])
    assert model.finish() is False


def test_duplicates_backwards_readings_and_reply_bursts_are_not_training_samples():
    model = MovementProfile()
    for p, t in ((0, 0), (5, 1), (5, 1.1), (3, 1.2), (80, 1.21), (10, 2)):
        model.observe("open", p, t)
    assert model.finish()
    assert model.samples["open"][0][0] == pytest.approx(5)
    assert all(not values for values in model.samples["open"][1:])


def test_partial_travel_only_teaches_covered_band_and_fallback_covers_the_rest():
    model = MovementProfile()
    for p, t in ((21, 0), (25, 1), (29, 2)):
        model.observe("open", p, t)
    assert model.finish()
    assert model.curve("open", 3, factor=1) == pytest.approx((3, 3, 4, 3, 3, 3))


def test_large_gaps_and_idle_time_do_not_form_a_speed_measurement():
    model = MovementProfile()
    model.observe("open", 0, 0)
    model.observe("open", 3, 1)
    model.observe("open", 4, 40)
    assert model.finish() is False


def test_saved_profile_roundtrip_and_corrupt_values_are_ignored():
    model = MovementProfile()
    model.setting_changed("open", 90)
    train(model, "open", (1, 2, 5, 5, 2, 1))
    restored = MovementProfile()
    restored.load(json.loads(json.dumps(model.export())))
    assert restored.export() == model.export()
    broken = model.export()
    broken["samples"]["open"][0] = [True, math.nan, math.inf, -1, "oops", 1.0]
    restored.load(broken)
    assert list(restored.samples["open"][0]) == [1.0]
    restored.load({"version": 50})
    assert list(restored.samples["open"][0]) == [1.0]


def test_motor_speed_change_invalidates_only_that_direction():
    model = MovementProfile()
    model.setting_changed("open", 90)
    train(model, "open", (2,) * 6)
    train(model, "close", (3,) * 6, 200)
    assert model.setting_changed("open", 80)
    assert all(not values for values in model.samples["open"])
    assert all(values for values in model.samples["close"])


@pytest.mark.parametrize("action", ["open", "close"])
def test_integrating_curved_speed_matches_small_step_reference(action):
    curve = (0.9, 1.8, 5.4, 5.4, 1.8, 0.9)
    start = 0 if action == "open" else 100
    direction = 1 if action == "open" else -1
    expected = float(start)
    for _ in range(20000):
        expected += direction * speed_at(expected, curve) * 0.001
    assert advance(start, 20, action, curve) == pytest.approx(expected, abs=0.01)
    assert advance(start, 1000, action, curve) == (100 if action == "open" else 0)
