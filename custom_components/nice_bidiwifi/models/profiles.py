"""Validated device profiles and controller quirks."""

from __future__ import annotations

from dataclasses import dataclass

from .capabilities import ProductFamily
from .device import NiceDeviceInfo


@dataclass(frozen=True, slots=True)
class DmpWriteRestriction:
    """One confirmed unsafe write profile."""

    key: str
    identity_alternatives: tuple[frozenset[str], ...]
    registers: frozenset[tuple[int, int]]
    reason: str

    def matches_identity(self, identity: str) -> bool:
        """Return whether any validated identity alternative matches."""
        return any(
            all(identifier in identity for identifier in alternative)
            for alternative in self.identity_alternatives
        )


@dataclass(frozen=True, slots=True)
class NiceDeviceProfile:
    """Validated behavior shared by a product family."""

    key: str
    families: frozenset[ProductFamily]
    dmp_status: bool | None
    high_level_actions: bool | None
    access_contract: str = "shared_wifi"
    blocked_gap: str | None = None


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

CORE_PROFILE = NiceDeviceProfile("core", frozenset({ProductFamily.CORE}), None, None, "core", "transport.core")
PROVIEW_PROFILE = NiceDeviceProfile("proview", frozenset({ProductFamily.PROVIEW}), None, None, "proview", "transport.proview")

DMP_WRITE_RESTRICTIONS = (
    DmpWriteRestriction(
        key="aria200s_clbox_speed_encoding",
        identity_alternatives=(
            frozenset({"aria200"}),
            frozenset({"clbox"}),
        ),
        registers=frozenset({(0x04, 0x42), (0x04, 0x43)}),
        reason=(
            "ARIA200S / CLBOX: speed values use an unverified "
            "controller-specific encoding"
        ),
    ),
)


def profile_for_family(family: ProductFamily) -> NiceDeviceProfile:
    """Return the validated profile for a normalized product family."""
    for profile in (SHARED_WIFI_PROFILE, CORE_PROFILE, PROVIEW_PROFILE):
        if family in profile.families:
            return profile
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
