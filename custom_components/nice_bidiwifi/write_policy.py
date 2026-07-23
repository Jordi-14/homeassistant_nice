"""Controller-specific safety policy for writable BusT4 registers."""

from __future__ import annotations

from dataclasses import dataclass

from .client import NiceBidiDeviceInfo


@dataclass(frozen=True)
class DmpWriteBlock:
    """A known-unsafe controller/register combination."""

    controller: str
    required_identifiers: frozenset[str]
    registers: frozenset[tuple[int, int]]
    reason: str


DMP_WRITE_BLOCKS = (
    DmpWriteBlock(
        controller="ARIA200S / CLBOX",
        required_identifiers=frozenset({"aria200", "clbox"}),
        registers=frozenset({(0x04, 0x42), (0x04, 0x43)}),
        reason="speed values use an unverified controller-specific encoding",
    ),
)


def _device_identification_text(info: NiceBidiDeviceInfo | None) -> str:
    """Return normalized controller identity text for policy matching."""
    if info is None:
        return ""
    parts = (
        info.device_type,
        info.device_manufacturer,
        info.device_product,
        info.device_description,
        info.device_product_detail,
    )
    return "".join(
        character
        for part in parts
        if part
        for character in part.casefold()
        if character.isalnum()
    )


def dmp_write_block_reason(
    info: NiceBidiDeviceInfo | None,
    group: int,
    parameter: int,
) -> str | None:
    """Return why a register write is blocked for a known controller."""
    identity = _device_identification_text(info)
    register = (group, parameter)
    for policy in DMP_WRITE_BLOCKS:
        if register not in policy.registers:
            continue
        if all(identifier in identity for identifier in policy.required_identifiers):
            return f"{policy.controller}: {policy.reason}"
    return None
