"""Tests for framed transport and sole-reader dispatch."""

from __future__ import annotations

import contextlib
import socket
import ssl
import threading
import time
from collections.abc import Callable
from queue import Empty, Queue

import pytest

from custom_components.nice_bidiwifi.protocol.nhk.codec import frame_xml
from custom_components.nice_bidiwifi.transport.dispatcher import (
    ResponseDispatcher,
)
from custom_components.nice_bidiwifi.transport.lan import (
    LegacyLanTlsTransport,
    SocketFrameTransport,
    make_local_tls_context,
)
from custom_components.nice_bidiwifi.transport.relay import make_relay_tls_context


class FakeSocket:
    """Socket fake that exposes deterministic receive chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []
        self.timeouts: list[float | None] = []
        self.closed = False
        self.recv_calls = 0

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _size: int) -> bytes:
        self.recv_calls += 1
        if not self.chunks:
            raise TimeoutError
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class QueueSocket:
    """Blocking socket fake for response ordering and lifecycle tests."""

    def __init__(self) -> None:
        self.chunks: Queue[bytes] = Queue()
        self.sent: list[bytes] = []
        self.timeout = 1.0
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value or 1.0

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _size: int) -> bytes:
        try:
            return self.chunks.get(timeout=self.timeout)
        except Empty as err:
            raise TimeoutError from err

    def close(self) -> None:
        self.closed = True
        self.chunks.put(b"")

    def feed(self, data: bytes) -> None:
        self.chunks.put(data)


def test_socket_transport_handles_fragmented_and_combined_frames() -> None:
    """Fragmentation and multiple frames never escape the framing layer."""
    first = frame_xml('<Response type="INFO" id="1"/>')
    second = frame_xml('<Response type="STATUS" id="2"/>')
    socket = FakeSocket([first[:7], first[7:] + second])
    transport = SocketFrameTransport(socket)

    assert transport.read_frame(1.0) == first
    assert transport.read_frame(1.0) == second
    assert socket.recv_calls == 2


def test_dispatcher_is_the_only_reader_and_routes_unsolicited_frames() -> None:
    """One exchange correlates its response and publishes adjacent events."""
    event = frame_xml(
        '<Response type="CHANGE" id="99"><DoorStatus>open</DoorStatus></Response>'
    )
    response = frame_xml('<Response type="STATUS" id="7"/>')
    socket = FakeSocket([event + response])
    transport = SocketFrameTransport(socket)
    dispatcher = ResponseDispatcher(transport)
    events: list[bytes] = []
    dispatcher.add_event_callback(events.append)

    frames = dispatcher.exchange(
        frame_xml('<Request type="STATUS" id="7"/>'),
        expected_type="STATUS",
        expected_id=7,
        timeout=1.0,
    )
    dispatcher.close()

    assert frames == [event, response]
    assert events == [event]
    assert socket.sent == [frame_xml('<Request type="STATUS" id="7"/>')]
    assert socket.recv_calls >= 1


def test_interrupt_ends_post_response_window_and_keeps_late_frames_as_events() -> None:
    """A waiting command cuts the listening window without losing frames."""
    socket = QueueSocket()
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    events: list[bytes] = []
    dispatcher.add_event_callback(events.append)
    response = frame_xml('<Response type="STATUS" id="7"/>')
    late = frame_xml(
        '<Response type="CHANGE" id="99"><DoorStatus>open</DoorStatus></Response>'
    )
    command_waiting = threading.Event()
    socket.feed(response)

    def request_command() -> None:
        time.sleep(0.1)
        command_waiting.set()

    threading.Thread(target=request_command).start()
    started = time.monotonic()
    frames = dispatcher.exchange(
        frame_xml('<Request type="STATUS" id="7"/>'),
        expected_type="STATUS",
        expected_id=7,
        timeout=1.0,
        post_response_listen_seconds=5.0,
        interrupt=command_waiting.is_set,
    )
    elapsed = time.monotonic() - started
    socket.feed(late)
    deadline = time.monotonic() + 1.0
    while not events and time.monotonic() < deadline:
        time.sleep(0.01)
    dispatcher.close()

    assert frames == [response]
    assert elapsed < 1.0
    assert events == [late]


def test_transport_close_is_idempotent_and_discards_buffers() -> None:
    """Unload and reconnect can close a framed transport repeatedly."""
    socket = FakeSocket([frame_xml("<Response/>")])
    transport = SocketFrameTransport(socket)

    transport.close()
    transport.close()

    assert socket.closed is True
    assert transport.connected is False
    assert transport.read_frame(1.0) == b""


def test_dispatcher_correlates_concurrent_out_of_order_responses() -> None:
    """Concurrent requests receive only their response despite reverse arrival."""
    socket = QueueSocket()
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    results: dict[int, list[bytes]] = {}

    def exchange(request_id: int) -> None:
        results[request_id] = dispatcher.exchange(
            frame_xml(f'<Request type="STATUS" id="{request_id}"/>'),
            expected_type="STATUS",
            expected_id=request_id,
            timeout=2.0,
        )

    first = threading.Thread(target=exchange, args=(1,))
    second = threading.Thread(target=exchange, args=(2,))
    first.start()
    second.start()
    while len(socket.sent) < 2:
        threading.Event().wait(0.01)
    response_2 = frame_xml('<Response type="STATUS" id="2"/>')
    response_1 = frame_xml('<Response type="STATUS" id="1"/>')
    socket.feed(response_2 + response_1)
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    dispatcher.close()

    assert results == {1: [response_1], 2: [response_2]}
    assert not first.is_alive()
    assert not second.is_alive()


def test_dispatcher_routes_malformed_and_duplicate_frames_as_events() -> None:
    """Unmatched and duplicate framed payloads never satisfy another request."""
    socket = QueueSocket()
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    events: list[bytes] = []
    dispatcher.add_event_callback(events.append)
    response = frame_xml('<Response type="INFO" id="4"/>')
    malformed = frame_xml("<not-valid")

    worker = threading.Thread(
        target=lambda: dispatcher.exchange(
            frame_xml('<Request type="INFO" id="4"/>'),
            expected_type="INFO",
            expected_id=4,
            timeout=2.0,
        )
    )
    worker.start()
    while not socket.sent:
        threading.Event().wait(0.01)
    socket.feed(malformed + response + response)
    worker.join(timeout=2.0)
    for _ in range(100):
        if len(events) >= 2:
            break
        threading.Event().wait(0.01)
    dispatcher.close()

    assert events == [malformed, response]


def test_dispatcher_close_unblocks_pending_exchange_and_stops_reader() -> None:
    """Closing a session releases waiters and leaves no reader behind."""
    socket = QueueSocket()
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    errors: list[Exception] = []

    def exchange() -> None:
        try:
            dispatcher.exchange(
                frame_xml('<Request type="INFO" id="8"/>'),
                expected_type="INFO",
                expected_id=8,
                timeout=10.0,
            )
        except Exception as err:
            errors.append(err)

    worker = threading.Thread(target=exchange)
    worker.start()
    while not socket.sent:
        threading.Event().wait(0.01)
    dispatcher.close()
    worker.join(timeout=2.0)

    assert errors
    assert isinstance(errors[0], OSError)
    assert not dispatcher.running
    assert not worker.is_alive()


class ShutdownTrackingSocket:
    """Blocking socket fake that records whether close races a running recv."""

    def __init__(
        self, *, honour_shutdown: bool = True, block_send: bool = False
    ) -> None:
        self.honour_shutdown = honour_shutdown
        self.block_send = block_send
        self.wake = threading.Event()
        self.in_recv = threading.Event()
        self.in_send = threading.Event()
        self.calls: list[str] = []
        self.closed_during_recv = False
        self.closed_during_send = False
        self.closed = False
        self._recv_active = False
        self._send_active = False

    def settimeout(self, value: float | None) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        if not self.block_send:
            return
        self._send_active = True
        self.in_send.set()
        try:
            self.wake.wait(timeout=5.0)
            raise BrokenPipeError("synthetic shutdown")
        finally:
            self._send_active = False

    def recv(self, _size: int) -> bytes:
        self._recv_active = True
        self.in_recv.set()
        try:
            # A real recv() does not return when another thread closes the
            # descriptor; only shutdown() (or data/timeout) wakes it.
            self.wake.wait(timeout=5.0)
            return b""
        finally:
            self._recv_active = False

    def shutdown(self, _how: int) -> None:
        self.calls.append("shutdown")
        if self.honour_shutdown:
            self.wake.set()

    def close(self) -> None:
        self.calls.append("close")
        self.closed_during_recv |= self._recv_active
        self.closed_during_send |= self._send_active
        self.closed = True


@pytest.fixture
def release_blocked_io():
    """Wake and join blocked fake I/O threads even when an assertion failed."""
    cleanups: list[Callable[[], None]] = []
    yield cleanups
    for cleanup in reversed(cleanups):
        cleanup()


def _release(socket: ShutdownTrackingSocket, *threads: threading.Thread) -> Callable[[], None]:
    def cleanup() -> None:
        socket.wake.set()
        for thread in threads:
            # Class method: a test may replace the instance's join().
            threading.Thread.join(thread, timeout=2.0)

    return cleanup


def _start_blocked_reader(
    socket: ShutdownTrackingSocket,
) -> tuple[ResponseDispatcher, threading.Thread]:
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    dispatcher.add_event_callback(lambda _frame: None)
    dispatcher.start()
    assert socket.in_recv.wait(timeout=2.0)
    reader = dispatcher._reader_thread
    assert reader is not None
    return dispatcher, reader


def test_dispatcher_close_never_releases_socket_under_running_reader(
    release_blocked_io: list[Callable[[], None]],
) -> None:
    """The descriptor stays allocated until the reader left recv()."""
    socket = ShutdownTrackingSocket()
    dispatcher, reader = _start_blocked_reader(socket)
    release_blocked_io.append(_release(socket, reader))

    dispatcher.close()

    assert socket.calls == ["shutdown", "close"]
    assert socket.closed_during_recv is False
    assert not reader.is_alive()
    assert not dispatcher.running


def test_dispatcher_defers_close_to_a_reader_that_does_not_stop(
    monkeypatch: pytest.MonkeyPatch,
    release_blocked_io: list[Callable[[], None]],
) -> None:
    """A stuck reader closes the socket itself instead of racing close()."""
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.transport.dispatcher."
        "_READER_STOP_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    socket = ShutdownTrackingSocket(honour_shutdown=False)
    dispatcher, reader = _start_blocked_reader(socket)
    release_blocked_io.append(_release(socket, reader))

    dispatcher.close()

    assert socket.calls == ["shutdown"]
    assert socket.closed is False
    socket.wake.set()
    reader.join(timeout=2.0)
    assert not reader.is_alive()
    assert socket.calls == ["shutdown", "close"]
    assert socket.closed_during_recv is False


def test_dispatcher_close_from_reader_callback_closes_immediately() -> None:
    """A failure callback on the reader thread is outside recv() and may close."""
    socket = FakeSocket([b""])
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    closed = threading.Event()

    def on_failure(_error: Exception) -> None:
        dispatcher.close()
        closed.set()

    dispatcher.add_failure_callback(on_failure)
    dispatcher.start()

    assert closed.wait(timeout=2.0)
    assert socket.closed is True


def _start_blocked_send(
    socket: ShutdownTrackingSocket,
) -> tuple[ResponseDispatcher, threading.Thread, list[Exception]]:
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))
    errors: list[Exception] = []

    def exchange() -> None:
        try:
            dispatcher.exchange(
                frame_xml('<Request type="INFO" id="5"/>'),
                expected_type="INFO",
                expected_id=5,
                timeout=5.0,
            )
        except Exception as err:
            errors.append(err)

    sender = threading.Thread(target=exchange)
    sender.start()
    assert socket.in_send.wait(timeout=2.0)
    return dispatcher, sender, errors


def test_dispatcher_close_waits_for_a_send_in_flight(
    release_blocked_io: list[Callable[[], None]],
) -> None:
    """A concurrent sendall() keeps the descriptor allocated until it returns."""
    socket = ShutdownTrackingSocket(block_send=True)
    dispatcher, sender, errors = _start_blocked_send(socket)
    release_blocked_io.append(_release(socket, sender))

    dispatcher.close()
    sender.join(timeout=2.0)

    assert socket.calls == ["shutdown", "close"]
    assert socket.closed_during_send is False
    assert not sender.is_alive()
    assert errors and isinstance(errors[0], OSError)


def test_last_sender_closes_when_close_timed_out(
    monkeypatch: pytest.MonkeyPatch,
    release_blocked_io: list[Callable[[], None]],
) -> None:
    """A send that ignores shutdown closes the transport when it finishes."""
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.transport.dispatcher."
        "_READER_STOP_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    socket = ShutdownTrackingSocket(honour_shutdown=False, block_send=True)
    dispatcher, sender, _errors = _start_blocked_send(socket)
    release_blocked_io.append(_release(socket, sender))

    dispatcher.close()

    assert socket.closed is False
    socket.wake.set()
    sender.join(timeout=2.0)
    assert socket.calls == ["shutdown", "close"]
    assert socket.closed_during_send is False


class RecvProbe:
    """Real socket wrapper that records when recv() is entered and left."""

    def __init__(self, real: socket.socket) -> None:
        self.real = real
        self.entered = threading.Event()
        self.events: list[str] = []

    def settimeout(self, value: float | None) -> None:
        self.real.settimeout(value)

    def sendall(self, data: bytes) -> None:
        self.real.sendall(data)

    def recv(self, size: int) -> bytes:
        self.entered.set()
        try:
            return self.real.recv(size)
        finally:
            self.events.append("recv_exit")

    def shutdown(self, how: int) -> None:
        self.events.append("shutdown")
        self.real.shutdown(how)

    def close(self) -> None:
        self.events.append("close")
        self.real.close()


def test_shutdown_wakes_real_socket_reader_before_descriptor_is_released() -> None:
    """With real sockets, the descriptor is closed only after recv() returned."""
    local, peer = socket.socketpair()
    probe = RecvProbe(local)
    reader = None
    try:
        dispatcher = ResponseDispatcher(SocketFrameTransport(probe))
        dispatcher.add_event_callback(lambda _frame: None)
        # A long read timeout keeps the reader inside one blocking recv().
        monkey_timeout = 3.0
        dispatcher.transport.read_frame = (  # type: ignore[method-assign]
            lambda _t, read=dispatcher.transport.read_frame: read(monkey_timeout)
        )
        dispatcher.start()
        reader = dispatcher._reader_thread
        assert reader is not None
        assert probe.entered.wait(timeout=2.0)
        time.sleep(0.05)

        dispatcher.close()

        assert not reader.is_alive()
        assert probe.events[-3:] == ["shutdown", "recv_exit", "close"]
        assert local.fileno() == -1
    finally:
        if reader is not None and reader.is_alive():
            # Regression path: wake the reader (EOF from the peer also works
            # when the old code already closed the local socket).
            with contextlib.suppress(OSError):
                local.shutdown(socket.SHUT_RDWR)
            peer.close()
            reader.join(timeout=4.0)
        peer.close()
        local.close()


def test_failed_reader_start_does_not_block_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader thread that never started is forgotten, so close() still closes."""
    socket = FakeSocket([])
    dispatcher = ResponseDispatcher(SocketFrameTransport(socket))

    def fail_start(_self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError):
        dispatcher.start()
    monkeypatch.undo()

    dispatcher.close()

    assert dispatcher._reader_thread is None
    assert socket.closed is True


