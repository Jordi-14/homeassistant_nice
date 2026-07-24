"""Normalized event models for Nice protocol adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class NiceEventKind(StrEnum):
    """Kinds of unsolicited Nice events."""

    CHANGE = "change"
    DIAGNOSTIC = "diagnostic"
    LIVE_STATUS = "live_status"
    UNKNOWN = "unknown"


class NiceEventCategory(StrEnum):
    """Stable Home Assistant event categories."""

    STATE_CHANGE = "state_change"
    OBSTRUCTION = "obstruction"
    DIAGNOSTIC = "diagnostic"
    BLUEBUS_ERROR = "bluebus_error"
    BATTERY = "battery"
    MAINTENANCE = "maintenance"
    MOTOR_CURRENT = "motor_current"
    RESET = "reset"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NiceEvent:
    """One bounded, non-sensitive unsolicited protocol event."""

    kind: NiceEventKind
    category: NiceEventCategory
    received_at: datetime
    event_id: str | None = None
    device_id: str | None = None
    state: str | None = None
    raw_state: str | None = None
    position: float | None = None
    obstruction: bool | None = None
    protocol_timestamp: str | None = None
    basic_diagnostic_code: str | None = None
    cause_code: str | None = None
    advanced_diagnostic_code: str | None = None
    relative_timestamp: str | None = None
    bluebus_error_status: str | None = None
    battery_device_type: str | None = None
    battery_level_code: str | None = None
    manoeuvre_count: int | None = None
    manoeuvre_threshold: int | None = None
    manoeuvre_average_current: float | None = None
    reset_device_class: str | None = None
    reset_cause: str | None = None

    def as_event_attributes(self) -> dict[str, str | int | float | bool]:
        """Return public attributes suitable for an EventEntity trigger."""
        values: tuple[tuple[str, str | int | float | bool | None], ...] = (
            ("kind", self.kind.value),
            ("event_id", self.event_id),
            ("device_id", self.device_id),
            ("received_at", self.received_at.isoformat()),
            ("state", self.state),
            ("raw_state", self.raw_state),
            ("position", self.position),
            ("obstruction", self.obstruction),
            ("protocol_timestamp", self.protocol_timestamp),
            ("basic_diagnostic_code", self.basic_diagnostic_code),
            ("cause_code", self.cause_code),
            ("advanced_diagnostic_code", self.advanced_diagnostic_code),
            ("relative_timestamp", self.relative_timestamp),
            ("bluebus_error_status", self.bluebus_error_status),
            ("battery_device_type", self.battery_device_type),
            ("battery_level_code", self.battery_level_code),
            ("manoeuvre_count", self.manoeuvre_count),
            ("manoeuvre_threshold", self.manoeuvre_threshold),
            ("manoeuvre_average_current", self.manoeuvre_average_current),
            ("reset_device_class", self.reset_device_class),
            ("reset_cause", self.reset_cause),
        )
        return {key: value for key, value in values if value is not None}

    def as_diagnostics(self) -> dict[str, str | int | float | bool]:
        """Return a bounded diagnostics representation without identifiers."""
        values = self.as_event_attributes()
        values.pop("device_id", None)
        values["category"] = self.category.value
        values["received_at"] = self.received_at.isoformat()
        return values
