"""Verified TLS transport for the Nice Internet relay."""

from __future__ import annotations

import socket
import ssl

from .lan import SocketFrameTransport


def make_relay_tls_context() -> ssl.SSLContext:
    """Create a public-PKI TLS context for an Internet endpoint."""
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class RelayTlsTransport(SocketFrameTransport):
    """A Nice relay transport with certificate and hostname verification."""

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        timeout: float,
    ) -> RelayTlsTransport:
        """Connect to the relay without weakening the system trust policy."""
        raw: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
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
