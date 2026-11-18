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
_READER_STOP_TIMEOUT_SECONDS = 2.0
_INTERRUPT_CHECK_SECONDS = 0.05
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
        # Threads that may still use the socket: the reader and in-flight
        # send_frame() calls. The descriptor is released only after all left.
        self._active_sends = 0
        self._close_deferred = False
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
        """Run the reader and release the transport if close() deferred it."""
        try:
            self._read_frames()
        finally:
            with self._condition:
                if self._reader_thread is threading.current_thread():
                    self._reader_thread = None
                close_transport = self._take_deferred_close_locked()
                self._condition.notify_all()
            if close_transport:
                self._close_transport()

    def _take_deferred_close_locked(self) -> bool:
        """Claim a deferred close once no thread can still use the socket."""
        if (
            self._close_deferred
            and self._reader_thread is None
            and self._active_sends == 0
        ):
            self._close_deferred = False
            return True
        return False

    def _send(self, request: bytes) -> None:
        """Send while counted as a socket user (counter taken by the caller)."""
        try:
            self.transport.send_frame(request)
        finally:
            with self._condition:
                self._active_sends -= 1
                close_transport = self._take_deferred_close_locked()
                self._condition.notify_all()
            if close_transport:
                self._close_transport()

    def _read_frames(self) -> None:
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
        interrupt: Callable[[], bool] | None = None,
    ) -> list[bytes]:
        """Send a request and collect its correlated response plus adjacent events.

        ``interrupt`` ends the post-response listening window early. Frames
        arriving afterwards are still published as unsolicited events.
        """
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
            self._active_sends += 1

        try:
            self._send(request)
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
                        if interrupt is not None and interrupt():
                            return list(pending.frames)
                        wait_until = post_deadline
                        if interrupt is not None:
                            wait_until = min(
                                wait_until, now + _INTERRUPT_CHECK_SECONDS
                            )
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
        """Stop the reader, release pending requests, and close the transport.

        The socket is never closed while another thread may still use it: a
        closed descriptor number is reused by the next ``open()`` in the
        process, and a stale reader (or sender) would then read from and
        write TLS records into that unrelated file. Wake the users with
        ``shutdown()``, wait for them, and only then close. If they do not
        stop in time, the last one closes the transport when it finishes.
        """
        current = threading.current_thread()
        with self._condition:
            if self._closed:
                return
            self._closed = True
            reader = self._reader_thread
            self._condition.notify_all()
            immediate = (
                reader is None or reader is current
            ) and self._active_sends == 0
        if immediate:
            # No other user: no reader, or close() runs from a reader callback
            # (the reader is then outside recv), and no send in flight.
            self._close_transport()
            return
        shutdown = getattr(self.transport, "shutdown", None)
        if shutdown is not None:
            try:
                shutdown()
            except OSError:
                _LOGGER.debug("Nice transport shutdown failed", exc_info=True)
        deadline = time.monotonic() + _READER_STOP_TIMEOUT_SECONDS
        if reader is not None and reader is not current:
            reader.join(timeout=_READER_STOP_TIMEOUT_SECONDS)
        with self._condition:
            while self._active_sends:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            self._close_deferred = True
            close_transport = self._take_deferred_close_locked()
            still_used = not close_transport and (
                self._active_sends or self._reader_thread not in (None, current)
            )
        if close_transport:
            self._close_transport()
        elif still_used:
            _LOGGER.warning(
                "Nice transport still in use after shutdown; "
                "the connection is closed when the last user finishes"
            )

    def _close_transport(self) -> None:
        try:
            self.transport.close()
        except OSError:
            _LOGGER.debug("Nice transport close failed", exc_info=True)
