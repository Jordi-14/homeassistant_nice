"""Transport contracts for framed Nice communication."""

from __future__ import annotations

from typing import Protocol


class FrameTransport(Protocol):
    """A connected framed byte transport."""

    @property
    def connected(self) -> bool:
        """Return whether the transport is connected."""

    def send_frame(self, frame: bytes) -> None:
        """Send one complete frame."""

    def read_frame(self, timeout: float) -> bytes:
        """Read one complete frame or return empty bytes on timeout."""

    def close(self) -> None:
        """Close the transport."""
