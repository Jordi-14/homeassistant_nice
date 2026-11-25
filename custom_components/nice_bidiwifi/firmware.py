"""Firmware artifact and adapter contracts; checksums do not authenticate images."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .errors import NiceParameterError, NiceUnsupportedError


@dataclass(frozen=True, slots=True, repr=False)
class FirmwareArtifact:
    product: str
    hardware: tuple[str, ...]
    version: str
    protocol: str
    sha256: str
    size: int
    vendor_reference: str

    def check_integrity(self, image, *, product, hardware, protocol):
        if (
            not self.vendor_reference
            or not self.version
            or self.product != product
            or hardware not in self.hardware
            or self.protocol != protocol
        ):
            raise NiceParameterError("Firmware target or provenance does not match")
        if (
            not 0 < self.size <= 16 * 1024 * 1024
            or len(image) != self.size
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
            or hashlib.sha256(image).hexdigest() != self.sha256
        ):
            raise NiceParameterError("Firmware length or integrity does not match")
        # Returning from this method establishes integrity, not authenticity.


class FirmwareAdapter:
    """Replace the full verifier/state adapter, never a user-supplied verified flag."""

    def validate_vendor_image(self, artifact, image, **target):
        artifact.check_integrity(image, **target)
        raise NiceUnsupportedError(
            "Vendor image authentication, compatibility and recovery remain unverified"
        )

    async def install(self, artifact, image, **target):
        # No download, network access, erase, upload, boot or restart occurs here.
        self.validate_vendor_image(artifact, image, **target)
        raise NiceUnsupportedError("Firmware programming adapter is unavailable")
