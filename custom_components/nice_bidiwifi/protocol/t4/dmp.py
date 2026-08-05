"""Pure DMP register frame encoding and decoding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...models.status import (
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_PARTIALLY_OPEN,
    STATE_STOPPED,
)
from .codec import dmp_checksum

STATUS_BY_BYTE = {
    0x01: STATE_STOPPED,
    0x02: STATE_OPENING,
    0x03: STATE_CLOSING,
    0x04: STATE_OPEN,
    0x05: STATE_CLOSED,
    0x10: STATE_PARTIALLY_OPEN,
    0x11: STATE_PARTIALLY_OPEN,
    0x12: STATE_PARTIALLY_OPEN,
    0x83: STATE_OPENING,
    0x84: STATE_CLOSING,
}


@dataclass(frozen=True, slots=True)
class DmpResponse:
    """Typed result of decoding one DMP response."""

    plain_hex: str
    group: int | None = None
    parameter: int | None = None
    operation: int | None = None
    value_type: int | None = None
    value: bytes | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the legacy dictionary representation."""
        result: dict[str, Any] = {"plain_hex": self.plain_hex}
        if self.group is not None:
            result["group"] = f"{self.group:02X}"
        if self.parameter is not None:
            result["parameter"] = f"{self.parameter:02X}"
        if self.operation is not None:
            result["operation"] = f"{self.operation:02X}"
        if self.value_type is not None:
            result["value_type"] = f"{self.value_type:02X}"
        if self.value is not None:
            result["value_hex"] = self.value.hex(" ")
            result["value_uint_be"] = int.from_bytes(self.value, "big")
        return result


def build_dmp_read_frame(
    daddr: int,
    dendpoint: int,
    group: int,
    parameter: int,
    index: int | None = None,
) -> bytes:
    """Build a DMP register read frame."""
    args = [group, parameter, 0x99, 0x00]
    if index is None:
        args.append(0x00)
    else:
        args.extend([0x01, index])
    sublen = len(args) + 1
    marker = 0xC9 ^ sublen ^ daddr ^ dendpoint
    body = [
        daddr,
        dendpoint,
        0x50,
        0x91,
        0x08,
        sublen,
        marker,
        *args,
        dmp_checksum(*args),
    ]
    length = len(body)
    return bytes([0x55, length, *body, length])


def build_dmp_write_frame(
    daddr: int,
    dendpoint: int,
    group: int,
    parameter: int,
    value: bytes,
) -> bytes:
    """Build a DMP register write frame."""
    if not value:
        raise ValueError("value must contain at least one byte")
    if len(value) > 0xFF:
        raise ValueError("value must fit in a DMP byte-length payload")
    args = [group, parameter, 0xA9, 0x00, len(value), *value]
    sublen = len(args) + 1
    marker = 0xC9 ^ sublen ^ daddr ^ dendpoint
    body = [
        daddr,
        dendpoint,
        0x50,
        0x91,
        0x08,
        sublen,
        marker,
        *args,
        dmp_checksum(*args),
    ]
    length = len(body)
    return bytes([0x55, length, *body, length])


def parse_dmp_response(plain: bytes) -> dict[str, Any]:
    """Parse a decrypted DMP response into the legacy dictionary shape."""
    return decode_dmp_response(plain).as_dict()


def decode_dmp_response(plain: bytes) -> DmpResponse:
    """Decode a DMP response into an immutable typed result."""
    plain_hex = plain.hex(" ")
    if len(plain) < 15 or plain[0] != 0x55:
        return DmpResponse(plain_hex=plain_hex)
    group = plain[9]
    parameter = plain[10]
    operation = plain[11]
    if plain[11] == 0x19 and len(plain) >= 15:
        value_len = plain[12]
        value_type = plain[13]
        value = plain[14 : 14 + value_len]
        if len(value) == value_len:
            return DmpResponse(
                plain_hex=plain_hex,
                group=group,
                parameter=parameter,
                operation=operation,
                value_type=value_type,
                value=value,
            )
    return DmpResponse(
        plain_hex=plain_hex,
        group=group,
        parameter=parameter,
        operation=operation,
    )


def dmp_bytes(
    register: DmpResponse | dict[str, Any] | None,
) -> bytes | None:
    """Return raw register bytes."""
    if not register:
        return None
    if isinstance(register, DmpResponse):
        return register.value
    value_hex = register.get("value_hex")
    if not value_hex:
        return None
    try:
        return bytes.fromhex(value_hex)
    except ValueError:
        return None


def dmp_uint(
    register: DmpResponse | dict[str, Any] | None,
) -> int | None:
    """Return an unsigned register value, excluding all-FF unavailable values."""
    value = dmp_bytes(register)
    if not value or all(byte == 0xFF for byte in value):
        return None
    return int.from_bytes(value, "big")


def dmp_bool(
    register: DmpResponse | dict[str, Any] | None,
) -> bool | None:
    """Return a boolean register value."""
    value = dmp_bytes(register)
    if not value or all(byte == 0xFF for byte in value):
        return None
    return value[0] != 0


def dmp_ascii(
    register: DmpResponse | dict[str, Any] | None,
) -> str | None:
    """Return a zero-stripped ASCII register value."""
    value = dmp_bytes(register)
    if not value:
        return None
    text = bytes(byte for byte in value if byte).decode("ascii", errors="ignore").strip()
    return text or None


def status_from_register(
    register: DmpResponse | dict[str, Any] | None,
) -> str | None:
    """Decode the controller state register."""
    value = dmp_bytes(register)
    return STATUS_BY_BYTE.get(value[0]) if value else None
