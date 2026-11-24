"""Recovered app menu records and a blocked native wire-decoder contract.

The Java constructor consumes decoded fields, not raw DMP bytes. Parsing these
fields must never be mistaken for having recovered the native STD_MENU codec.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...errors import NiceParameterError


class MenuRole(StrEnum):
    USER = "user"
    INSTALLER = "installer"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MenuEntry:
    user_set: bool
    user_get: bool
    installer_set: bool
    installer_get: bool
    group: bool
    indentation: int
    app_id: int
    original_key: int

    @classmethod
    def from_decoded_fields(cls, fields: tuple[str, ...]):
        if len(fields) < 8 or any(flag not in {"y", "n"} for flag in fields[:5]):
            raise NiceParameterError("Malformed decoded menu entry")
        try:
            indentation, app_id, original = (int(value) for value in fields[5:8])
        except ValueError as err:
            raise NiceParameterError("Malformed menu identity") from err
        if not 0 <= indentation <= 32 or app_id < 0 or not -32768 <= original <= 65535:
            raise NiceParameterError("Menu identity is outside verified bounds")
        return cls(
            *(flag == "y" for flag in fields[:5]),
            indentation,
            app_id,
            original & 0xFFFF,
        )

    @property
    def binding(self):
        return self.original_key & 255, self.original_key >> 8

    def allows(self, role: MenuRole, operation: str) -> bool | None:
        if self.group:
            return False
        if operation not in {"get", "set"}:
            raise NiceParameterError("Unknown menu operation")
        if role is MenuRole.UNKNOWN:
            return None
        return getattr(self, f"{role}_{operation}")


@dataclass(frozen=True, slots=True)
class MenuPermission:
    """A scoped, decoded adapter observation. No config/import grants this role."""

    device_id: int
    binding: tuple[int, int]
    role: MenuRole
    entry: MenuEntry
    session_generation: int

    def can_set(self) -> bool | None:
        return (
            self.entry.allows(self.role, "set")
            if self.entry.binding == self.binding
            else False
        )


class MenuWireDecoder:
    gap = "controller.menu_wire"

    def decode(self, payload: bytes) -> tuple[MenuEntry, ...]:
        raise NiceParameterError(
            "STD_MENU wire decoder is unresolved; controller.menu_wire"
        )
