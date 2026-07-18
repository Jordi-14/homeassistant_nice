"""Typed calibration data structures for Nice."""

from __future__ import annotations

from typing import Any, TypedDict


class CalibrationEvent(TypedDict, total=False):
    """One calibration event."""

    index: int
    timestamp: str
    stage: str
    message: str
    details: dict[str, Any]


class CalibrationSample(TypedDict, total=False):
    """One selected calibration sample."""

    action: str
    endpoint_action: str
    valid: bool
    failure_reason: str | None
    attempt: int
    target_percent: int | float
    start_raw: int
    start_percent: float
    target_raw: int
    requested_stop_raw: int
    requested_stop_percent: float
    stop_command_raw: int
    stop_command_percent: float
    corrected_stop_raw: int
    corrected_stop_percent: float
    final_raw: int | None
    final_percent: float | None
    error_raw: int | None
    error_percent: float | None
    move_duration_ms: int
    speed_raw_per_second: float | None
    stop_command_latency_ms: int | None
    successful: bool
    successful_attempts: int
    stability_attempts: int
    attempts_used: int
    selection_strategy: str
    selected_attempt: int | None
    selected_attempts: list[int | None]
    selected_window_avg_abs_error_percent: float | None
    selected_abs_error_percent: float | None
    ignored_outlier_attempts: list[int]
    ignored_invalid_attempts: list[int]
    outlier_error_percent: float
    last_attempt: dict[str, Any]
    attempts: list[dict[str, Any]]


class CalibrationProfile(TypedDict, total=False):
    """Stored calibration profile."""

    version: int
    mode: str
    created_at: str
    updated_at: str
    poll_seconds: float
    settle_seconds: float
    command_pause_seconds: float
    max_attempts: int
    stability_attempts: int
    target_tolerance_percent: float
    targets: list[int]
    bounds: dict[str, Any]
    travel_speed: dict[str, dict[str, Any]]
    samples: dict[str, list[CalibrationSample]]
    events: list[CalibrationEvent]


class CalibrationReport(TypedDict, total=False):
    """Recorder-safe calibration report."""

    state: str
    quality: str
    summary: str
    updated_at: str | None
    profile_version: int | None
    profile_mode: str | None
    tolerance_percent: float
    poll_seconds: float | None
    settle_seconds: float | None
    command_pause_seconds: float | None
    max_attempts: int | None
    stability_attempts: int | None
    point_count: int
    successful_points: int
    invalid_points: int
    failed_points: list[dict[str, Any]]
    total_attempts: int
    max_attempts_used: int
    max_abs_error_percent: float | None
    avg_abs_error_percent: float | None
    bounds: dict[str, Any]
    travel_speed: dict[str, dict[str, Any]]
    points: list[dict[str, Any]]
    events: list[CalibrationEvent]
    profile: CalibrationProfile
