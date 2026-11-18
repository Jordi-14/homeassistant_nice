"""Local TLS transport for Nice interfaces."""

from __future__ import annotations

import socket
import ssl
import threading
import time
from typing import Protocol

from ..protocol.nhk.codec import ETX, STX

LEGACY_TLS_SECURITY_LEVEL_ZERO_REASONS = frozenset(
    {
        "DH_KEY_TOO_SMALL",
        "WRONG_SIGNATURE_TYPE",
    }
)

# An idle TLS read yields the SSL object promptly to a queued command.
_READ_SLICE_SECONDS = 0.05


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
        self._io_condition = threading.Condition()
        self._io_busy = False
        self._waiting_writers = 0
        # Reader polling must not shorten a subsequent write's configured timeout.
        gettimeout = getattr(connected_socket, "gettimeout", None)
        self._write_timeout: float | None = gettimeout() if gettimeout else 10.0

    def _acquire_io(self, *, writer: bool, deadline: float | None) -> bool:
        """Serialize SSL calls, giving pending writers priority over another read."""
        with self._io_condition:
            if writer:
                self._waiting_writers += 1
            try:
                while self._io_busy or (not writer and self._waiting_writers):
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        return False
                    self._io_condition.wait(timeout=remaining)
                if not writer and deadline is not None and time.monotonic() >= deadline:
                    return False
                self._io_busy = True
                return True
            finally:
                if writer:
                    self._waiting_writers -= 1
                    self._io_condition.notify_all()

    def _release_io(self) -> None:
        with self._io_condition:
            self._io_busy = False
            self._io_condition.notify_all()

    @property
    def connected(self) -> bool:
        """Return whether a socket is attached."""
        return self.socket is not None

    def send_frame(self, frame: bytes) -> None:
        """Send one complete frame."""
        deadline = None if self._write_timeout is None else time.monotonic() + self._write_timeout
        if not self._acquire_io(writer=True, deadline=deadline):
            raise TimeoutError("timed out waiting to send Nice frame")
        try:
            connected_socket = self.socket
            if connected_socket is None:
                raise OSError("socket is closed")
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            connected_socket.settimeout(remaining)
            connected_socket.sendall(frame)
        finally:
            self._release_io()

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
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if not self._acquire_io(writer=False, deadline=deadline):
                return b""
            try:
                connected_socket = self.socket
                if connected_socket is None:
                    return b""
                remaining = max(0.0, deadline - time.monotonic())
                connected_socket.settimeout(min(remaining, _READ_SLICE_SECONDS))
                chunk = connected_socket.recv(65535)
            except TimeoutError:
                return b""
            finally:
                self._release_io()
            if not chunk:
                raise OSError("socket closed by peer")
            self._buffer.extend(chunk)
            buffered = self._buffered_frame()
            if buffered is not None:
                return buffered

    def shutdown(self) -> None:
        """Wake a reader blocked in ``recv`` without releasing the descriptor.

        ``close()`` frees the file descriptor number immediately. If another
        thread is still inside ``recv`` (or OpenSSL's ``SSL_read``), the next
        ``open()`` anywhere in the process can receive that number and the
        stale reader then reads from and writes TLS records into an unrelated
        file. ``shutdown()`` ends the connection but keeps the descriptor
        allocated until the reader has stopped and ``close()`` runs.
        """
        connected_socket = self.socket
        if connected_socket is None or not hasattr(connected_socket, "shutdown"):
            return
        try:
            if isinstance(connected_socket, ssl.SSLSocket):
                # SSLSocket.shutdown() also detaches the SSL object, racing
                # with a reader that is inside it. Shut down only the TCP side.
                socket.socket.shutdown(connected_socket, socket.SHUT_RDWR)
            else:
                connected_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Already disconnected; the reader wakes up on its own.
            pass

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
