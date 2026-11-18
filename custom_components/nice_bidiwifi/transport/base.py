"""Transport contracts for framed Nice communication."""

from __future__ import annotations

from typing import Protocol


class FrameTransport(Protocol):
    """A connected framed byte transport.

    The dispatcher can call send_frame and read_frame from different threads.
    Implementations sharing mutable TLS state must serialize the actual I/O.
    Shutdown may wake I/O without releasing the descriptor; final close follows
    the dispatcher's socket-user lifetime checks.
    """

    @property
    def connected(self) -> bool:
        """Return whether the transport is connected."""

    def send_frame(self, frame: bytes) -> None:
        """Send one complete frame."""

    def read_frame(self, timeout: float) -> bytes:
        """Read one complete frame or return empty bytes on timeout."""

    def close(self) -> None:
        """Close the transport."""
