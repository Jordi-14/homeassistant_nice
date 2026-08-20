"""Local TLS transport for Nice interfaces."""

from __future__ import annotations

import socket
import ssl
from typing import Protocol

from ..protocol.nhk.codec import ETX, STX

LEGACY_TLS_SECURITY_LEVEL_ZERO_REASONS = frozenset(
    {
        "DH_KEY_TOO_SMALL",
        "WRONG_SIGNATURE_TYPE",
    }
)


class SocketLike(Protocol):
    """The socket operations required by the framed transport."""

    def settimeout(self, value: float | None) -> None:
        """Set the socket timeout."""

    def sendall(self, data: bytes) -> None:
        """Send bytes."""

    def recv(self, size: int) -> bytes:
        """Receive bytes."""

    def close(self) -> None:
        """Close the socket."""


def make_local_tls_context(
    *,
    legacy_compatibility: bool = False,
    cipher_security_level: int | None = None,
) -> ssl.SSLContext:
    """Create the TLS context required by the local Nice endpoint."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # The local interface exposes a device certificate outside the HA trust
    # store. Authentication is performed by the subsequent NHK handshake.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    if legacy_compatibility:
        legacy_option = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", None)
        if legacy_option is None:
            raise ssl.SSLError(
                "Legacy server compatibility is unavailable in this OpenSSL build"
            )
        if cipher_security_level not in {0, 1}:
            raise ValueError(
                "Legacy local TLS cipher security level must be 0 or 1"
            )
        context.options |= legacy_option
        context.set_ciphers(
            f"DEFAULT:@SECLEVEL={cipher_security_level}"
        )
    return context


class SocketFrameTransport:
    """Frame a connected socket and retain incomplete/combined reads."""

    def __init__(self, connected_socket: SocketLike) -> None:
        self.socket: SocketLike | None = connected_socket
        self._buffer = bytearray()

    @property
    def connected(self) -> bool:
        """Return whether a socket is attached."""
        return self.socket is not None

    def send_frame(self, frame: bytes) -> None:
        """Send one complete frame."""
        if self.socket is None:
            raise OSError("socket is closed")
        self.socket.sendall(frame)

    def _buffered_frame(self) -> bytes | None:
        try:
            start = self._buffer.index(STX[0])
        except ValueError:
            self._buffer.clear()
            return None
        try:
            end = self._buffer.index(ETX[0], start + 1)
        except ValueError:
            if start:
                del self._buffer[:start]
            return None
        frame = bytes(self._buffer[start : end + 1])
        del self._buffer[: end + 1]
        return frame

    def read_frame(self, timeout: float) -> bytes:
        """Read exactly one STX/ETX frame."""
        buffered = self._buffered_frame()
        if buffered is not None:
            return buffered
        if self.socket is None:
            return b""
        self.socket.settimeout(timeout)
        while True:
            try:
                chunk = self.socket.recv(65535)
            except TimeoutError:
                return b""
            if not chunk:
                raise OSError("socket closed by peer")
            self._buffer.extend(chunk)
            buffered = self._buffered_frame()
            if buffered is not None:
                return buffered

    def close(self) -> None:
        """Close the socket and discard buffered data."""
        connected_socket = self.socket
        self.socket = None
        self._buffer.clear()
        if connected_socket is not None:
            connected_socket.close()


class LanTlsTransport(SocketFrameTransport):
    """A local Nice TLS socket transport."""

    def __init__(
        self,
        connected_socket: SocketLike,
        *,
        legacy_compatibility: bool = False,
        cipher_security_level: int | None = None,
    ) -> None:
        super().__init__(connected_socket)
        self.legacy_compatibility = legacy_compatibility
        self.cipher_security_level = cipher_security_level

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        timeout: float,
    ) -> LanTlsTransport:
        """Connect and complete the constrained local TLS handshake."""
        return cls._connect_with_context(
            host,
            port,
            timeout,
            make_local_tls_context(),
        )

    @classmethod
    def _connect_with_context(
        cls,
        host: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
        *,
        legacy_compatibility: bool = False,
        cipher_security_level: int | None = None,
    ) -> LanTlsTransport:
        """Open one socket and complete its TLS handshake."""
        raw: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            raw = socket.create_connection((host, port), timeout=timeout)
            raw.settimeout(timeout)
            tls_socket = context.wrap_socket(
                raw,
                server_hostname=None,
                do_handshake_on_connect=False,
            )
            tls_socket.do_handshake()
            return cls(
                tls_socket,
                legacy_compatibility=legacy_compatibility,
                cipher_security_level=cipher_security_level,
            )
        except BaseException:
            if tls_socket is not None:
                tls_socket.close()
            elif raw is not None:
                raw.close()
            raise


def _requires_security_level_zero(err: ssl.SSLError) -> bool:
    """Return whether OpenSSL reported an approved legacy security error."""
    reason = str(getattr(err, "reason", "") or "").upper()
    if reason in LEGACY_TLS_SECURITY_LEVEL_ZERO_REASONS:
        return True
    detail = str(err).upper()
    return any(
        f"[SSL: {approved_reason}]" in detail
        for approved_reason in LEGACY_TLS_SECURITY_LEVEL_ZERO_REASONS
    )


class LegacyLanTlsTransport(LanTlsTransport):
    """Explicitly enabled TLS compatibility for older local interfaces."""

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        timeout: float,
    ) -> LegacyLanTlsTransport:
        """Try security level 1, relaxing to 0 only for a small DH key."""
        try:
            return cls._connect_with_context(
                host,
                port,
                timeout,
                make_local_tls_context(
                    legacy_compatibility=True,
                    cipher_security_level=1,
                ),
                legacy_compatibility=True,
                cipher_security_level=1,
            )
        except ssl.SSLError as err:
            if not _requires_security_level_zero(err):
                raise
        return cls._connect_with_context(
            host,
            port,
            timeout,
            make_local_tls_context(
                legacy_compatibility=True,
                cipher_security_level=0,
            ),
            legacy_compatibility=True,
            cipher_security_level=0,
        )
