"""Decoding of controller event-history rows (T4_LOGS)."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import EVT_CHANGE_STATE, EVT_STATE_CAUSE
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

    def as_dict(self) -> dict[str, str | int | None]:
        """Serialize the fields that are safe for diagnostics."""
        return {
            "id": self.id,
            "time": self.time,
            "dep": self.dep,
            "change_state": self.change_state,
            "cause": self.cause,
            "data_hex": self.data_hex,
        }


def decode_history_row(
    row_id: int, time: str | None, protocol: str, plain: bytes
) -> ControllerHistoryRow | None:
    """Decode one decrypted DEP history row; return None when it is not DEP."""
    live = cuwifi_t4_message(plain)
    if protocol != "DEP" or live is None or len(live.message) < 2:
        return None
    group, section, *data = live.message
    is_state_change = (group, section) == (0x04, 0x02) and bool(data)
    return ControllerHistoryRow(
        id=row_id,
        time=time,
        protocol=protocol,
        dep=f"{group:02X}/{section:02X}",
        change_state=EVT_CHANGE_STATE.get(data[0], "undefined") if is_state_change else None,
        cause=(
            EVT_STATE_CAUSE.get(data[1], "undefined")
            if is_state_change and len(data) > 1
            else None
        ),
        data_hex=bytes(data[:HISTORY_DATA_HEX_BYTES]).hex(" "),
    )
