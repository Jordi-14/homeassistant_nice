"""Device and INFO capability models for Nice."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NiceServiceCapability:
    """A service or property advertised by a Nice INFO request."""

    owner: str
    owner_id: str | None
    name: str
    path: str
    value_type: str | None
    permission: str | None
    values_raw: str | None
    values: tuple[str, ...]

    @property
    def readable(self) -> bool:
        """Return whether the capability is readable."""
        return "r" in (self.permission or "")

    @property
    def writable(self) -> bool:
        """Return whether the capability is writable."""
        return "w" in (self.permission or "")

    @property
    def emits_events(self) -> bool:
        """Return whether the capability advertises event delivery."""
        return "e" in (self.permission or "")


@dataclass(frozen=True, slots=True)
class NiceDeviceInfo:
    """Static interface and control-unit metadata."""

    interface_hw_version: str | None
    interface_fw_version: str | None
    interface_manufacturer: str | None
    interface_product: str | None
    interface_serial: str | None
    device_type: str | None
    device_manufacturer: str | None
    device_product: str | None
    device_description: str | None
    device_hw_version: str | None
    device_fw_version: str | None
    device_serial: str | None
    device_product_detail: str | None
    protocol_version: str | None = None
    services: tuple[NiceServiceCapability, ...] = ()
    properties: tuple[NiceServiceCapability, ...] = ()
