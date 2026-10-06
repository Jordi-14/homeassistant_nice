"""Tests for framed transport and sole-reader dispatch."""

from __future__ import annotations

import ssl
import threading
import time
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
