"""CU_WIFI live T4 event decoding."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...models.status import STATE_CLOSING, STATE_OPENING
from .dmp import STATUS_BY_BYTE
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

DMP_TARGET_CONTROLLER = (0x00, 0x03)
LIVE_RAW_POSITION_OPEN = 7000
# T4 event class byte -> LOG_REPLY_STATUS code
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
# EvtChangeState movements missing from STATUS_BY_BYTE (shared with register 04/01)
CHANGE_STATE_MOVEMENT = {0x81: STATE_OPENING, 0x82: STATE_CLOSING, 0x85: STATE_CLOSING}
_EVENT_FIELDS = (
    "change_state",
    "cause",
    "basic_diagnostic",
    "advanced_diagnostic",
    "command",
    "obstruction",
    "event_class",
    "event_payload_hex",
)


@dataclass(frozen=True, slots=True)
class CuwifiLiveStatus:
    """State or position decoded from a CU_WIFI live T4 event."""

    state: str | None
    position: float | None
    payload_hex: str
    payload_kind: str
    raw_position: int | None = None
    position_scale: str | None = None
    change_state: str | None = None
    cause: str | None = None
    basic_diagnostic: str | None = None
    advanced_diagnostic: str | None = None
    command: str | None = None
    obstruction: bool | None = None  # from the 0x23 diagnostics block only
    event_class: str | None = None
    event_payload_hex: str | None = None  # 04/02 and 04/82 only

    @property
    def affects_gate_status(self) -> bool:
        """Return whether the payload carries a state or a position."""
        return self.state is not None or self.position is not None


def merge_live_event_fields(
    base: CuwifiLiveStatus, other: CuwifiLiveStatus
) -> CuwifiLiveStatus:
    """Fill event fields that are None in base from another payload."""
    return replace(
        base,
        **{
            name: getattr(other, name)
            for name in _EVENT_FIELDS
            if getattr(base, name) is None
        },
    )


@dataclass(frozen=True, slots=True)
class LiveT4Message:
    """Inner controller-originated live T4 message and target address."""

    message: bytes
    target: tuple[int, int]


def cuwifi_t4_message(plain: bytes) -> LiveT4Message | None:
    """Return the inner controller-originated CU_WIFI T4 message."""
    if len(plain) < 12:
        return None

    if plain[0] == 0x55:
        if len(plain) < 4:
            return None
        body_size = plain[1]
        if body_size != plain[-1] or body_size != len(plain) - 3:
            return None
        body = plain[2:-1]
    else:
        body = plain

    if len(body) < 10:
        return None

    to_row, to_address, from_row, from_address, message_type, message_size = body[:6]
    message = body[7:-1]
    if message_size != len(message) + 1:
        return None
    target = (to_row, to_address)
    if target not in {(0x00, 0xFF), (0xFF, 0x01)}:
        return None
    if (from_row, from_address) != DMP_TARGET_CONTROLLER:
        return None
    if message_type != 0x01:
        return None

    return LiveT4Message(message=message, target=target)


def _live_04_40_position(
    position_value: int,
    target: tuple[int, int],
) -> tuple[float | None, str | None]:
    if target == (0xFF, 0x01):
        if 0 <= position_value <= LIVE_RAW_POSITION_OPEN:
            return round((position_value / LIVE_RAW_POSITION_OPEN) * 100.0, 1), "raw_0_7000"
        return None, "raw_0_7000"
    if 0 <= position_value <= 100:
        return float(position_value), "percent"
    return None, None


def parse_cuwifi_live_status_payload(plain: bytes) -> CuwifiLiveStatus | None:
    """Parse CU_WIFI live T4 status and instant-position payloads."""
    live_message = cuwifi_t4_message(plain)
    if live_message is None:
        return None
    message = live_message.message
    if not message or len(message) < 3 or message[0] != 0x04:
        return None

    payload_hex = message.hex(" ")
    if message[1] == 0x40 and len(message) >= 5:
        state = STATUS_BY_BYTE.get(message[2])
        position_value = int.from_bytes(message[3:5], "big")
        position, position_scale = _live_04_40_position(position_value, live_message.target)
        if state is None and position is None:
            return None
        return CuwifiLiveStatus(
            state=state,
            position=position,
            payload_hex=payload_hex,
            payload_kind="04/40",
            event_class=LOG_REPLY_STATUS[EVENT_CLASS_STATUS[0x40]],
            raw_position=position_value if position is not None else None,
            position_scale=position_scale if position is not None else None,
        )

    if message[1] == 0x02:
        data = message[2:]
        basic = advanced = obstruction = None
        if len(data) >= 7 and data[4] == 0x23:
            basic = AUTOMATION_STATUS_DIAGNOSTICS.get(data[5], "undefined")
            advanced = ADVANCED_DIAGNOSTIC.get(data[6], "undefined")
            obstruction = (
                data[5] in AUTOMATION_STATUS_OBSTACLE_CODES
                or data[6] in ADVANCED_DIAGNOSTIC_OBSTRUCTION_CODES
            )
        return CuwifiLiveStatus(
            state=STATUS_BY_BYTE.get(data[0], CHANGE_STATE_MOVEMENT.get(data[0])),
            position=None,
            payload_hex=payload_hex,
            payload_kind="04/02",
            event_class=LOG_REPLY_STATUS[EVENT_CLASS_STATUS[0x02]],
            event_payload_hex=payload_hex,
            change_state=EVT_CHANGE_STATE.get(data[0], "undefined"),
            cause=(
                EVT_STATE_CAUSE.get(data[1], "undefined") if len(data) > 1 else None
            ),
            basic_diagnostic=basic,
            advanced_diagnostic=advanced,
            obstruction=obstruction,
        )

    if message[1] == 0x82:
        return CuwifiLiveStatus(
            state=None,
            position=None,
            payload_hex=payload_hex,
            payload_kind="04/82",
            event_class=LOG_REPLY_STATUS[EVENT_CLASS_STATUS[0x82]],
            event_payload_hex=payload_hex,
            command=EVT_MOV_COMMAND.get(message[2], "undefined"),
        )

    return None


def effective_live_state(live_status: CuwifiLiveStatus) -> str | None:
    """Return the state that a live T4 payload may apply to device state."""
    if live_status.payload_kind == "04/40" and not movement_state(live_status):
        return None
    return live_status.state


def movement_state(live_status: CuwifiLiveStatus) -> bool:
    """Return whether a live status reports active movement."""
    return live_status.state in {STATE_OPENING, STATE_CLOSING}
