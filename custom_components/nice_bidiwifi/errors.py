"""Domain errors for the Nice integration."""

from __future__ import annotations

import re
from enum import StrEnum


DMP_STATUS_NHK_FALLBACK_ERROR_CODES = frozenset({"5", "14"})
DMP_STATUS_COMMAND_ONLY_ERROR_CODES = frozenset({"14"})
RUNTIME_RETRYABLE_CONNECT_ERROR_CODES = frozenset({"7", "15"})
# The interface answered a signed request on a working session but does not
# support it, e.g. DMP status reads on CU_WIFI or an unadvertised DEP action.
UNSUPPORTED_REQUEST_ERROR_CODES = frozenset({"5", "14"})


class NiceBidiError(Exception):
    """Base error for Nice operations."""

    def __init__(self, message: str = "", *, layer: str | None = None,
                 code: str | None = None, request_type: str | None = None) -> None:
        super().__init__(message)
        self.layer = layer
        self.code = code
        self.request_type = request_type
        self.delivery = "unknown"


class NiceBidiAuthError(NiceBidiError):
    """Authentication failed."""


class NiceTransportError(NiceBidiError):
    """The selected transport failed."""


class NiceBidiConnectionError(NiceTransportError):
    """The device or relay did not respond correctly."""


class NiceProtocolError(NiceBidiConnectionError):
    """A protocol response was malformed or unsupported."""


class ProtocolLayer(StrEnum):
    NHK = "nhk"
    DMP = "dmp"


class NiceResponseError(NiceBidiConnectionError):
    """An explicit device response, distinct from a transport failure."""

    def __init__(self, message: str, *, layer: ProtocolLayer, code: str,
                 request_type: str, operation: str | None = None,
                 register: tuple[int, int] | None = None,
                 unsupported: bool = False) -> None:
        super().__init__(message)
        self.layer = layer
        self.code = code
        self.request_type = request_type
        self.operation = operation
        self.register = register
        self.unsupported = unsupported
        # NHK 14 reports a bus timeout, not proof that a write was refused.
        self.delivery_unknown = layer is ProtocolLayer.NHK and code == "14"
        self.delivery = "unknown" if self.delivery_unknown else "sent"


class NiceParameterError(NiceBidiError, ValueError):
    """A parameter cannot be represented or safely written with its metadata."""

    def __init__(self, message: str) -> None:
        super().__init__(message, layer="validation")
        self.delivery = "not_sent"


class NiceReceiverFormatError(NiceBidiError):
    """The receiver answered, but its record layout or target is unverified.

    This is not evidence of a broken TLS session and must not cause reconnects
    or automatic replay of an otherwise valid, answered inspection request.
    """


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
    if isinstance(err, NiceResponseError):
        return err.code if err.layer is ProtocolLayer.NHK else None
    if isinstance(err, NiceBidiError) and err.layer == "nhk" and err.code is not None:
        return err.code
    match = re.search(r"<Code>\s*([^<\s]+)\s*</Code>", str(err))
    return match.group(1) if match else None


def is_unsupported_request_error(err: Exception) -> bool:
    """Return whether a working session rejected only the request itself."""
    if isinstance(err, NiceResponseError):
        return err.unsupported
    return (
        isinstance(err, NiceBidiConnectionError)
        and nice_error_code(err) in UNSUPPORTED_REQUEST_ERROR_CODES
    )


def is_request_rejection(err: Exception) -> bool:
    """An answered request should not be retried as a broken connection."""
    return isinstance(err, NiceResponseError) or is_unsupported_request_error(err)


# Concise domain names for new layers; legacy names remain the concrete class
# names so existing logs and exception handling stay compatible.
NiceError = NiceBidiError
NiceAuthError = NiceBidiAuthError
NiceConnectionError = NiceBidiConnectionError
