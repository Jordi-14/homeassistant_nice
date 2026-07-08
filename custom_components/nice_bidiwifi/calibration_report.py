"""Calibration report helpers for Nice."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from .calibration_types import CalibrationEvent, CalibrationProfile, CalibrationReport

CALIBRATION_REPORT_ATTRIBUTE_EVENT_LIMIT = 5


def calibration_quality(report: CalibrationReport | None) -> str | None:
    """Return the latest calibration quality grade."""
    if report is None:
        return None
    quality = report.get("quality")
    return str(quality) if quality is not None else None


def calibration_report_summary(report: CalibrationReport | None) -> str | None:
    """Return a compact calibration report summary."""
    if report is None:
        return None
    summary = report.get("summary")
    return str(summary)[:255] if summary is not None else None


def calibration_report_attributes(report: CalibrationReport | None) -> dict[str, Any]:
    """Return recorder-safe calibration report attributes."""
    if report is None:
        return {}

    events = report.get("events")
    points = report.get("points")
    return {
        "state": report.get("state"),
        "quality": report.get("quality"),
        "summary": report.get("summary"),
        "updated_at": report.get("updated_at"),
        "profile_version": report.get("profile_version"),
        "profile_mode": report.get("profile_mode"),
        "tolerance_percent": report.get("tolerance_percent"),
        "poll_seconds": report.get("poll_seconds"),
        "settle_seconds": report.get("settle_seconds"),
        "command_pause_seconds": report.get("command_pause_seconds"),
        "max_attempts": report.get("max_attempts"),
        "stability_attempts": report.get("stability_attempts"),
        "point_count": report.get("point_count"),
        "successful_points": report.get("successful_points"),
        "invalid_points": report.get("invalid_points"),
        "failed_points": _compact_calibration_failed_points(report.get("failed_points")),
        "total_attempts": report.get("total_attempts"),
        "max_attempts_used": report.get("max_attempts_used"),
        "max_abs_error_percent": report.get("max_abs_error_percent"),
        "avg_abs_error_percent": report.get("avg_abs_error_percent"),
        "bounds": report.get("bounds"),
        "travel_speed": report.get("travel_speed"),
        "points": _compact_calibration_points(points),
        "event_count": len(events) if isinstance(events, list) else None,
        "last_events": _compact_calibration_events(events),
        "full_report_log_prefix": "Nice calibration report",
        "full_report_note": (
            "Full report is written to Home Assistant logs in chunks when calibration finishes or fails."
        ),
    }


def build_live_calibration_report(
    *,
    state: str,
    summary: str,
    events: list[CalibrationEvent],
    tolerance_percent: float,
    poll_seconds: float,
    settle_seconds: float,
    command_pause_seconds: float,
    max_attempts: int,
    stability_attempts: int,
) -> CalibrationReport:
    """Build a report while calibration is still running or failed."""
    return {
        "state": state,
        "quality": state,
        "summary": summary,
        "updated_at": datetime.now(UTC).isoformat(),
        "tolerance_percent": tolerance_percent,
        "poll_seconds": poll_seconds,
        "settle_seconds": settle_seconds,
        "command_pause_seconds": command_pause_seconds,
        "max_attempts": max_attempts,
        "stability_attempts": stability_attempts,
        "events": list(events),
    }


def build_calibration_report(profile: CalibrationProfile, state: str) -> CalibrationReport:
    """Build quality metrics and a copyable report from a calibration profile."""
    points = _calibration_points(profile)
    events = profile.get("events", [])
    tolerance = float(profile.get("target_tolerance_percent") or 2.0)
    mode = str(profile.get("mode") or "encoder")

    if mode == "time":
        return _build_time_calibration_report(profile, state, events)

    if not points:
        return {
            "state": state,
            "quality": "unknown",
            "summary": "No calibration points found in the stored profile",
            "profile": profile,
            "profile_mode": mode,
            "travel_speed": profile.get("travel_speed", {}),
            "points": [],
            "events": events if isinstance(events, list) else [],
        }

    abs_errors = [
        abs(float(error))
        for point in points
        if isinstance((error := point.get("final_error_percent")), (int, float))
    ]
    invalid_points = sum(1 for point in points if point.get("valid", True) is False)
    attempts_used = [int(point["attempts_used"]) for point in points]
    success_count = sum(1 for point in points if point["successful"])
    total_points = len(points)
    max_abs_error = round(max(abs_errors), 2) if abs_errors else None
    avg_abs_error = round(sum(abs_errors) / len(abs_errors), 2) if abs_errors else None
    total_attempts = sum(attempts_used)
    max_attempts_used = max(attempts_used)
    failed_points = [
        {
            "direction": point["direction"],
            "target_percent": point["target_percent"],
            "final_error_percent": point["final_error_percent"],
            "attempts_used": point["attempts_used"],
            "selection_strategy": point.get("selection_strategy"),
            "selected_attempt": point.get("selected_attempt"),
            "selected_abs_error_percent": point.get("selected_abs_error_percent"),
            "ignored_invalid_attempts": point.get("ignored_invalid_attempts"),
            "valid": point.get("valid"),
            "failure_reason": point.get("failure_reason"),
        }
        for point in points
        if not point["successful"]
    ]

    quality = _calibration_quality(
        success_count,
        total_points,
        max_abs_error if max_abs_error is not None else 1000.0,
        avg_abs_error if avg_abs_error is not None else 1000.0,
        max_attempts_used,
    )
    max_error_text = f"{max_abs_error:.2f}%" if max_abs_error is not None else "unknown"
    avg_error_text = f"{avg_abs_error:.2f}%" if avg_abs_error is not None else "unknown"
    summary = (
        f"{quality}: {success_count}/{total_points} repeatable targets within {tolerance:g}%"
        f"; max error {max_error_text}; avg error {avg_error_text}"
        f"; attempts {total_attempts}; events {len(events) if isinstance(events, list) else 0}"
    )
    if invalid_points:
        summary = f"{summary}; invalid points {invalid_points}"
    return {
        "state": state,
        "quality": quality,
        "summary": summary,
        "updated_at": profile.get("updated_at"),
        "profile_version": profile.get("version"),
        "profile_mode": mode,
        "tolerance_percent": tolerance,
        "poll_seconds": profile.get("poll_seconds"),
        "settle_seconds": profile.get("settle_seconds"),
        "command_pause_seconds": profile.get("command_pause_seconds"),
        "max_attempts": profile.get("max_attempts"),
        "stability_attempts": profile.get("stability_attempts"),
        "point_count": total_points,
        "successful_points": success_count,
        "invalid_points": invalid_points,
        "failed_points": failed_points,
        "total_attempts": total_attempts,
        "max_attempts_used": max_attempts_used,
        "max_abs_error_percent": max_abs_error,
        "avg_abs_error_percent": avg_abs_error,
        "bounds": profile.get("bounds", {}),
        "travel_speed": profile.get("travel_speed", {}),
        "points": points,
        "events": events if isinstance(events, list) else [],
    }


def _build_time_calibration_report(
    profile: CalibrationProfile,
    state: str,
    events: Any,
) -> CalibrationReport:
    """Build a report for time-based calibration profiles."""
    travel_speed = profile.get("travel_speed", {})
    open_speed = _speed_percent_per_second(travel_speed, "open")
    close_speed = _speed_percent_per_second(travel_speed, "close")
    complete = open_speed is not None and close_speed is not None
    quality = "time_based" if complete else "needs_review"
    if complete:
        summary = (
            "time_based: full-travel timing measured"
            f"; open {open_speed:.2f}%/s"
            f"; close {close_speed:.2f}%/s"
        )
    else:
        summary = "needs_review: incomplete time-based calibration"
    return {
        "state": state,
        "quality": quality,
        "summary": summary,
        "updated_at": profile.get("updated_at"),
        "profile_version": profile.get("version"),
        "profile_mode": "time",
        "tolerance_percent": profile.get("target_tolerance_percent"),
        "poll_seconds": profile.get("poll_seconds"),
        "settle_seconds": profile.get("settle_seconds"),
        "command_pause_seconds": profile.get("command_pause_seconds"),
        "max_attempts": profile.get("max_attempts"),
        "stability_attempts": profile.get("stability_attempts"),
        "point_count": 0,
        "successful_points": 0,
        "invalid_points": 0,
        "failed_points": [],
        "total_attempts": 0,
        "max_attempts_used": 0,
        "max_abs_error_percent": None,
        "avg_abs_error_percent": None,
        "bounds": profile.get("bounds", {}),
        "travel_speed": travel_speed if isinstance(travel_speed, dict) else {},
        "points": [],
        "events": events if isinstance(events, list) else [],
    }


def _speed_percent_per_second(travel_speed: Any, action: str) -> float | None:
    """Return a stored full-travel speed for one direction."""
    if not isinstance(travel_speed, dict):
        return None
    action_speed = travel_speed.get(action)
    if not isinstance(action_speed, dict):
        return None
    speed = action_speed.get("speed_percent_per_second")
    return float(speed) if isinstance(speed, (int, float)) and speed > 0 else None


def format_calibration_report(report: CalibrationReport) -> str:
    """Format a calibration report as copyable plain text."""
    lines = [
        "Nice position calibration report",
        f"State: {report.get('state')}",
        f"Quality: {report.get('quality')}",
        f"Summary: {report.get('summary')}",
        f"Updated at: {report.get('updated_at')}",
        f"Profile mode: {report.get('profile_mode')}",
        f"Tolerance: {report.get('tolerance_percent')}%",
        f"Poll seconds: {report.get('poll_seconds')}",
        f"Settle seconds: {report.get('settle_seconds')}",
        f"Command pause seconds: {report.get('command_pause_seconds')}",
        f"Stability attempts: {report.get('stability_attempts')}",
    ]

    bounds = report.get("bounds")
    if isinstance(bounds, dict) and bounds:
        lines.append("")
        lines.append("Bounds:")
        for key, value in sorted(bounds.items()):
            lines.append(f"- {key}: {value}")

    travel_speed = report.get("travel_speed")
    if isinstance(travel_speed, dict) and travel_speed:
        lines.append("")
        lines.append("Full-travel speed:")
        for direction, value in sorted(travel_speed.items()):
            if not isinstance(value, dict):
                continue
            lines.append(
                "- "
                f"{direction}: {value.get('speed_percent_per_second')}%/s "
                f"duration={value.get('duration_ms')}ms "
                f"from={value.get('start_percent')}% "
                f"to={value.get('end_percent')}%"
            )

    points = report.get("points")
    if isinstance(points, list) and points:
        lines.append("")
        lines.append("Calibration points:")
        for point in points:
            lines.append(
                "- "
                f"{point.get('direction')} {point.get('target_percent')}% "
                f"valid={point.get('valid')} "
                f"failure={point.get('failure_reason')} "
                f"success={point.get('successful')} "
                f"selection={point.get('selection_strategy')} "
                f"selected_attempt={point.get('selected_attempt')} "
                f"selected_attempts={point.get('selected_attempts')} "
                f"selected_abs_error={point.get('selected_abs_error_percent')}% "
                f"selected_window_avg_abs_error={point.get('selected_window_avg_abs_error_percent')}% "
                f"outliers={point.get('ignored_outlier_attempts')} "
                f"invalid_attempts={point.get('ignored_invalid_attempts')} "
                f"successful_attempts={point.get('successful_attempts')} "
                f"attempts={point.get('attempts_used')} "
                f"selected_final={point.get('final_percent')}% "
                f"selected_error={point.get('final_error_percent')}% "
                f"corrected_stop={point.get('corrected_stop_percent')}%"
            )
            attempts = point.get("attempts")
            if isinstance(attempts, list):
                selected_attempts = point.get("selected_attempts")
                if not isinstance(selected_attempts, list):
                    selected_attempts = []
                ignored_outliers = point.get("ignored_outlier_attempts")
                if not isinstance(ignored_outliers, list):
                    ignored_outliers = []
                for attempt in attempts:
                    if not isinstance(attempt, dict):
                        continue
                    markers = []
                    if attempt.get("attempt") in selected_attempts:
                        markers.append("selected")
                    if attempt.get("attempt") in ignored_outliers:
                        markers.append("outlier")
                    if attempt.get("valid", True) is False:
                        markers.append("invalid")
                    lines.append(
                        "  "
                        f"attempt {attempt.get('attempt')}: "
                        f"markers={markers} "
                        f"failure={attempt.get('failure_reason')} "
                        f"requested_stop={attempt.get('requested_stop_percent')}% "
                        f"stop_sent={attempt.get('stop_command_percent')}% "
                        f"final={attempt.get('final_percent')}% "
                        f"error={attempt.get('error_percent')}% "
                        f"latency={attempt.get('stop_command_latency_ms')}ms "
                        f"duration={attempt.get('move_duration_ms')}ms "
                        f"speed_raw_per_second={attempt.get('speed_raw_per_second')}"
                    )

    events = report.get("events")
    if isinstance(events, list) and events:
        lines.append("")
        lines.append("Event log:")
        for event in events:
            if not isinstance(event, dict):
                continue
            details = event.get("details")
            detail_text = ""
            if isinstance(details, dict) and details:
                detail_text = f" {json.dumps(details, sort_keys=True)}"
            lines.append(
                f"[{event.get('index')}] {event.get('timestamp')} "
                f"{event.get('stage')}: {event.get('message')}{detail_text}"
            )

    return "\n".join(lines)


def _compact_calibration_failed_points(failed_points: Any) -> list[dict[str, Any]]:
    """Return a small failed-point list suitable for state attributes."""
    if not isinstance(failed_points, list):
        return []
    compact_points = []
    for point in failed_points:
        if not isinstance(point, dict):
            continue
        compact_points.append(
            {
                "direction": point.get("direction"),
                "target_percent": point.get("target_percent"),
                "final_error_percent": point.get("final_error_percent"),
                "attempts_used": point.get("attempts_used"),
                "selection_strategy": point.get("selection_strategy"),
                "selected_attempt": point.get("selected_attempt"),
                "selected_abs_error_percent": point.get("selected_abs_error_percent"),
                "ignored_invalid_attempts": point.get("ignored_invalid_attempts"),
                "valid": point.get("valid"),
                "failure_reason": point.get("failure_reason"),
            }
        )
    return compact_points


def _compact_calibration_points(points: Any) -> list[dict[str, Any]]:
    """Return point summaries without per-attempt details."""
    if not isinstance(points, list):
        return []
    compact_points = []
    for point in points:
        if not isinstance(point, dict):
            continue
        compact_points.append(
            {
                "direction": point.get("direction"),
                "valid": point.get("valid"),
                "failure_reason": point.get("failure_reason"),
                "target_percent": point.get("target_percent"),
                "successful": point.get("successful"),
                "successful_attempts": point.get("successful_attempts"),
                "attempts_used": point.get("attempts_used"),
                "selection_strategy": point.get("selection_strategy"),
                "selected_attempt": point.get("selected_attempt"),
                "selected_attempts": point.get("selected_attempts"),
                "selected_abs_error_percent": point.get("selected_abs_error_percent"),
                "selected_window_avg_abs_error_percent": point.get("selected_window_avg_abs_error_percent"),
                "ignored_outlier_attempts": point.get("ignored_outlier_attempts"),
                "ignored_invalid_attempts": point.get("ignored_invalid_attempts"),
                "final_percent": point.get("final_percent"),
                "final_error_percent": point.get("final_error_percent"),
                "corrected_stop_percent": point.get("corrected_stop_percent"),
            }
        )
    return compact_points


def _compact_calibration_events(events: Any) -> list[dict[str, Any]]:
    """Return the last calibration events without bulky details."""
    if not isinstance(events, list):
        return []
    compact_events = []
    for event in events[-CALIBRATION_REPORT_ATTRIBUTE_EVENT_LIMIT:]:
        if not isinstance(event, dict):
            continue
        compact_events.append(
            {
                "index": event.get("index"),
                "timestamp": event.get("timestamp"),
                "stage": event.get("stage"),
                "message": event.get("message"),
            }
        )
    return compact_events


def _calibration_quality(
    success_count: int,
    total_points: int,
    max_abs_error: float,
    avg_abs_error: float,
    max_attempts_used: int,
) -> str:
    """Return a simple quality grade for a calibration result."""
    if success_count < total_points:
        return "needs_review"
    if max_abs_error <= 0.5 and avg_abs_error <= 0.35 and max_attempts_used <= 2:
        return "excellent"
    if max_abs_error <= 1.0:
        return "good"
    if max_abs_error <= 2.0:
        return "usable"
    return "needs_review"


def _calibration_points(profile: CalibrationProfile) -> list[dict[str, Any]]:
    """Flatten stored calibration samples into report points."""
    samples_by_direction = profile.get("samples")
    if not isinstance(samples_by_direction, dict):
        return []

    points: list[dict[str, Any]] = []
    for direction in ("open", "close"):
        samples = samples_by_direction.get(direction, [])
        if not isinstance(samples, list):
            continue
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            attempts = sample.get("attempts", [])
            points.append(
                {
                    "direction": direction,
                    "valid": sample.get("valid", True),
                    "failure_reason": sample.get("failure_reason"),
                    "target_percent": sample.get("target_percent"),
                    "successful": bool(sample.get("successful")),
                    "successful_attempts": sample.get("successful_attempts"),
                    "stability_attempts": sample.get("stability_attempts"),
                    "attempts_used": int(sample.get("attempts_used") or 0),
                    "selection_strategy": sample.get("selection_strategy"),
                    "selected_attempt": sample.get("selected_attempt"),
                    "selected_attempts": sample.get("selected_attempts"),
                    "selected_window_avg_abs_error_percent": sample.get(
                        "selected_window_avg_abs_error_percent"
                    ),
                    "selected_abs_error_percent": sample.get("selected_abs_error_percent"),
                    "ignored_outlier_attempts": sample.get("ignored_outlier_attempts"),
                    "ignored_invalid_attempts": sample.get("ignored_invalid_attempts"),
                    "outlier_error_percent": sample.get("outlier_error_percent"),
                    "final_percent": sample.get("final_percent"),
                    "final_error_percent": sample.get("error_percent"),
                    "final_error_raw": sample.get("error_raw"),
                    "corrected_stop_percent": sample.get("corrected_stop_percent"),
                    "corrected_stop_raw": sample.get("corrected_stop_raw"),
                    "final_attempt": {
                        "valid": sample.get("valid", True),
                        "failure_reason": sample.get("failure_reason"),
                        "attempt": sample.get("attempt"),
                        "requested_stop_percent": sample.get("requested_stop_percent"),
                        "stop_command_percent": sample.get("stop_command_percent"),
                        "final_percent": sample.get("final_percent"),
                        "error_percent": sample.get("error_percent"),
                        "move_duration_ms": sample.get("move_duration_ms"),
                        "stop_command_latency_ms": sample.get("stop_command_latency_ms"),
                        "speed_raw_per_second": sample.get("speed_raw_per_second"),
                    },
                    "last_attempt": sample.get("last_attempt"),
                    "attempts": attempts if isinstance(attempts, list) else [],
                }
            )
    return points
