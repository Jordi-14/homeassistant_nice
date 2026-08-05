"""Reviewed DMP targets and register profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DmpTarget:
    """One BusT4 destination."""

    address: int
    endpoint: int

    def as_tuple(self) -> tuple[int, int]:
        """Return the wire-level target pair."""
        return self.address, self.endpoint


@dataclass(frozen=True, slots=True)
class DmpRegisterProfile:
    """One named group of registers read from a target."""

    key: str
    target: DmpTarget
    registers: tuple[tuple[int, int], ...]
    required: bool


CONTROLLER_TARGET = DmpTarget(0x00, 0x03)
OXI_TARGET = DmpTarget(0x00, 0x0A)

CORE_STATUS_PROFILE = DmpRegisterProfile(
    key="controller_core_status",
    target=CONTROLLER_TARGET,
    registers=(
        (0x04, 0x01),
        (0x04, 0x11),
        (0x04, 0x18),
        (0x04, 0x19),
    ),
    required=True,
)

EXTENDED_CONTROLLER_PROFILE = DmpRegisterProfile(
    key="controller_extended_status",
    target=CONTROLLER_TARGET,
    registers=(
        (0x04, 0x12),
        (0x04, 0x21),
        (0x04, 0x22),
        (0x04, 0x23),
        (0x04, 0x42),
        (0x04, 0x43),
        (0x04, 0x4A),
        (0x04, 0x4B),
        (0x04, 0x71),
        (0x04, 0x72),
        (0x04, 0x73),
        (0x04, 0x74),
        (0x04, 0x80),
        (0x04, 0x81),
        (0x04, 0x84),
        (0x04, 0x85),
        (0x04, 0x86),
        (0x04, 0x88),
        (0x04, 0x89),
        (0x04, 0x8A),
        (0x04, 0x8C),
        (0x04, 0x94),
        (0x04, 0x9C),
        (0x04, 0xB1),
        (0x04, 0xB2),
        (0x04, 0xB3),
        (0x04, 0xD0),
        (0x04, 0xD1),
        (0x04, 0xD2),
        (0x04, 0xD4),
    ),
    required=False,
)
OXI_INFO_PROFILE = DmpRegisterProfile(
    key="oxi_identity",
    target=OXI_TARGET,
    registers=(
        (0x0A, 0x04),
        (0x0A, 0x09),
        (0x0A, 0x0A),
        (0x0A, 0x0B),
        (0x0A, 0x0C),
    ),
    required=False,
)
