"""Normalized Nice zeroconf discovery identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from .capabilities import ProductFamily

DEFAULT_NICE_PORT = 443
_SEPARATED_MAC_CANDIDATE = re.compile(
    r"(?i)([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5,})"
)
_COMPACT_MAC_CANDIDATE = re.compile(
    r"(?i)(?<![0-9a-f])([0-9a-f]{12})(?![0-9a-f])"
)


class NiceDiscoveryService(StrEnum):
    """Observed Nice Bonjour service types."""

    HAP = "_hap._tcp.local."
    NAP = "_nap._tcp.local."
    MFI_CONFIG = "_mfi-config._tcp.local."
    WNC_CONFIG = "_wnc-config._tcp.local."
    UNKNOWN = "unknown"

    @property
    def provisioning(self) -> bool:
        """Return whether this service exists only during provisioning."""
        return self in {self.MFI_CONFIG, self.WNC_CONFIG}

    @property
    def operational(self) -> bool:
        """Return whether this service can represent an operational interface."""
        return self in {self.HAP, self.NAP}


def normalize_device_id(value: str | None) -> str | None:
    """Normalize a MAC-like stable accessory identifier."""
    if not value:
        return None
    compact = re.sub(r"[\s:.-]", "", value)
    if len(compact) != 12 or re.fullmatch(r"[0-9A-Fa-f]{12}", compact) is None:
        return None
    compact = compact.upper()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _properties(properties: Mapping[str, Any]) -> dict[str, str]:
    return {
        key_text.casefold(): value
        for key, raw_value in properties.items()
        if (key_text := _text(key)) is not None
        and (value := _text(raw_value)) is not None
    }


def _first(properties: Mapping[str, str], *keys: str) -> str | None:
    return next(
        (properties[key] for key in keys if key in properties),
        None,
    )


def _identity_from_text(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        if match := _SEPARATED_MAC_CANDIDATE.search(value):
            octets = re.split(r"[:-]", match.group(1))
            if normalized := normalize_device_id("".join(octets[-6:])):
                return normalized
        if match := _COMPACT_MAC_CANDIDATE.search(value):
            if normalized := normalize_device_id(match.group(1)):
                return normalized
    return None


def _family(model: str | None, name: str) -> ProductFamily:
    token = re.sub(r"[^a-z0-9]", "", f"{model or ''} {name}".casefold())
    if "bidiwifi" in token:
        return ProductFamily.BIDI_WIFI
    if "it4wifi" in token:
        return ProductFamily.IT4_WIFI
    if "cuwifi" in token:
        return ProductFamily.CU_WIFI
    if "proview" in token:
        return ProductFamily.PROVIEW
    if "core" in token:
        return ProductFamily.CORE
    return ProductFamily.UNKNOWN


def _instance_name(name: str, service_type: str) -> str:
    suffix = f".{service_type}"
    if name.casefold().endswith(suffix.casefold()):
        name = name[: -len(suffix)]
    return name.strip().rstrip(".") or "Nice"


@dataclass(frozen=True, slots=True)
class NiceDiscoveryInfo:
    """Validated discovery data for one Nice network interface."""

    unique_id: str | None
    host: str
    addresses: tuple[str, ...]
    port: int
    name: str
    service: NiceDiscoveryService
    family: ProductFamily
    model: str | None = None
    manufacturer: str | None = None
    hardware: str | None = None
    protocol_version: str | None = None
    status_flag: str | None = None

    @classmethod
    def from_service(
        cls,
        *,
        host: str,
        port: int | None,
        name: str,
        hostname: str,
        service_type: str,
        properties: Mapping[str, Any],
        addresses: tuple[str, ...] = (),
    ) -> NiceDiscoveryInfo:
        """Parse Home Assistant zeroconf data using observed TXT fallbacks."""
        normalized_properties = _properties(properties)
        model = _first(normalized_properties, "model", "md")
        instance_name = _instance_name(name, service_type)
        unique_id = normalize_device_id(
            _first(
                normalized_properties,
                "deviceid",
                "id",
                "mac",
                "macaddress",
            )
        ) or _identity_from_text(instance_name, hostname)
        try:
            service = NiceDiscoveryService(service_type.casefold())
        except ValueError:
            service = NiceDiscoveryService.UNKNOWN

        model_parts = (
            tuple(part.strip() for part in model.split(" - "))
            if model
            else ()
        )
        manufacturer = _first(
            normalized_properties,
            "manufacturer",
            "manuf",
            "mf",
        ) or (model_parts[0] if len(model_parts) >= 2 else None)
        hardware = _first(
            normalized_properties,
            "hardware",
            "hardwareid",
            "hw",
        ) or (model_parts[-1] if len(model_parts) >= 3 else None)
        protocol_version = _first(
            normalized_properties,
            "protovers",
            "protocol",
            "pv",
        )
        status_flag = _first(normalized_properties, "sf")
        normalized_host = host.strip()
        normalized_addresses = tuple(
            dict.fromkeys(
                address.strip()
                for address in (normalized_host, *addresses)
                if address.strip()
            )
        )
        return cls(
            unique_id=unique_id,
            host=normalized_host,
            addresses=normalized_addresses,
            port=port or DEFAULT_NICE_PORT,
            name=instance_name,
            service=service,
            family=_family(model, instance_name),
            model=model,
            manufacturer=manufacturer,
            hardware=hardware,
            protocol_version=protocol_version,
            status_flag=status_flag,
        )

    @property
    def provisioning(self) -> bool:
        """Return whether this is a setup access point, not an interface."""
        return self.service.provisioning

    @property
    def operational(self) -> bool:
        """Return whether the service type is operational."""
        return self.service.operational

    @property
    def supported_family(self) -> bool:
        """Return whether the current local client supports this family."""
        return self.family not in {
            ProductFamily.CORE,
            ProductFamily.PROVIEW,
        }

    def entry_metadata(self) -> dict[str, Any]:
        """Serialize bounded discovery metadata for config-entry storage."""
        metadata = {
            "discovery_service_type": self.service.value,
            "discovery_name": self.name,
            "discovery_addresses": list(self.addresses),
        }
        optional = {
            "discovery_model": self.model,
            "discovery_manufacturer": self.manufacturer,
            "discovery_hardware": self.hardware,
            "discovery_protocol": self.protocol_version,
            "discovery_status_flag": self.status_flag,
        }
        metadata.update(
            {
                key: value
                for key, value in optional.items()
                if value is not None
            }
        )
        return metadata
