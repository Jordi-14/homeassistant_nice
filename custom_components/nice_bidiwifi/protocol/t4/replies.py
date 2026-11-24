"""Validate addressed DMP replies and surface their controller-level errors."""

from ...errors import NiceProtocolError, NiceResponseError, ProtocolLayer
from .codec import dmp_checksum
from .dmp import DmpResponse

DMP_ERRORS = {
    0xF0: "INFO not implemented",
    0xF1: "GET not implemented",
    0xF2: "SET not implemented",
    0xF7: "operation in progress",
    0xF8: "memory error",
    0xF9: "invalid parameters",
    0xFB: "sequence error",
    0xFC: "flag not found",
    0xFD: "command not found",
    0xFE: "profile not found",
    0xFF: "controller error",
}
UNSUPPORTED_CODES = frozenset({0xF0, 0xF1, 0xF2, 0xFD, 0xFE})
OPERATIONS = {0x09: "info", 0x19: "get", 0x29: "set"}


def checked_dmp_reply(
    plain: bytes, *, target: tuple[int, int], group: int, parameter: int, operation: int
) -> DmpResponse | None:
    """Match a reply before consuming data; unrelated/late replies are ignored."""
    if len(plain) < 16 or plain[0] != 0x55:
        return None
    if tuple(plain[4:6]) != target or plain[6] != 0x08:
        return None
    if plain[9:12] != bytes((group, parameter, operation)):
        return None
    body = plain[2:-1]
    if (
        plain[1] != len(body)
        or plain[-1] != len(body)
        or tuple(body[:2]) != (0x50, 0x91)
        or body[5] != len(body) - 7
        or dmp_checksum(*body[:6]) != body[6]
        or dmp_checksum(*body[7:-1]) != body[-1]
    ):
        raise NiceProtocolError("Malformed matching DMP reply")
    status = plain[13]
    if status >= 0xF0:
        raise NiceResponseError(
            f"DMP {OPERATIONS[operation].upper()} {group:02X}/{parameter:02X}: "
            f"{DMP_ERRORS.get(status, 'unknown controller error')} ({status:02X})",
            layer=ProtocolLayer.DMP,
            code=f"{status:02X}",
            request_type="T4_REQUEST",
            operation=OPERATIONS[operation],
            register=(group, parameter),
            unsupported=status in UNSUPPORTED_CODES,
        )
    if status != 0:
        raise NiceProtocolError(f"Unrecognized DMP reply status {status:02X}")
    value = plain[14:-2]
    if plain[12] != len(value):
        raise NiceProtocolError("DMP reply value length does not match its payload")
    return DmpResponse(plain.hex(" "), group, parameter, operation, status, value)
