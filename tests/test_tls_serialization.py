"""TLS state is accessed by one thread at a time without starving commands."""

import socket
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from custom_components.nice_bidiwifi.protocol.nhk.codec import frame_xml
from custom_components.nice_bidiwifi.transport.dispatcher import ResponseDispatcher
from custom_components.nice_bidiwifi.transport.lan import (
    SocketFrameTransport,
    make_local_tls_context,
)


class CheckedSocket:
    """Detect overlapping I/O or timeout changes during another operation."""

    def __init__(self, *, hold_read=False, drip=False):
        self.timeout = 2.0
        self.guard = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.overlap = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.sent = threading.Event()
        self.hold_read = hold_read
        self.drip = drip
        self.stopped = False
        self.closed = False
        self.events = []
        self.write_timeouts = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        with self.guard:
            self.overlap |= self.active != 0
            self.timeout = timeout

    @contextmanager
    def operation(self, name):
        with self.guard:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.overlap |= self.active > 1
            self.events.append(name)
        try:
            yield
        finally:
            with self.guard:
                self.active -= 1

    def recv(self, size):
        with self.operation("read"):
            self.entered.set()
            self.release.wait(None if self.hold_read else min(self.timeout, 0.005))
            if self.stopped:
                return b""
            if self.drip:
                time.sleep(0.002)
                return b"\x02x"
            raise TimeoutError

    def sendall(self, data):
        with self.operation("write"):
            self.write_timeouts.append(self.timeout)
            self.sent.set()
            if self.stopped:
                raise BrokenPipeError

    def shutdown(self, how):
        self.stopped = True
        self.release.set()

    def close(self):
        self.overlap |= self.active != 0
        self.closed = True


def wait_for_writer(transport):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with transport._io_condition:
            if transport._waiting_writers:
                return
        time.sleep(0.001)
    raise AssertionError("writer was not queued")


def test_writer_waits_for_read_and_takes_priority_over_next_read():
    sock = CheckedSocket(hold_read=True, drip=True)
    transport = SocketFrameTransport(sock)
    reader = threading.Thread(target=lambda: transport.read_frame(0.2))
    writer = threading.Thread(target=lambda: transport.send_frame(b"command"))
    try:
        reader.start()
        assert sock.entered.wait(2)
        writer.start()
        wait_for_writer(transport)
        assert not sock.sent.is_set()
        sock.release.set()
        assert sock.sent.wait(2)
    finally:
        sock.release.set()
        reader.join(2)
        if writer.ident is not None:
            writer.join(2)
        transport.close()
    assert not reader.is_alive() and not writer.is_alive()
    assert sock.maximum == 1 and not sock.overlap
    assert sock.events[:2] == ["read", "write"]


def test_read_polling_does_not_shorten_write_timeout():
    sock = CheckedSocket()
    transport = SocketFrameTransport(sock)
    assert transport.read_frame(0.25) == b""
    transport.send_frame(b"command")
    assert 1.0 < sock.write_timeouts[0] <= 2.0
    assert not sock.overlap
    transport.close()


@pytest.mark.parametrize("writer", [False, True])
def test_deadline_expiring_after_acquisition_does_not_start_socket_io(monkeypatch, writer):
    sock = CheckedSocket()
    transport = SocketFrameTransport(sock)
    transport._write_timeout = 0.01
    acquire = transport._acquire_io

    def delayed_acquisition(**kwargs):
        acquired = acquire(**kwargs)
        time.sleep(0.03)
        return acquired

    monkeypatch.setattr(transport, "_acquire_io", delayed_acquisition)
    if writer:
        with pytest.raises(TimeoutError, match="deadline expired"):
            transport.send_frame(b"expired command")
        assert not sock.sent.is_set()
    else:
        assert transport.read_frame(0.01) == b""
        assert not sock.entered.is_set()
    assert not transport._io_busy and transport._waiting_writers == 0
    transport.close()


def test_timed_out_waiting_writer_never_sends_later_and_releases_priority():
    sock = CheckedSocket(hold_read=True)
    transport = SocketFrameTransport(sock)
    transport._write_timeout = 0.02
    reader = threading.Thread(target=lambda: transport.read_frame(0.5))
    try:
        reader.start()
        assert sock.entered.wait(2)
        with pytest.raises(TimeoutError, match="waiting to send"):
            transport.send_frame(b"expired command")
        assert not sock.sent.is_set()
        assert transport._waiting_writers == 0
    finally:
        sock.release.set()
        reader.join(3)
    assert not sock.sent.is_set()
    transport._write_timeout = 2
    transport.send_frame(b"next command")
    assert sock.sent.is_set() and not sock.overlap
    transport.close()


def test_continuously_fragmented_frame_has_a_total_deadline_and_keeps_buffer():
    sock = CheckedSocket(drip=True)
    transport = SocketFrameTransport(sock)
    started = time.monotonic()
    assert transport.read_frame(0.08) == b""
    assert time.monotonic() - started < 0.5
    assert transport._buffer
    sock.recv = lambda size: b"done\x03"
    assert transport.read_frame(0.1).endswith(b"done\x03")
    transport.close()


