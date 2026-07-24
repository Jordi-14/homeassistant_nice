"""Validated device profiles and controller quirks."""

from __future__ import annotations

from dataclasses import dataclass

from .capabilities import ProductFamily
from .device import NiceDeviceInfo


@dataclass(frozen=True, slots=True)
class DmpWriteRestriction:
    """One confirmed unsafe write profile."""

    key: str
    identifiers: frozenset[str]
    registers: frozenset[tuple[int, int]]
    reason: str


@dataclass(frozen=True, slots=True)
class NiceDeviceProfile:
    """Validated behavior shared by a product family."""

    key: str
    families: frozenset[ProductFamily]
    dmp_status: bool | None
    high_level_actions: bool | None


SHARED_WIFI_PROFILE = NiceDeviceProfile(
    key="shared_wifi",
    families=frozenset(
        {
            ProductFamily.BIDI_WIFI,
            ProductFamily.IT4_WIFI,
            ProductFamily.CU_WIFI,
        }
    ),
    dmp_status=None,
    high_level_actions=None,
)

CONSERVATIVE_PROFILE = NiceDeviceProfile(
    key="unknown",
    families=frozenset({ProductFamily.UNKNOWN}),
    dmp_status=None,
    high_level_actions=None,
)

DMP_WRITE_RESTRICTIONS = (
    DmpWriteRestriction(
        key="aria200s_clbox_speed_encoding",
        identifiers=frozenset({"aria200", "clbox"}),
        registers=frozenset({(0x04, 0x42), (0x04, 0x43)}),
        reason=(
            "ARIA200S / CLBOX: speed values use an unverified "
            "controller-specific encoding"
        ),
    ),
)


def profile_for_family(family: ProductFamily) -> NiceDeviceProfile:
    """Return the validated profile for a normalized product family."""
    if family in SHARED_WIFI_PROFILE.families:
        return SHARED_WIFI_PROFILE
    return CONSERVATIVE_PROFILE


def normalized_device_identity(info: NiceDeviceInfo | None) -> str:
    """Return controller identity text suitable for exact quirk predicates."""
    if info is None:
        return ""
    return "".join(
        character
        for part in (
            info.device_type,
            info.device_manufacturer,
            info.device_product,
            info.device_description,
            info.device_product_detail,
        )
        if part
        for character in part.casefold()
        if character.isalnum()
    )
