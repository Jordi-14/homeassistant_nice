"""Normalized capability model for Nice devices."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .device import NiceDeviceInfo, NiceServiceCapability
from ..protocol.t4.allowed import T4Allowed, decode_t4_allowed
from ..protocol.t4.actions import T4_ACTION_BY_CODE


class ProductFamily(StrEnum):
    """Known Nice product protocol families."""

    BIDI_WIFI = "bidiwifi"
    IT4_WIFI = "it4wifi"
    CU_WIFI = "cu_wifi"
    CORE = "core"
    PROVIEW = "proview"
    UNKNOWN = "unknown"


class CapabilityConfidence(StrEnum):
    """Origin confidence for normalized capability decisions."""

    ADVERTISED = "advertised"
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


def _normalize_product(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _product_family(info: NiceDeviceInfo) -> ProductFamily:
    product = _normalize_product(info.interface_product)
    if "bidiwifi" in product:
        return ProductFamily.BIDI_WIFI
    if "it4wifi" in product:
        return ProductFamily.IT4_WIFI
    if "cuwifi" in product:
        return ProductFamily.CU_WIFI
    if product == "core" or product.startswith("core"):
        return ProductFamily.CORE
    if "proview" in product:
        return ProductFamily.PROVIEW
    return ProductFamily.UNKNOWN


def _matching_capability(
    capabilities: tuple[NiceServiceCapability, ...],
    name: str,
    device_id: int,
) -> NiceServiceCapability | None:
    target_id = str(device_id)
    for capability in capabilities:
        if capability.name != name:
            continue
        if capability.owner == "Device" and capability.owner_id not in {None, target_id}:
            continue
        return capability
    return None


def _decode_t4_allowed(
    properties: tuple[NiceServiceCapability, ...],
    device_id: int,
) -> T4Allowed:
    capability = _matching_capability(properties, "T4_allowed", device_id)
    return decode_t4_allowed(
        capability.values_raw if capability is not None else None,
        advertised=capability is not None,
    )


@dataclass(frozen=True, slots=True)
class NiceCapabilities:
    """Normalized capabilities for one interface and automation."""

    family: ProductFamily
    device_id: int
    services: tuple[NiceServiceCapability, ...]
    properties: tuple[NiceServiceCapability, ...]
    t4_allowed: T4Allowed
    high_level_actions: bool | None
    readable_status: bool | None
    obstruction: bool | None
    profile_key: str = "unknown"
    product_detail: str | None = None
    interface_firmware: str | None = None
    interface_hardware: str | None = None
    device_firmware: str | None = None
    device_hardware: str | None = None
    observed_dmp_registers: frozenset[str] = frozenset()
    local_available: bool = True
    relay_available: bool = False
    confidence: CapabilityConfidence = CapabilityConfidence.ADVERTISED
    status_sources: frozenset[str] = frozenset()
    position_sources: frozenset[str] = frozenset()
    local_events: bool | None = None
    diagnostic_events: bool | None = None
    logs: bool | None = None
    groups: bool | None = None
    tables: bool | None = None

    @classmethod
    def from_device_info(
        cls,
        info: NiceDeviceInfo,
        device_id: int = 1,
    ) -> NiceCapabilities:
        """Build normalized capabilities from INFO metadata."""
        door_action = _matching_capability(info.services, "DoorAction", device_id)
        door_status = _matching_capability(info.properties, "DoorStatus", device_id)
        obstruction = _matching_capability(info.properties, "Obstruct", device_id)
        return cls(
            family=_product_family(info),
            device_id=device_id,
            services=info.services,
            properties=info.properties,
            t4_allowed=_decode_t4_allowed(
                (*info.properties, *info.services),
                device_id,
            ),
            high_level_actions=(
                door_action.writable if door_action is not None else None
            ),
            readable_status=(
                door_status.readable if door_status is not None else None
            ),
            obstruction=(
                obstruction.readable if obstruction is not None else None
            ),
            product_detail=info.device_product_detail,
            interface_firmware=info.interface_fw_version,
            interface_hardware=info.interface_hw_version,
            device_firmware=info.device_fw_version,
            device_hardware=info.device_hw_version,
            status_sources=(
                frozenset({"nhk_door_status"})
                if door_status is not None and door_status.readable
                else frozenset()
            ),
        )

    def supports_t4_action(self, action_code: int) -> bool | None:
        """Return support for a T4 action, or unknown when no mask is advertised."""
        return self.t4_allowed.supports(action_code)

    @property
    def t4_allowed_mask(self) -> int | None:
        """Return the decoded mask when valid."""
        return self.t4_allowed.mask

    @property
    def t4_allowed_valid(self) -> bool | None:
        """Return validity, or unknown when the property is absent."""
        return self.t4_allowed.valid

    @property
    def supported_t4_action_codes(self) -> frozenset[int] | None:
        """Return advertised action codes that exist in the reviewed catalog."""
        if not self.t4_allowed.advertised:
            return None
        if not self.t4_allowed.valid or self.t4_allowed.mask is None:
            return frozenset()
        return frozenset(
            code
            for code in T4_ACTION_BY_CODE
            if self.t4_allowed.mask & (1 << code)
        )
