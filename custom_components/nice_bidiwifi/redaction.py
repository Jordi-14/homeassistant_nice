"""Central redaction helpers for integration-owned output."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "host",
        "password",
        "source_id",
        "target_mac",
        "username",
        "relay_host",
        "access_token",
        "refresh_token",
    }
)


def configured_secrets(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Return non-empty sensitive values from stored configuration."""
    return tuple(
        str(data[key])
        for key in SENSITIVE_CONFIG_KEYS
        if data.get(key) not in {None, ""}
    )


def redact_text(
    message: str | None,
    secrets: Iterable[str],
    *,
    replacement: str = "<redacted>",
) -> str | None:
    """Remove configured secrets from free-form text."""
    if message is None:
        return None
    redacted = message
    for secret in sorted(set(secrets), key=len, reverse=True):
        if not secret:
            continue
        redacted = re.sub(
            re.escape(secret),
            replacement,
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def allowed_config_diagnostics(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the explicit config fields permitted in diagnostics."""
    keys = (
        "name",
        "host",
        "port",
        "username",
        "password",
        "source_id",
        "target_mac",
        "device_id",
        "t4_timeout_ms",
        "connection_mode",
        "relay_host",
        "relay_port",
    )
    return {key: data[key] for key in keys if key in data}


def bounded_protocol_observations(
    values: Mapping[str, Any],
    *,
    limit: int = 128,
    value_limit: int = 128,
) -> dict[str, str]:
    """Return bounded printable protocol observations for diagnostics."""
    observations: dict[str, str] = {}
    for key in sorted(values):
        if len(observations) >= limit:
            break
        if not re.fullmatch(r"[A-Za-z0-9_./@-]{1,96}", str(key)):
            continue
        value = str(values[key])
        observations[str(key)] = "".join(
            character
            if character.isprintable()
            else "\N{REPLACEMENT CHARACTER}"
            for character in value[:value_limit]
        )
    return observations