def test_many_writers_do_not_overlap_a_single_reader():
    sock = CheckedSocket()
    transport = SocketFrameTransport(sock)
    done = threading.Event()
    errors = []

    def read():
        while not done.is_set():
            transport.read_frame(0.1)

    def write():
        try:
            for _ in range(100):
                transport.send_frame(b"command")
        except Exception as err:
            errors.append(err)

    reader = threading.Thread(target=read)
    writers = [threading.Thread(target=write) for _ in range(4)]
    try:
        reader.start()
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join(3)
        assert not any(writer.is_alive() for writer in writers)
    finally:
        done.set()
        sock.release.set()
        reader.join(2)
        for writer in writers:
            writer.join(2)
        transport.close()
    assert not errors and not sock.overlap and sock.maximum == 1
    assert len(sock.write_timeouts) == 400


def test_shutdown_retires_a_writer_queued_behind_the_reader():
    sock = CheckedSocket(hold_read=True)
    transport = SocketFrameTransport(sock)
    dispatcher = ResponseDispatcher(transport)
    dispatcher.add_event_callback(lambda frame: None)
    errors = []

    def exchange():
        try:
            dispatcher.exchange(frame_xml('<Request type="INFO" id="1"/>'), expected_type="INFO", expected_id=1, timeout=2)
        except OSError as err:
            errors.append(err)

    sender = threading.Thread(target=exchange)
    dispatcher.start()
    reader = dispatcher._reader_thread
    try:
        assert sock.entered.wait(2)
        sender.start()
        wait_for_writer(transport)
        dispatcher.close()
    finally:
        sock.release.set()
        sender.join(3)
        reader.join(3)
        dispatcher.close()
    assert not sender.is_alive() and not reader.is_alive()
    assert errors and sock.closed and not sock.overlap
    assert dispatcher._active_sends == 0
    assert not transport._io_busy


class CheckedTLSSocket(CheckedSocket):
    """Instrument real SSL calls without serializing them inside the test."""

    def __init__(self, real):
        super().__init__()
        self.real = real

    def gettimeout(self):
        return self.real.gettimeout()

    def settimeout(self, timeout):
        super().settimeout(timeout)
        self.real.settimeout(timeout)

    def recv(self, size):
        with self.operation("read"):
            return self.real.recv(size)

    def sendall(self, data):
        with self.operation("write"):
            self.real.sendall(data)

    def shutdown(self, how):
        socket.socket.shutdown(self.real, how)

    def close(self):
        super().close()
        self.real.close()


def test_real_tls_bidirectional_requests_and_events_are_serialized(tmp_path):
    """One live TLS session handles concurrent callers and pushed events safely."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    local, peer = socket.socketpair()
    local.settimeout(5)
    peer.settimeout(5)
    client_tls = make_local_tls_context().wrap_socket(local, server_hostname=None, do_handshake_on_connect=False)
    server_tls = server_context.wrap_socket(peer, server_side=True, do_handshake_on_connect=False)
    stop = threading.Event()
    ready = threading.Event()
    server_errors, caller_errors = [], []
    replies = []

    def server():
        buffer = bytearray()
        try:
            server_tls.do_handshake()
            ready.set()
            server_tls.settimeout(0.05)
            while not stop.is_set():
                try:
                    data = server_tls.recv(65535)
                except TimeoutError:
                    data = None
                if data == b"":
                    return
                if data:
                    buffer.extend(data)
                    while b"\x03" in buffer:
                        end = buffer.index(3)
                        request = ET.fromstring(bytes(buffer[1:end]).decode())
                        del buffer[:end + 1]
                        server_tls.sendall(frame_xml(f'<Response type="INFO" id="{request.attrib["id"]}"/>'))
                server_tls.sendall(frame_xml('<Event type="CHANGE"><DoorStatus>open</DoorStatus></Event>'))
        except OSError as err:
            if not stop.is_set():
                server_errors.append(err)
        finally:
            server_tls.close()

    server_thread = threading.Thread(target=server)
    wrapped = CheckedTLSSocket(client_tls)
    dispatcher = ResponseDispatcher(SocketFrameTransport(wrapped))
    events = []
    dispatcher.add_event_callback(events.append)
    callers = []

    def call(offset):
        try:
            for number in range(offset, offset + 10):
                frames = dispatcher.exchange(frame_xml(f'<Request type="INFO" id="{number}"/>'),
                                             expected_type="INFO", expected_id=number, timeout=5)
                assert any(f'id="{number}"'.encode() in f for f in frames)
                replies.append(number)
        except Exception as err:
            caller_errors.append(err)

    try:
        server_thread.start()
        client_tls.do_handshake()
        assert ready.wait(5)
        callers = [threading.Thread(target=call, args=(offset,)) for offset in (0, 10, 20, 30)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(6)
        assert not any(caller.is_alive() for caller in callers)
        assert not caller_errors and not server_errors
        assert sorted(replies) == list(range(40))
        assert events
    finally:
        stop.set()
        dispatcher.close()
        for caller in callers:
            caller.join(3)
        with suppress(OSError):
            socket.socket.shutdown(server_tls, socket.SHUT_RDWR)
        server_thread.join(6)
    assert not server_thread.is_alive()
    assert wrapped.maximum == 1 and not wrapped.overlap and wrapped.closed
