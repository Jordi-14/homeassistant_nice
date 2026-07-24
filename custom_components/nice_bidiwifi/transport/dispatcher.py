"""Single-reader response and event dispatcher."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time

from ..protocol.nhk.codec import response_matches, response_summary
from .base import FrameTransport

_LOGGER = logging.getLogger(__name__)

RawEventCallback = Callable[[bytes], None]


class ResponseDispatcher:
    """Own all reads for one framed transport."""

    def __init__(self, transport: FrameTransport) -> None:
        self.transport = transport
        self._event_callbacks: list[RawEventCallback] = []

    def add_event_callback(self, callback: RawEventCallback) -> Callable[[], None]:
        """Register a raw unsolicited-frame callback."""
        self._event_callbacks.append(callback)

        def remove() -> None:
            if callback in self._event_callbacks:
                self._event_callbacks.remove(callback)

        return remove

    def _publish_event(self, frame: bytes) -> None:
        for callback in tuple(self._event_callbacks):
            try:
                callback(frame)
            except Exception:
                _LOGGER.exception("Nice unsolicited-frame callback failed")

    def read_frame(self, timeout: float) -> bytes:
        """Read one frame through the sole transport reader."""
        return self.transport.read_frame(timeout)

    def exchange(
        self,
        request: bytes,
        *,
        expected_type: str | None,
        expected_id: int | None,
        timeout: float,
        post_response_listen_seconds: float = 0.0,
    ) -> list[bytes]:
        """Send a request and collect its response plus adjacent events."""
        self.transport.send_frame(request)
        frames: list[bytes] = []
        deadline = time.monotonic() + timeout
        post_response_deadline: float | None = None

        while time.monotonic() < deadline:
            active_deadline = post_response_deadline or deadline
            response = self.read_frame(max(0.1, active_deadline - time.monotonic()))
            if not response:
                break
            frames.append(response)
            if response_matches(response, expected_type, expected_id):
                _LOGGER.debug("Received matching Nice response: %s", response_summary(response))
                if post_response_listen_seconds <= 0:
                    return frames
                post_response_deadline = time.monotonic() + post_response_listen_seconds
                deadline = post_response_deadline
                continue
            _LOGGER.debug("Received unsolicited Nice frame: %s", response_summary(response))
            self._publish_event(response)

        return frames
