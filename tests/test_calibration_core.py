"""Tests for pure calibration lifecycle, estimator, and persistence."""

from __future__ import annotations

import pytest

from custom_components.nice_bidiwifi.calibration_estimator import (
    interpolate_stop_percent,
    median_time_travel_duration_ms,
    select_calibration_sample,
)
from custom_components.nice_bidiwifi.calibration_state import (
    CalibrationState,
    CalibrationStateMachine,
)
from custom_components.nice_bidiwifi.calibration_storage import (
    migrate_calibration_profile,
)


def test_calibration_state_machine_accepts_lifecycle_and_rejects_skips() -> None:
    """Lifecycle rules are testable without Home Assistant or a client."""
    machine = CalibrationStateMachine()

    assert machine.transition("running") is CalibrationState.RUNNING
    assert machine.transition("calibrated") is CalibrationState.CALIBRATED
    assert machine.transition("running") is CalibrationState.RUNNING
    assert machine.transition("cancelled") is CalibrationState.CANCELLED

    with pytest.raises(ValueError, match="Invalid calibration transition"):
        machine.transition("failed")


def test_calibration_profile_migration_is_versioned_and_non_destructive() -> None:
    """Legacy profiles receive current structural defaults without mutation."""
    legacy = {
        "version": 1,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    migrated, changed = migrate_calibration_profile(legacy)

    assert changed is True
    assert migrated is not legacy
    assert migrated["version"] == 5
    assert migrated["mode"] == "encoder"
    assert migrated["samples"] == {"open": [], "close": []}
    assert "mode" not in legacy


def test_current_calibration_profile_preserves_identity() -> None:
    """A complete current profile is not rewritten on every load."""
    profile = {
        "version": 6,
        "mode": "time",
        "targets": [],
        "bounds": {},
        "travel_speed": {},
        "samples": {"open": [], "close": []},
        "events": [],
    }

    current, changed = migrate_calibration_profile(profile)

    assert current is profile
    assert changed is False


def test_calibration_estimators_select_stable_low_error_samples() -> None:
    """Pure estimators preserve stable-window and median behavior."""
    attempts = [
        {"attempt": 1, "error_percent": 12.0, "valid": True},
        {"attempt": 2, "error_percent": 1.0, "valid": True},
        {"attempt": 3, "error_percent": -0.5, "valid": True},
    ]

    selection = select_calibration_sample(
        attempts,
        tolerance_percent=2.0,
        stability_attempts=2,
        outlier_error_percent=15.0,
    )

    assert selection["strategy"] == "stable_window"
    assert selection["selected_attempt"] == 3
    assert median_time_travel_duration_ms(
        [{"duration_ms": 3000}, {"duration_ms": 1000}, {"duration_ms": 2000}]
    ) == 2000
    assert interpolate_stop_percent(50, [(20, 18), (80, 75)]) == 46.5
