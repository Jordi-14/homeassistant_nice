"""Normalized configuration models for Nice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .credentials import NiceCredentials


class ConnectionMode(StrEnum):
    """Supported runtime connection policies."""

    LOCAL_WITH_CLOUD_FALLBACK = "local_with_cloud_fallback"
    LOCAL_ONLY = "local_only"
    CLOUD_ONLY = "cloud_only"


@dataclass(frozen=True, slots=True)
class NiceEndpoint:
    """One transport endpoint."""

    host: str
    port: int

    def __post_init__(self) -> None:
        """Validate endpoint values."""
        if not self.host.strip():
            raise ValueError("Endpoint host cannot be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("Endpoint port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class NiceConnectionPolicy:
    """Connection route policy for one entry."""

    mode: ConnectionMode = ConnectionMode.LOCAL_ONLY
    local: NiceEndpoint | None = None
    relay: NiceEndpoint | None = None

    def __post_init__(self) -> None:
        """Require the endpoints needed by the selected route policy."""
        if self.mode is ConnectionMode.LOCAL_ONLY and self.local is None:
            raise ValueError("Local-only mode requires a local endpoint")
        if self.mode is ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK and (
            self.local is None or self.relay is None
        ):
            raise ValueError("Local-with-cloud-fallback mode requires local and relay endpoints")
        if self.mode is ConnectionMode.CLOUD_ONLY and self.relay is None:
            raise ValueError("Cloud-only mode requires a relay endpoint")


@dataclass(frozen=True, slots=True)
class NiceEntryConfig:
    """Validated configuration consumed by the coordinator."""

    name: str
    credentials: NiceCredentials
    connection: NiceConnectionPolicy
    device_id: int
    t4_timeout_ms: int

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        title: str | None = None,
    ) -> NiceEntryConfig:
        """Build the validated runtime configuration from stored entry data."""
        mode = ConnectionMode(str(data.get("connection_mode", ConnectionMode.LOCAL_ONLY)))
        local = _endpoint_from_mapping(data, "host", "port", default_port=443)
        relay = _endpoint_from_mapping(data, "relay_host", "relay_port", default_port=443)
        return cls(
            name=str(data.get("name") or title or "Nice Gate"),
            credentials=NiceCredentials(
                username=_required_text(data, "username"),
                password_hex=_required_text(data, "password"),
                target_mac=_required_text(data, "target_mac"),
                source_id=str(data.get("source_id") or "").strip() or None,
            ),
            connection=NiceConnectionPolicy(mode=mode, local=local, relay=relay),
            device_id=_positive_int(data.get("device_id", 1), "device_id"),
            t4_timeout_ms=_positive_int(
                data.get("t4_timeout_ms", 200),
                "t4_timeout_ms",
            ),
        )


def _required_text(data: Mapping[str, Any], key: str) -> str:
    """Return one required non-empty configuration value."""
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required configuration value: {key}")
    return value


def _positive_int(value: Any, key: str) -> int:
    """Return a positive integer configuration value."""
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{key} must be positive")
    return parsed


def _endpoint_from_mapping(
    data: Mapping[str, Any],
    host_key: str,
    port_key: str,
    *,
    default_port: int,
) -> NiceEndpoint | None:
    """Return an endpoint when its host is configured."""
    host = str(data.get(host_key) or "").strip()
    if not host:
        return None
    return NiceEndpoint(host=host, port=int(data.get(port_key, default_port)))
