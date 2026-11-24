"""Decoding of controller event-history rows (T4_LOGS)."""

from __future__ import annotations

from dataclasses import dataclass

from .semantics import decode_event_meaning, safe_event_data
from .live import cuwifi_t4_message

HISTORY_DATA_HEX_BYTES = 32


@dataclass(frozen=True, slots=True)
class ControllerHistoryRow:
    """One decoded controller event-history row."""

    id: int
    time: str | None  # controller clock, as given
    protocol: str
    dep: str  # "group/class", for example "04/02"
    change_state: str | None  # 04/02 only
    cause: str | None  # 04/02 only
    data_hex: str
    event_class: str | None = None
    basic_diagnostic: str | None = None
    advanced_diagnostic: str | None = None
    command: str | None = None
    obstruction: bool | None = None
    confidence: str = "reported"
    physical_cause: str = "unknown"
    actor: str = "unknown"
    event_description: str | None = None
    details: tuple[tuple[str, int], ...] = ()

    def as_dict(self) -> dict[str, str | int | None]:
        """Serialize the fields that are safe for diagnostics."""
        return {
            "id": self.id,
            "time": self.time,
            "dep": self.dep,
            "change_state": self.change_state,
            "cause": self.cause,
            "data_hex": self.data_hex,
            "protocol": self.protocol,
            "event_class": self.event_class,
            "basic_diagnostic": self.basic_diagnostic,
            "advanced_diagnostic": self.advanced_diagnostic,
            "command": self.command,
            "obstruction": self.obstruction,
            "confidence": self.confidence,
            "physical_cause": self.physical_cause,
            "actor": self.actor,
            "event_description": self.event_description,
            "details": dict(self.details),
        }


def decode_history_row(
    row_id: int, time: str | None, protocol: str, plain: bytes
) -> ControllerHistoryRow | None:
    """Decode one decrypted DEP history row; return None when it is not DEP."""
    live = cuwifi_t4_message(plain)
    if protocol != "DEP" or live is None or len(live.message) < 2:
        return None
    group, section, *data = live.message
    meaning = decode_event_meaning(group, section, bytes(data))
    if meaning is None:
        return None
    return ControllerHistoryRow(
        id=row_id,
        time=time[:96] if time else None,
        protocol=protocol,
        dep=f"{group:02X}/{section:02X}",
        change_state=meaning.change_state,
        cause=meaning.cause,
        data_hex=safe_event_data(group, section, bytes(data)),
        event_class=meaning.event_class,
        basic_diagnostic=meaning.basic,
        advanced_diagnostic=meaning.advanced,
        command=meaning.command,
        obstruction=meaning.obstruction,
        details=meaning.details,
        event_description=meaning.description,
    )
