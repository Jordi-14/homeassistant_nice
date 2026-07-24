"""TLS transport for the Nice Internet relay."""

from __future__ import annotations

import logging
import socket
import ssl

from .lan import SocketFrameTransport

_LOGGER = logging.getLogger(__name__)

RELAY_TLS_ENCRYPTED = True
RELAY_TLS_CERTIFICATE_VERIFICATION = False
RELAY_TLS_HOSTNAME_VERIFICATION = False
RELAY_TLS_COMPATIBILITY_REASON = "nice_relay_unverifiable_certificate"


def make_relay_tls_context() -> ssl.SSLContext:
    """Create the TLS context required by the Nice Internet relay.

    The production relay presents a self-signed, expired certificate and the
    official client accepts it without certificate or hostname verification.
    Keep TLS encryption and a modern protocol floor, but do not claim peer
    identity.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class RelayTlsTransport(SocketFrameTransport):
    """Encrypted transport compatible with the Nice Internet relay."""

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        timeout: float,
    ) -> RelayTlsTransport:
        """Connect to a relay whose certificate cannot be publicly verified."""
        raw: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            _LOGGER.debug(
                "Connecting to Nice relay with TLS peer verification disabled "
                "for service compatibility"
            )
            raw = socket.create_connection((host, port), timeout=timeout)
            raw.settimeout(timeout)
            tls_socket = make_relay_tls_context().wrap_socket(
                raw,
                server_hostname=host,
            )
            return cls(tls_socket)
        except BaseException:
            if tls_socket is not None:
                tls_socket.close()
            elif raw is not None:
                raw.close()
            raise
