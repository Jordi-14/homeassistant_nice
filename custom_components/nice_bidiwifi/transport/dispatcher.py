"""Single-reader response and event dispatcher."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import threading
import time

from ..protocol.nhk.codec import response_matches, response_summary
from .base import FrameTransport

_LOGGER = logging.getLogger(__name__)

RawEventCallback = Callable[[bytes], None]
ReaderFailureCallback = Callable[[Exception], None]

_READER_TIMEOUT_SECONDS = 0.25
_MAX_EXCHANGE_FRAMES = 64


@dataclass(slots=True)
class _PendingExchange:
    """State for one request waiting on the shared reader."""

    expected_type: str | None
    expected_id: int | None
    post_response_listen_seconds: float
    frames: list[bytes] = field(default_factory=list)
    matched: bool = False
    post_response_deadline: float | None = None


class ResponseDispatcher:
    """Own every read and correlate responses for one framed transport."""

    def __init__(self, transport: FrameTransport) -> None:
        self.transport = transport
        self._condition = threading.Condition()
        self._pending: list[_PendingExchange] = []
        self._event_callbacks: list[RawEventCallback] = []
        self._failure_callbacks: list[ReaderFailureCallback] = []
        self._reader_thread: threading.Thread | None = None
        self._closed = False
        self._failure: Exception | None = None

    @property
    def running(self) -> bool:
        """Return whether the background reader is alive."""
        with self._condition:
            return bool(self._reader_thread and self._reader_thread.is_alive())

    @property
    def failure(self) -> Exception | None:
        """Return the terminal reader failure, if any."""
        with self._condition:
            return self._failure

    def add_event_callback(self, callback: RawEventCallback) -> Callable[[], None]:
        """Register a raw unsolicited-frame callback."""
        with self._condition:
            self._event_callbacks.append(callback)

        def remove() -> None:
            self.remove_event_callback(callback)

        return remove

    def remove_event_callback(self, callback: RawEventCallback) -> None:
        """Remove a raw unsolicited-frame callback."""
        with self._condition:
            if callback in self._event_callbacks:
                self._event_callbacks.remove(callback)
            self._condition.notify_all()

    def add_failure_callback(
        self,
        callback: ReaderFailureCallback,
    ) -> Callable[[], None]:
        """Register a callback for terminal reader failures."""
        with self._condition:
            self._failure_callbacks.append(callback)

        def remove() -> None:
            self.remove_failure_callback(callback)

        return remove

    def remove_failure_callback(self, callback: ReaderFailureCallback) -> None:
        """Remove a reader-failure callback."""
        with self._condition:
            if callback in self._failure_callbacks:
                self._failure_callbacks.remove(callback)
            self._condition.notify_all()

    def start(self) -> None:
        """Start the sole reader when the connection first becomes active."""
        with self._condition:
            if self._closed:
                raise OSError("response dispatcher is closed")
            if self._failure is not None:
                raise OSError(str(self._failure)) from self._failure
            if self._reader_thread and self._reader_thread.is_alive():
                return
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="nice-bidiwifi-reader",
                daemon=True,
            )
            self._reader_thread.start()

    def _publish_event(self, frame: bytes) -> None:
        with self._condition:
            callbacks = tuple(self._event_callbacks)
        for callback in callbacks:
            try:
                callback(frame)
            except Exception:
                _LOGGER.exception("Nice unsolicited-frame callback failed")

    def _publish_failure(self, error: Exception) -> None:
        with self._condition:
            callbacks = tuple(self._failure_callbacks)
        for callback in callbacks:
            try:
                callback(error)
            except Exception:
                _LOGGER.exception("Nice reader-failure callback failed")

    def _reader_loop(self) -> None:
        """Continuously route complete frames from the transport."""
        while True:
            with self._condition:
                if self._closed:
                    return
            try:
                frame = self.transport.read_frame(_READER_TIMEOUT_SECONDS)
            except Exception as err:
                with self._condition:
                    if self._closed:
                        return
                    self._failure = err
                    self._condition.notify_all()
                self._publish_failure(err)
                return
            if not frame:
                with self._condition:
                    if (
                        not self._pending
                        and not self._event_callbacks
                        and not self._failure_callbacks
                    ):
                        return
                    self._condition.wait(timeout=0.01)
                continue

            publish = True
            now = time.monotonic()
            with self._condition:
                for pending in self._pending:
                    if not pending.matched and response_matches(
                        frame,
                        pending.expected_type,
                        pending.expected_id,
                    ):
                        self._append_exchange_frame(pending, frame)
                        pending.matched = True
                        publish = False
                        if pending.post_response_listen_seconds > 0:
                            pending.post_response_deadline = (
                                now + pending.post_response_listen_seconds
                            )
                        _LOGGER.debug(
                            "Received matching Nice response: %s",
                            response_summary(frame),
                        )
                        break

                if publish:
                    for pending in self._pending:
                        self._append_exchange_frame(pending, frame)
                self._condition.notify_all()

            if publish:
                _LOGGER.debug(
                    "Received unsolicited Nice frame: %s",
                    response_summary(frame),
                )
                self._publish_event(frame)

    @staticmethod
    def _append_exchange_frame(
        pending: _PendingExchange,
        frame: bytes,
    ) -> None:
        """Retain a bounded compatibility window around one response."""
        if len(pending.frames) >= _MAX_EXCHANGE_FRAMES:
            pending.frames.pop(0)
        pending.frames.append(frame)

    def exchange(
        self,
        request: bytes,
        *,
        expected_type: str | None,
        expected_id: int | None,
        timeout: float,
        post_response_listen_seconds: float = 0.0,
    ) -> list[bytes]:
        """Send a request and collect its correlated response plus adjacent events."""
        pending = _PendingExchange(
            expected_type=expected_type,
            expected_id=expected_id,
            post_response_listen_seconds=max(0.0, post_response_listen_seconds),
        )
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._closed:
                raise OSError("response dispatcher is closed")
            if self._failure is not None:
                raise OSError(str(self._failure)) from self._failure
            self._pending.append(pending)

        try:
            self.transport.send_frame(request)
            self.start()
            with self._condition:
                while True:
                    if self._failure is not None:
                        raise OSError(str(self._failure)) from self._failure
                    if self._closed:
                        raise OSError("response dispatcher is closed")
                    now = time.monotonic()
                    if pending.matched:
                        post_deadline = pending.post_response_deadline
                        if post_deadline is None or now >= post_deadline:
                            return list(pending.frames)
                        wait_until = post_deadline
                    else:
                        if now >= deadline:
                            return list(pending.frames)
                        wait_until = deadline
                    self._condition.wait(timeout=max(0.0, wait_until - now))
        finally:
            stop_idle_reader = False
            with self._condition:
                if pending in self._pending:
                    self._pending.remove(pending)
                stop_idle_reader = (
                    not self._pending
                    and not self._event_callbacks
                    and not self._failure_callbacks
                )
                reader = self._reader_thread
                self._condition.notify_all()
            if (
                stop_idle_reader
                and reader is not None
                and reader is not threading.current_thread()
            ):
                reader.join(timeout=1.0)

    def close(self) -> None:
        """Stop the reader, release pending requests, and close the transport."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            reader = self._reader_thread
            self._condition.notify_all()
        try:
            self.transport.close()
        finally:
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=2.0)
                if reader.is_alive():
                    _LOGGER.warning(
                        "Nice response reader did not stop after transport close"
                    )
