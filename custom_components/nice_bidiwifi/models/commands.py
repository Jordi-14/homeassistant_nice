"""Normalized commands and acknowledgements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class CommandKind(StrEnum):
    """Supported command families."""

    DOOR_ACTION = "door_action"
    T4_ACTION = "t4_action"
    DMP_WRITE = "dmp_write"


class CommandAcknowledgement(StrEnum):
    """Normalized command completion state."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NiceCommand:
    """One reviewed command request."""

    key: str
    kind: CommandKind
    arguments: tuple[tuple[str, int | float | str | bool], ...] = ()


@dataclass(frozen=True, slots=True)
class NiceCommandResult:
    """Transport-independent command outcome."""

    command: NiceCommand
    acknowledgement: CommandAcknowledgement
    latency_ms: int
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_code: str | None = None
    details: tuple[tuple[str, Any], ...] = ()

    @property
    def accepted(self) -> bool:
        """Return whether the device acknowledged the command."""
        return self.acknowledgement is CommandAcknowledgement.ACCEPTED
