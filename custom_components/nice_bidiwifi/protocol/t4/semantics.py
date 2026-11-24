"""One source-backed interpretation of live and stored T4 event data."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import (
    ADVANCED_DIAGNOSTIC,
    ADVANCED_DIAGNOSTIC_OBSTRUCTION_CODES,
    AUTOMATION_STATUS_DIAGNOSTICS,
    AUTOMATION_STATUS_OBSTACLE_CODES,
    EVT_CHANGE_STATE,
    EVT_MOV_COMMAND,
    EVT_STATE_CAUSE,
    LOG_REPLY_STATUS,
)

EVENT_CLASS_STATUS = {
    0x01: 200,
    0x02: 201,
    0x08: 202,
    0x10: 203,
    0x11: 204,
    0x40: 205,
    0x81: 206,
    0x82: 207,
    0x83: 208,
    0xC1: 209,
    0xD1: 210,
}


@dataclass(frozen=True, slots=True)
class EventMeaning:
    event_class: str
    change_state: str | None = None
    cause: str | None = None
    basic: str | None = None
    advanced: str | None = None
    obstruction: bool | None = None
    command: str | None = None
    confidence: str = "reported"
    physical_cause: str = "unknown"
    actor: str = "unknown"
    details: tuple[tuple[str, int], ...] = ()
    description: str | None = None


def decode_event_meaning(group: int, section: int, data: bytes) -> EventMeaning | None:
    if group != 4 or section not in EVENT_CLASS_STATUS:
        return None  # receiver/unknown payloads can contain remote codes and keys
    kind = LOG_REPLY_STATUS[EVENT_CLASS_STATUS[section]]
    if section == 2:
        if not data:
            return None
        basic = advanced = obstruction = None
        if len(data) >= 6 and data[4] == 0x23:
            basic = AUTOMATION_STATUS_DIAGNOSTICS.get(data[5], "undefined")
            if len(data) >= 7:
                advanced = ADVANCED_DIAGNOSTIC.get(data[6], "undefined")
            obstruction = (
                data[5] in AUTOMATION_STATUS_OBSTACLE_CODES
                or len(data) >= 7
                and data[6] in ADVANCED_DIAGNOSTIC_OBSTRUCTION_CODES
            )
        return EventMeaning(
            kind,
            EVT_CHANGE_STATE.get(data[0], "undefined"),
            EVT_STATE_CAUSE.get(data[1], "undefined") if len(data) > 1 else None,
            basic,
            advanced,
            obstruction,
        )
    if section == 0x82:
        return EventMeaning(
            kind, command=EVT_MOV_COMMAND.get(data[0], "undefined") if data else None
        )
    if section in {0x81, 0xC1}:
        name = "channel_code" if section == 0x81 else "manual_command_code"
        labels = (
            {1: "step_step", 2: "open_partial_1", 4: "open", 8: "close"}
            if section == 0x81
            else {
                0: "stop_m1",
                1: "open_man_present_m1",
                2: "close_man_present_m1",
                16: "stop_m2",
                17: "open_man_present_m2",
                18: "close_man_present_m2",
            }
        )
        return EventMeaning(
            kind,
            details=((name, data[0]),) if data else (),
            description=labels.get(data[0], "undefined") if data else None,
        )
    return EventMeaning(kind)


def safe_event_data(group: int, section: int, data: bytes) -> str:
    """Only reviewed fixed-layout payloads may be exported as raw bytes."""
    size = {2: 7, 0x40: 4, 0x82: 1}.get(section, 0) if group == 4 else 0
    return data[:size].hex(" ")
