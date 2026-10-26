"""Tests for controlled proxy forwarding, policy, and shareable reports."""

from __future__ import annotations

import base64
import json
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from custom_components.nice_bidiwifi.protocol.nhk.codec import frame_xml
from custom_components.nice_bidiwifi.protocol.t4.codec import xor_sha256
from scripts.nhk_tls_proxy import FrameReader, frame_summary, open_private_file

ROOT = Path(__file__).resolve().parents[1]


class Chunks:
    """Return deterministic fragments and timeouts without a network."""

    def __init__(self, *chunks: bytes | Exception) -> None:
        self.chunks = iter(chunks)

    def recv(self, _size: int) -> bytes:
        chunk = next(self.chunks, b"")
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


def test_frame_reader_retains_fragments_and_coalesced_frames() -> None:
    first = frame_xml('<Request type="INFO"/>')
    second = frame_xml('<Request type="PAIR"/>')
    reader = FrameReader(Chunks(first[:8], TimeoutError(), first[8:] + second))
    with pytest.raises(TimeoutError):
        reader.read()
    assert reader.read() == first
    assert reader.read() == second
    assert reader.read() == b""


def test_frame_reader_rejects_truncated_request() -> None:
    reader = FrameReader(Chunks(b'\x02<Request type="PAIR"'))
    with pytest.raises(ValueError, match="Incomplete"):
        reader.read()


def test_summary_decodes_dep_without_exporting_authentication_or_identifiers() -> None:
    payload = bytes.fromhex("55 0c 00 03 50 91 01 05 c6 01 82 05 64 e2 0c")
    key = b"synthetic-test-key"
    encoded_key = base64.b64encode(key).decode()
    body = base64.b64encode(xor_sha256(payload, key)).decode()
    request = frame_xml(
        '<Request type="T4_REQUEST" source="private-user" target="private-mac">'
        '<Interface><Protocol>DEP</Protocol>'
        f'<T4 key="{encoded_key}">{body}</T4></Interface>'
        '<Sign>private-signature</Sign></Request>'
    )
    summary = frame_summary(request, "app_to_bidi", 1)
    assert summary["dep_frames_hex"] == [payload.hex(" ")]
    assert summary["protocol"] == "DEP"
    exported = json.dumps(summary)
    for secret in ("private-user", "private-mac", "private-signature", encoded_key, body):
        assert secret not in exported
    assert "dep_frames_hex" not in frame_summary(request, "bidi_to_app", 1)
    unknown = frame_xml('<Request type="private-identifier"><Protocol>private-value</Protocol></Request>')
    assert "private" not in json.dumps(frame_summary(unknown, "app_to_bidi", 1))


def test_capture_file_is_private_and_does_not_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "capture.jsonl"
    with open_private_file(target) as output:
        output.write("first capture")
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        open_private_file(target)
    assert target.read_text() == "first capture"


def test_proxy_requires_explicit_gate_control_acknowledgement() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nhk_tls_proxy.py", "--listen-host", "127.0.0.1",
         "--upstream-host", "127.0.0.2", "--cert", "unused", "--key", "unused",
         "--summary", "unused", "--label", "baseline"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "--acknowledge-gate-control" in result.stderr


def test_real_tls_proxy_blocks_pair_and_relays_requests_responses_and_events(tmp_path: Path, socket_enabled: None) -> None:
    """Use two local TLS sessions; no connection to a real gate is made."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=proxy-test"],
        check=True, capture_output=True,
    )
    info = frame_xml('<Request type="INFO" id="2" source="private-source" target="private-target"/>')
    pair = frame_xml('<Request type="PAIR" id="1" source="private-source" target="private-target"/>')
    response = frame_xml('<Response type="INFO" id="2"><Name>private-gate</Name></Response>')
    event = frame_xml('<Event type="T4_EVENT"/>')
    upstream_received: list[bytes] = []
    failures: list[Exception] = []
    release_server = threading.Event()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(10)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            proxy_port = reservation.getsockname()[1]

        def real_device() -> None:
            try:
                connection, _ = server.accept()
                with connection, context.wrap_socket(connection, server_side=True) as tls:
                    tls.settimeout(5)
                    tls.sendall(event)  # Unsolicited event before the request response.
                    upstream_received.append(FrameReader(tls).read())
                    tls.sendall(response)
                    release_server.wait(5)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=real_device)
        thread.start()
        summary_path = tmp_path / "summary.jsonl"
        process = subprocess.Popen(
            [sys.executable, "-u", "scripts/nhk_tls_proxy.py",
             "--listen-host", "127.0.0.1", "--listen-port", str(proxy_port),
             "--upstream-host", "127.0.0.1", "--upstream-port", str(server.getsockname()[1]),
             "--cert", str(cert), "--key", str(key), "--summary", str(summary_path),
             "--label", "baseline", "--acknowledge-gate-control"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    client = socket.create_connection(("127.0.0.1", proxy_port), timeout=1)
                    break
                except ConnectionRefusedError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("Proxy did not open its local test listener")
                    time.sleep(0.05)
            client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_context.check_hostname = False
            client_context.verify_mode = ssl.CERT_NONE
            with client, client_context.wrap_socket(client, server_hostname=None) as tls:
                tls.settimeout(5)
                # PAIR and INFO must be checked separately even in one TCP write.
                tls.sendall(pair + info)
                reader = FrameReader(tls)
                replies = [reader.read() for _ in range(3)]
                assert event in replies
                assert response in replies
                assert any(b'PAIR' in reply and b'<Error>' in reply for reply in replies)
        finally:
            release_server.set()
            process.send_signal(signal.SIGINT)
            try:
                output, _ = process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                pytest.fail(f"Proxy did not close connections on Ctrl-C: {output}")
            thread.join(timeout=10)
        assert process.returncode == 0, output
        assert not failures
        assert upstream_received == [info]
        summary_text = summary_path.read_text()
        assert all(secret not in summary_text for secret in ("private-source", "private-target", "private-gate"))
        records = [json.loads(line) for line in summary_text.splitlines()]
        assert any(record.get("type") == "PAIR" and record["blocked"] for record in records)
        assert any(record.get("type") == "T4_EVENT" for record in records)
