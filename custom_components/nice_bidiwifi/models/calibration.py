"""Typed calibration source models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CalibrationMode(StrEnum):
    """Supported calibration measurement modes."""

    ENCODER = "encoder"
    LIVE_PERCENT = "live_percent"
    LIVE_SCALAR = "live_scalar"
    TIME = "time"


@dataclass(slots=True)
class CalibrationPositionSource:
    """Position source and quality evidence for one calibration run."""

    mode: CalibrationMode
    scalar_closed_raw: int | None = None
    scalar_open_raw: int | None = None
    scalar_min_raw: int | None = None
    scalar_max_raw: int | None = None
    observed_values: set[float] = field(default_factory=set)
    last_moving_value: dict[str, float] = field(default_factory=dict)
    monotonic_transitions: int = 0
    direction_violations: int = 0
