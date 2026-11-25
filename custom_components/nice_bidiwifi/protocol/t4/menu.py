"""Decoded app menu fields, audited raw records and a blocked live role contract.

Raw records do not establish paging, command-ID mappings or an authenticated
controller role. They cannot become session permissions without those contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
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
    """Scoped menu-column evidence; device authorization is not inferred."""

    device_id: int
    binding: tuple[int, int]
    role: MenuRole
    entry: MenuEntry
    session_generation: int
    verified_monotonic: float = field(default_factory=time.monotonic, compare=False)

    def can_set(self) -> bool | None:
        return (
            self.entry.allows(self.role, "set")
            if self.entry.binding == self.binding
            else False
        )


class MenuWireDecoder:
    gap = "controller.menu_wire"

    def decode(self, payload: bytes, *, group: int = 4) -> tuple[NativeMenuRecord, ...]:
        """Decode records only; no controller role or permission is granted."""
        return decode_menu_records(payload, group=group)


@dataclass(frozen=True, slots=True)
class NativeMenuRecord:
    """One native two-byte record, with no authenticated role or app-ID guess."""

    group: int
    parameter: int
    user_set: bool
    user_get: bool
    installer_set: bool
    installer_get: bool
    is_group: bool
    indentation: int

    @property
    def binding(self) -> tuple[int, int]:
        return self.group, self.parameter


def decode_menu_records(payload: bytes, *, group: int) -> tuple[NativeMenuRecord, ...]:
    """Audit-only decode of decompileMenu @0x226a4; it grants no permissions.

    Native consumes flags/parameter pairs with the profile/group passed separately.
    Mapping to obfuscated Java IDs, continuation and authenticated role selection
    remain separate contracts. Reject odd/truncated input instead of the native
    decoder's unchecked final-byte read.
    """
    if isinstance(group, bool) or not isinstance(group, int) or not 0 <= group <= 255:
        raise NiceParameterError("Invalid menu group")
    if len(payload) > 1024 or len(payload) % 2:
        raise NiceParameterError("Truncated or oversized menu records")
    return tuple(
        NativeMenuRecord(group, payload[index + 1], *(bool(payload[index] & bit) for bit in (0x80, 0x40, 0x20, 0x10, 8)), payload[index] & 7)
        for index in range(0, len(payload), 2)
    )


@dataclass(frozen=True, slots=True)
class ControllerMenuSnapshot:
    records: tuple[NativeMenuRecord, ...]
    session_scope: tuple | None
    pages: int
    device_id: int
    complete: bool = True


def build_controller_menu_request(sequence: int) -> bytes:
    """STD_MENU GET for controller group 4, retaining the app's sequence cookie."""
    from .codec import dmp_checksum
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 255:
        raise NiceParameterError("Invalid menu continuation sequence")
    payload = [0, 0x10, 0x99, sequence, 1, 4]
    header = [0, 3, 0x50, 0x91, 8, len(payload) + 1]
    body = [*header, dmp_checksum(*header), *payload, dmp_checksum(*payload)]
    return bytes([0x55, len(body), *body, len(body)])
