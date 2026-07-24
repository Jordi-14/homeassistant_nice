"""Tests for framed transport and sole-reader dispatch."""

from __future__ import annotations

from custom_components.nice_bidiwifi.protocol.nhk.codec import frame_xml
from custom_components.nice_bidiwifi.transport.dispatcher import (
    ResponseDispatcher,
)
from custom_components.nice_bidiwifi.transport.lan import SocketFrameTransport


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

    assert frames == [event, response]
    assert events == [event]
    assert socket.sent == [frame_xml('<Request type="STATUS" id="7"/>')]
    assert socket.recv_calls == 1


def test_transport_close_is_idempotent_and_discards_buffers() -> None:
    """Unload and reconnect can close a framed transport repeatedly."""
    socket = FakeSocket([frame_xml("<Response/>")])
    transport = SocketFrameTransport(socket)

    transport.close()
    transport.close()

    assert socket.closed is True
    assert transport.connected is False
    assert transport.read_frame(1.0) == b""