def test_unexpected_shutdown_error_still_hands_over_the_close(
    monkeypatch: pytest.MonkeyPatch,
    release_blocked_io: list[Callable[[], None]],
) -> None:
    """A failing shutdown hook must not leave the socket open after the reader."""
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.transport.dispatcher."
        "_READER_STOP_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    socket = ShutdownTrackingSocket(honour_shutdown=False)
    dispatcher, reader = _start_blocked_reader(socket)
    release_blocked_io.append(_release(socket, reader))
    hook_calls: list[str] = []

    def broken_shutdown() -> None:
        hook_calls.append("shutdown")
        raise RuntimeError("synthetic shutdown failure")

    dispatcher.transport.shutdown = broken_shutdown  # type: ignore[method-assign]

    dispatcher.close()  # the reader stays blocked in recv()

    assert hook_calls == ["shutdown"]
    assert socket.closed is False
    socket.wake.set()
    reader.join(timeout=2.0)
    assert not reader.is_alive()
    assert socket.calls.count("close") == 1
    assert socket.closed_during_recv is False


def test_join_error_after_reader_exit_still_closes_the_socket(
    release_blocked_io: list[Callable[[], None]],
) -> None:
    """A close claimed by close() itself is performed even if join() raised."""
    socket = ShutdownTrackingSocket()
    dispatcher, reader = _start_blocked_reader(socket)
    release_blocked_io.append(_release(socket, reader))
    real_join = reader.join

    def join_then_fail(timeout: float | None = None) -> None:
        real_join(timeout)
        raise RuntimeError("synthetic join failure")

    reader.join = join_then_fail  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="synthetic join failure"):
        dispatcher.close()

    assert not reader.is_alive()
    assert socket.calls == ["shutdown", "close"]
    assert socket.closed_during_recv is False


