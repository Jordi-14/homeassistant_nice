"""Normalized position provenance and confidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .status import STATE_CLOSED, STATE_OPEN, NiceStatus


class PositionSource(StrEnum):
    """Known sources of a displayed position."""

    TIME_SIMULATION = "time_simulation"
    CONFIRMED_ENDPOINT = "confirmed_endpoint"
    HELD_LAST_KNOWN = "held_last_known"
    T4_LIVE_SCALAR_CALIBRATED = "t4_live_scalar_calibrated"
    T4_INSTANT = "t4_04_40"
    DMP_ENCODER = "dmp_encoder"
    CONTROLLER_REPORTED = "controller_reported"


class PositionConfidence(StrEnum):
    """Confidence assigned to a normalized position."""

    MEASURED = "measured"
    OBSERVED = "observed"
    REPORTED = "reported"
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class NicePosition:
    """Position value with provenance and confidence."""

    value: float
    source: PositionSource
    confidence: PositionConfidence

    @property
    def estimated(self) -> bool:
        """Return whether the value is estimated."""
        return self.confidence is PositionConfidence.ESTIMATED


def resolve_position(
    status: NiceStatus | None,
    *,
    simulated: float | None,
    last_known: float | None,
) -> NicePosition | None:
    """Resolve the best display position without Home Assistant dependencies."""
    if simulated is not None:
        return NicePosition(
            value=simulated,
            source=PositionSource.TIME_SIMULATION,
            confidence=PositionConfidence.ESTIMATED,
        )
    if status is None:
        return None

    registers = status.registers
    if "NHK/ConfirmedEndpointPosition" in registers:
        return _position(
            status.position,
            last_known,
            PositionSource.CONFIRMED_ENDPOINT,
            PositionConfidence.OBSERVED,
        )
    if status.position is None and last_known is not None:
        return NicePosition(
            value=last_known,
            source=PositionSource.HELD_LAST_KNOWN,
            confidence=PositionConfidence.ESTIMATED,
        )
    if "NHK/T4CalibratedPosition" in registers:
        return _position(
            status.position,
            last_known,
            PositionSource.T4_LIVE_SCALAR_CALIBRATED,
            PositionConfidence.OBSERVED,
        )
    if "NHK/T4InstantPosition" in registers:
        return _position(
            status.position,
            last_known,
            PositionSource.T4_INSTANT,
            PositionConfidence.OBSERVED,
        )
    if (
        status.position is not None
        and status.current_position is not None
        and status.closed_position is not None
        and status.open_position is not None
        and status.closed_position != status.open_position
    ):
        return NicePosition(
            value=status.position,
            source=PositionSource.DMP_ENCODER,
            confidence=PositionConfidence.MEASURED,
        )
    if status.position is not None and status.state in {STATE_OPEN, STATE_CLOSED}:
        return NicePosition(
            value=status.position,
            source=PositionSource.CONFIRMED_ENDPOINT,
            confidence=PositionConfidence.OBSERVED,
        )
    if status.position is not None:
        return NicePosition(
            value=status.position,
            source=PositionSource.CONTROLLER_REPORTED,
            confidence=PositionConfidence.REPORTED,
        )
    return None


def _position(
    value: float | None,
    fallback: float | None,
    source: PositionSource,
    confidence: PositionConfidence,
) -> NicePosition | None:
    """Create a position when the normalized or fallback value is known."""
    resolved = value if value is not None else fallback
    if resolved is None:
        return None
    return NicePosition(value=resolved, source=source, confidence=confidence)
