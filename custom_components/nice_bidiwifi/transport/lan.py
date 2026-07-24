"""Local TLS transport for Nice interfaces."""

from __future__ import annotations

import socket
import ssl
from typing import Protocol

from ..protocol.nhk.codec import ETX, STX


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


def make_local_tls_context() -> ssl.SSLContext:
    """Create the TLS context required by the local Nice endpoint."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # The local interface exposes a device certificate outside the HA trust
    # store. Authentication is performed by the subsequent NHK handshake.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
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
                return b""
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

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        timeout: float,
    ) -> LanTlsTransport:
        """Connect and complete the constrained local TLS handshake."""
        raw: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            raw = socket.create_connection((host, port), timeout=timeout)
            raw.settimeout(timeout)
            tls_socket = make_local_tls_context().wrap_socket(
                raw,
                server_hostname=None,
                do_handshake_on_connect=False,
            )
            tls_socket.do_handshake()
            return cls(tls_socket)
        except BaseException:
            if tls_socket is not None:
                tls_socket.close()
            elif raw is not None:
                raw.close()
            raise