def test_interrupted_reader_start_never_runs_a_second_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread launched before start() was interrupted must not read."""
    dispatcher = ResponseDispatcher(SocketFrameTransport(FakeSocket([])))
    entered: list[threading.Thread] = []
    launched: list[threading.Thread] = []
    original_start = threading.Thread.start

    def record_read_frames(_self: ResponseDispatcher) -> None:
        entered.append(threading.current_thread())

    def start_and_record(self: threading.Thread) -> None:
        launched.append(self)
        original_start(self)

    def start_then_interrupt(self: threading.Thread) -> None:
        start_and_record(self)
        raise RuntimeError("synthetic interrupted start")

    monkeypatch.setattr(ResponseDispatcher, "_read_frames", record_read_frames)
    try:
        monkeypatch.setattr(threading.Thread, "start", start_then_interrupt)
        with pytest.raises(RuntimeError):
            dispatcher.start()
        monkeypatch.setattr(threading.Thread, "start", start_and_record)
        dispatcher.start()
    finally:
        monkeypatch.setattr(threading.Thread, "start", original_start)
        for thread in launched:
            thread.join(timeout=2.0)

    assert len(launched) == 2
    assert not any(thread.is_alive() for thread in launched)
    assert entered == [launched[1]]


def test_tls_socket_shutdown_keeps_ssl_object_attached() -> None:
    """Shutdown of an SSLSocket must not detach the SSL object under a reader."""
    local, peer = socket.socketpair()
    tls = make_local_tls_context().wrap_socket(
        local, server_hostname=None, do_handshake_on_connect=False
    )
    try:
        assert tls._sslobj is not None
        transport = SocketFrameTransport(tls)

        transport.shutdown()

        # SSLSocket.shutdown() would set _sslobj to None while a reader may
        # still be inside it; only the TCP side may be shut down.
        assert tls._sslobj is not None
        peer.settimeout(2.0)
        assert peer.recv(1) == b""
        assert tls.fileno() != -1
    finally:
        peer.close()
        tls.close()


def test_transport_distinguishes_peer_close_from_idle_timeout() -> None:
    """EOF is terminal while a socket timeout remains a harmless idle read."""
    timed_out = SocketFrameTransport(FakeSocket([]))
    assert timed_out.read_frame(0.01) == b""

    closed = SocketFrameTransport(FakeSocket([b""]))
    with pytest.raises(OSError, match="closed by peer"):
        closed.read_frame(0.01)


def test_relay_tls_context_uses_encryption_without_peer_verification() -> None:
    """Relay TLS must match the self-signed production service."""
    context = make_relay_tls_context()

    assert context.verify_mode is ssl.CERT_NONE
    assert context.check_hostname is False
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_legacy_local_tls_context_is_explicit_and_constrained() -> None:
    """Legacy options are absent by default and bounded when explicitly enabled."""
    standard = make_local_tls_context()
    legacy = make_local_tls_context(
        legacy_compatibility=True,
        cipher_security_level=1,
    )

    assert standard.minimum_version is ssl.TLSVersion.TLSv1_2
    assert standard.maximum_version is ssl.TLSVersion.TLSv1_2
    assert legacy.options & ssl.OP_LEGACY_SERVER_CONNECT
    assert legacy.security_level == 1


@pytest.mark.parametrize(
    "error_message",
    [
        "[SSL: DH_KEY_TOO_SMALL] dh key too small",
        "[SSL: WRONG_SIGNATURE_TYPE] wrong signature type",
    ],
)
def test_legacy_local_tls_relaxes_only_for_approved_security_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_message: str,
) -> None:
    """Security level 0 is retried only for approved OpenSSL reasons."""
    attempts: list[int | None] = []
    connected = object()

    def connect_once(
        cls,
        host,
        port,
        timeout,
        context,
        *,
        legacy_compatibility=False,
        cipher_security_level=None,
    ):
        attempts.append(cipher_security_level)
        if cipher_security_level == 1:
            raise ssl.SSLError(error_message)
        return connected

    monkeypatch.setattr(
        LegacyLanTlsTransport,
        "_connect_with_context",
        classmethod(connect_once),
    )

    assert LegacyLanTlsTransport.connect("192.0.2.10", 443, 10.0) is connected
    assert attempts == [1, 0]


def test_legacy_local_tls_does_not_relax_for_other_handshake_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated TLS failures retain security level 1 and propagate."""
    attempts: list[int | None] = []

    def connect_once(
        cls,
        host,
        port,
        timeout,
        context,
        *,
        legacy_compatibility=False,
        cipher_security_level=None,
    ):
        attempts.append(cipher_security_level)
        raise ssl.SSLError("certificate failure")

    monkeypatch.setattr(
        LegacyLanTlsTransport,
        "_connect_with_context",
        classmethod(connect_once),
    )

    with pytest.raises(ssl.SSLError, match="certificate failure"):
        LegacyLanTlsTransport.connect("192.0.2.10", 443, 10.0)
    assert attempts == [1]
