"""Pure calibration lifecycle state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CalibrationState(StrEnum):
    """Calibration lifecycle states."""

    NOT_CALIBRATED = "not_calibrated"
    RUNNING = "running"
    CALIBRATED = "calibrated"
    CANCELLED = "cancelled"
    FAILED = "failed"


_ALLOWED_TRANSITIONS = {
    CalibrationState.NOT_CALIBRATED: {
        CalibrationState.NOT_CALIBRATED,
        CalibrationState.RUNNING,
        CalibrationState.CALIBRATED,
    },
    CalibrationState.RUNNING: {
        CalibrationState.CALIBRATED,
        CalibrationState.CANCELLED,
        CalibrationState.FAILED,
    },
    CalibrationState.CALIBRATED: {
        CalibrationState.NOT_CALIBRATED,
        CalibrationState.RUNNING,
        CalibrationState.CALIBRATED,
    },
    CalibrationState.CANCELLED: {
        CalibrationState.NOT_CALIBRATED,
        CalibrationState.RUNNING,
        CalibrationState.CALIBRATED,
    },
    CalibrationState.FAILED: {
        CalibrationState.NOT_CALIBRATED,
        CalibrationState.RUNNING,
        CalibrationState.CALIBRATED,
    },
}


@dataclass(slots=True)
class CalibrationStateMachine:
    """Validate state changes independently from Home Assistant."""

    state: CalibrationState = CalibrationState.NOT_CALIBRATED

    def transition(self, state: CalibrationState | str) -> CalibrationState:
        """Move to an allowed lifecycle state."""
        target = CalibrationState(state)
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(
                f"Invalid calibration transition: {self.state} -> {target}"
            )
        self.state = target
        return target
