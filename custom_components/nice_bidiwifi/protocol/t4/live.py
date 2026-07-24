"""CU_WIFI live T4 event decoding."""

from __future__ import annotations

from dataclasses import dataclass

from ...models.status import STATE_CLOSING, STATE_OPENING
from .dmp import STATUS_BY_BYTE

DMP_TARGET_CONTROLLER = (0x00, 0x03)
LIVE_RAW_POSITION_OPEN = 7000


@dataclass(frozen=True, slots=True)
class CuwifiLiveStatus:
    """State or position decoded from a CU_WIFI live T4 event."""

    state: str | None
    position: float | None
    payload_hex: str
    payload_kind: str
    raw_position: int | None = None
    position_scale: str | None = None


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
            raw_position=position_value if position is not None else None,
            position_scale=position_scale if position is not None else None,
        )

    if message[1] == 0x02:
        state = STATUS_BY_BYTE.get(message[2])
        if state is None:
            return None
        return CuwifiLiveStatus(
            state=state,
            position=None,
            payload_hex=payload_hex,
            payload_kind="04/02",
        )

    return None


def movement_state(live_status: CuwifiLiveStatus) -> bool:
    """Return whether a live status reports active movement."""
    return live_status.state in {STATE_OPENING, STATE_CLOSING}
