"""Domain errors for the Nice integration."""

from __future__ import annotations

import re


DMP_STATUS_NHK_FALLBACK_ERROR_CODES = frozenset({"5", "14"})
DMP_STATUS_COMMAND_ONLY_ERROR_CODES = frozenset({"14"})
RUNTIME_RETRYABLE_CONNECT_ERROR_CODES = frozenset({"7", "15"})


class NiceBidiError(Exception):
    """Base error for Nice operations."""


class NiceBidiAuthError(NiceBidiError):
    """Authentication failed."""


class NiceTransportError(NiceBidiError):
    """The selected transport failed."""


class NiceBidiConnectionError(NiceTransportError):
    """The device or relay did not respond correctly."""


class NiceProtocolError(NiceBidiConnectionError):
    """A protocol response was malformed or unsupported."""


class NiceReadPreemptedError(NiceBidiError):
    """A background read released the session for a pending command."""


class NiceUnsupportedError(NiceBidiError):
    """The requested capability is not supported."""


class NicePermissionError(NiceBidiError):
    """The active Nice user cannot perform the operation."""


class NiceUnsafeStateError(NiceBidiError):
    """The operation is unsafe in the current physical state."""


class NiceCalibrationError(NiceBidiError):
    """Calibration data or state is invalid."""


class NiceCloudError(NiceBidiError):
    """Base error for the optional MyNice credential bootstrap."""


class NiceCloudAuthError(NiceCloudError):
    """The MyNice account credentials or access token were rejected."""


class NiceCloudAccessError(NiceCloudError):
    """The authenticated account cannot access its bootstrap data."""


class NiceCloudConnectionError(NiceCloudError):
    """The MyNice bootstrap service could not be reached."""


class NiceCloudSchemaError(NiceCloudError):
    """The MyNice bootstrap response does not have a supported shape."""


def nice_error_code(err: Exception | str) -> str | None:
    """Return a Nice XML error code from an exception or response string."""
    match = re.search(r"<Code>\s*([^<\s]+)\s*</Code>", str(err))
    return match.group(1) if match else None


# Concise domain names for new layers; legacy names remain the concrete class
# names so existing logs and exception handling stay compatible.
NiceError = NiceBidiError
NiceAuthError = NiceBidiAuthError
NiceConnectionError = NiceBidiConnectionError
