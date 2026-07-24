"""Normalized event models for Nice protocol adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class NiceEventKind(StrEnum):
    """Kinds of unsolicited Nice events."""

    CHANGE = "change"
    DIAGNOSTIC = "diagnostic"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NiceEvent:
    """One normalized unsolicited event."""

    kind: NiceEventKind
    received_at: datetime
    device_id: str | None = None
    state: str | None = None
    obstruction: bool | None = None
    details: tuple[tuple[str, Any], ...] = ()
