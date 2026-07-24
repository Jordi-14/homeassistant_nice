"""Controller-specific safety policy for writable BusT4 registers."""

from __future__ import annotations

from .client import NiceBidiDeviceInfo
from .models.profiles import DMP_WRITE_RESTRICTIONS, normalized_device_identity


def dmp_write_block_reason(
    info: NiceBidiDeviceInfo | None,
    group: int,
    parameter: int,
) -> str | None:
    """Return why a register write is blocked for a known controller."""
    identity = normalized_device_identity(info)
    register = (group, parameter)
    for policy in DMP_WRITE_RESTRICTIONS:
        if register not in policy.registers:
            continue
        if all(identifier in identity for identifier in policy.identifiers):
            return policy.reason
    return None
