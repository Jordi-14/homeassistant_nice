"""Domain errors for the Nice integration."""

from __future__ import annotations

import re


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
    """The MyNice account or OAuth application credentials were rejected."""


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
