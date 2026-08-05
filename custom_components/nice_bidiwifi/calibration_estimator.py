"""Pure calibration sample selection and interpolation."""

from __future__ import annotations

from typing import Any

from .errors import NiceCalibrationError


def median_time_travel_duration_ms(
    samples: list[dict[str, Any]],
) -> int | None:
    """Return the median valid duration from full-travel samples."""
    durations = [
        sample["duration_ms"]
        for sample in samples
        if isinstance(sample.get("duration_ms"), int)
        and sample["duration_ms"] > 0
    ]
    if not durations:
        return None
    durations.sort()
    return durations[len(durations) // 2]


def select_time_travel_sample(
    action: str,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select the median-duration sample for one direction."""
    valid_samples = [
        sample
        for sample in samples
        if isinstance(sample.get("duration_ms"), int)
        and sample["duration_ms"] > 0
    ]
    if not valid_samples:
        raise NiceCalibrationError(
            f"Position calibration produced no timed {action} samples"
        )

    selected = sorted(
        valid_samples,
        key=lambda sample: sample["duration_ms"],
    )[len(valid_samples) // 2]
    result = dict(selected)
    result.update(
        {
            "selection_strategy": "median_duration",
            "selected_attempt": selected.get("attempt"),
            "measurement_count": len(valid_samples),
            "duration_samples_ms": [
                sample["duration_ms"] for sample in valid_samples
            ],
            "speed_samples_percent_per_second": [
                sample.get("speed_percent_per_second")
                for sample in valid_samples
            ],
            "samples": valid_samples,
        }
    )
    return result


def calibration_attempt_valid(attempt: dict[str, Any]) -> bool:
    """Return whether an attempt may be used for learned thresholds."""
    return attempt.get("valid", True) is not False


def calibration_attempt_abs_error(attempt: dict[str, Any]) -> float:
    """Return an attempt's absolute percentage error."""
    if not calibration_attempt_valid(attempt):
        return 1000.0
    try:
        return abs(float(attempt.get("error_percent", 1000.0)))
    except (TypeError, ValueError):
        return 1000.0


def calibration_attempt_successful(
    attempt: dict[str, Any],
    *,
    tolerance_percent: float,
) -> bool:
    """Return whether an attempt finished inside the target tolerance."""
    return (
        calibration_attempt_valid(attempt)
        and calibration_attempt_abs_error(attempt) <= tolerance_percent
    )


def calibration_success_count(
    attempts: list[dict[str, Any]],
    *,
    tolerance_percent: float,
) -> int:
    """Return the number of successful attempts."""
    return sum(
        calibration_attempt_successful(
            attempt,
            tolerance_percent=tolerance_percent,
        )
        for attempt in attempts
    )


def calibration_attempts_stable(
    attempts: list[dict[str, Any]],
    *,
    tolerance_percent: float,
    stability_attempts: int,
) -> bool:
    """Return whether any consecutive attempts are repeatably accurate."""
    if len(attempts) < stability_attempts:
        return False
    for start in range(0, len(attempts) - stability_attempts + 1):
        window = attempts[start : start + stability_attempts]
        if (
            calibration_success_count(
                window,
                tolerance_percent=tolerance_percent,
            )
            == stability_attempts
        ):
            return True
    return False


def select_calibration_sample(
    attempts: list[dict[str, Any]],
    *,
    tolerance_percent: float,
    stability_attempts: int,
    outlier_error_percent: float,
) -> dict[str, Any]:
    """Choose the calibration result to store for one target."""
    if not attempts:
        raise NiceCalibrationError("Position calibration produced no attempts")

    ignored_invalid_attempts = [
        int(attempt["attempt"])
        for attempt in attempts
        if not calibration_attempt_valid(attempt)
        and isinstance(attempt.get("attempt"), int)
    ]
    valid_attempts = [
        attempt for attempt in attempts if calibration_attempt_valid(attempt)
    ]
    ignored_outliers = [
        int(attempt["attempt"])
        for attempt in valid_attempts
        if calibration_attempt_abs_error(attempt) > outlier_error_percent
        and isinstance(attempt.get("attempt"), int)
    ]
    stable_windows: list[tuple[float, int, list[dict[str, Any]]]] = []
    for start in range(0, len(attempts) - stability_attempts + 1):
        window = attempts[start : start + stability_attempts]
        if (
            calibration_success_count(
                window,
                tolerance_percent=tolerance_percent,
            )
            != stability_attempts
        ):
            continue
        average_error = sum(
            calibration_attempt_abs_error(attempt)
            for attempt in window
        ) / len(window)
        stable_windows.append((average_error, start, window))

    if stable_windows:
        average_error, _, selected_window = min(
            stable_windows,
            key=lambda item: (item[0], -item[1]),
        )
        selected_sample = min(
            selected_window,
            key=calibration_attempt_abs_error,
        )
        return _selection(
            selected_sample,
            strategy="stable_window",
            selected_attempts=[
                attempt.get("attempt") for attempt in selected_window
            ],
            average_error=round(average_error, 2),
            ignored_outliers=ignored_outliers,
            ignored_invalid=ignored_invalid_attempts,
        )

    non_outlier_attempts = [
        attempt
        for attempt in valid_attempts
        if calibration_attempt_abs_error(attempt) <= outlier_error_percent
    ]
    candidates = non_outlier_attempts or valid_attempts
    if not candidates:
        selected_sample = attempts[-1]
        return {
            "sample": selected_sample,
            "strategy": "no_valid_attempt",
            "selected_attempt": selected_sample.get("attempt"),
            "selected_attempts": [],
            "selected_window_avg_abs_error_percent": None,
            "selected_abs_error_percent": None,
            "ignored_outlier_attempts": ignored_outliers,
            "ignored_invalid_attempts": ignored_invalid_attempts,
        }

    selected_sample = min(candidates, key=calibration_attempt_abs_error)
    return _selection(
        selected_sample,
        strategy=(
            "best_non_outlier_attempt"
            if non_outlier_attempts
            else "best_attempt"
        ),
        selected_attempts=[selected_sample.get("attempt")],
        average_error=None,
        ignored_outliers=ignored_outliers,
        ignored_invalid=ignored_invalid_attempts,
    )


def _selection(
    sample: dict[str, Any],
    *,
    strategy: str,
    selected_attempts: list[Any],
    average_error: float | None,
    ignored_outliers: list[int],
    ignored_invalid: list[int],
) -> dict[str, Any]:
    """Build the common selected-sample payload."""
    return {
        "sample": sample,
        "strategy": strategy,
        "selected_attempt": sample.get("attempt"),
        "selected_attempts": selected_attempts,
        "selected_window_avg_abs_error_percent": average_error,
        "selected_abs_error_percent": round(
            calibration_attempt_abs_error(sample),
            2,
        ),
        "ignored_outlier_attempts": ignored_outliers,
        "ignored_invalid_attempts": ignored_invalid,
    }


def interpolate_stop_percent(
    target: float,
    points: list[tuple[float, float]],
) -> float:
    """Interpolate a corrected stop percentage from calibration points."""
    if not points:
        raise NiceCalibrationError("No calibration points are available")
    previous_target, previous_stop = points[0]
    for next_target, next_stop in points[1:]:
        if target <= next_target:
            if next_target == previous_target:
                return _clamp(next_stop)
            ratio = (target - previous_target) / (
                next_target - previous_target
            )
            return _clamp(
                previous_stop + ((next_stop - previous_stop) * ratio)
            )
        previous_target, previous_stop = next_target, next_stop
    return _clamp(points[-1][1])


def _clamp(value: float) -> float:
    """Clamp a percentage."""
    return max(0.0, min(100.0, value))
