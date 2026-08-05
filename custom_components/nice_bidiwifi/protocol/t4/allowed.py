"""Strict decoding for the advertised T4 action bitmask."""

from __future__ import annotations

from dataclasses import dataclass
import re

MAX_T4_ALLOWED_HEX_DIGITS = 64
_HEX_MASK = re.compile(r"(?:0[xX])?([0-9A-Fa-f]+)")


@dataclass(frozen=True, slots=True)
class T4Allowed:
    """Decoded state of one optional ``T4_allowed`` declaration."""

    advertised: bool
    valid: bool | None
    mask: int | None

    def supports(self, action_code: int) -> bool | None:
        """Return whether an action bit is set."""
        if not self.advertised:
            return None
        if not self.valid or self.mask is None or action_code < 0:
            return False
        return bool(self.mask & (1 << action_code))


def decode_t4_allowed(
    raw_value: str | None,
    *,
    advertised: bool = True,
) -> T4Allowed:
    """Decode a bounded hexadecimal mask without accepting partial input."""
    if not advertised:
        return T4Allowed(advertised=False, valid=None, mask=None)
    if raw_value is None:
        return T4Allowed(advertised=True, valid=False, mask=None)

    token = raw_value.strip()
    match = _HEX_MASK.fullmatch(token)
    if match is None:
        return T4Allowed(advertised=True, valid=False, mask=None)
    digits = match.group(1)
    if len(digits) > MAX_T4_ALLOWED_HEX_DIGITS:
        return T4Allowed(advertised=True, valid=False, mask=None)
    return T4Allowed(advertised=True, valid=True, mask=int(digits, 16))
