"""Credential models for Nice protocol adapters."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..errors import NiceAuthError


@dataclass(frozen=True, slots=True)
class NiceCredentials:
    """Credentials needed for NHK authentication."""

    username: str
    password_hex: str
    target_mac: str
    source_id: str | None = None

    @property
    def source(self) -> str:
        """Return the NHK source identifier."""
        return self.source_id or self.username

    @property
    def password(self) -> bytes:
        """Return the pairing password as raw bytes."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.password_hex):
            raise NiceAuthError("Password must be a 64-character hexadecimal value")
        return bytes.fromhex(self.password_hex)
