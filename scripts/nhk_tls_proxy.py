#!/usr/bin/env python3
"""Capture MyNice Pro through two TLS connections to a real Nice interface.

This forwards real commands. PAIR is always blocked. Raw XML is private and
opt-in; the default JSONL summary excludes NHK identities and authentication.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts._standalone_client import load_client_module  # noqa: E402

load_client_module(REPO_ROOT)

from custom_components.nice_bidiwifi.protocol.nhk.codec import (  # noqa: E402
    frame_xml,
    xml_payload,
)
from custom_components.nice_bidiwifi.protocol.t4.codec import (  # noqa: E402
    decrypt_t4_payloads_from_frame,
)

MAX_FRAME_BYTES = 1024 * 1024


class FrameReader:
    """Keep split and coalesced NHK frames intact, including across timeouts."""

    def __init__(self, stream: ssl.SSLSocket) -> None:
        self.stream = stream
        self.buffer = bytearray()

    def read(self) -> bytes:
        """Return one complete frame, or EOF; reject incomplete frames."""
        while True:
            end = self.buffer.find(b"\x03")
            if end >= 0:
                if end + 1 > MAX_FRAME_BYTES:
                    raise ValueError("NHK frame too large")
                frame = bytes(self.buffer[:end + 1])
                del self.buffer[:end + 1]
                if not frame.startswith(b"\x02"):
                    raise ValueError("Missing NHK frame start")
                return frame
            if len(self.buffer) > MAX_FRAME_BYTES:
                raise ValueError("NHK frame too large")
            chunk = self.stream.recv(65536)
            if not chunk:
                if self.buffer:
                    raise ValueError("Incomplete NHK frame")
                return b""
            self.buffer.extend(chunk)


def parse_request(frame: bytes) -> ET.Element:
    """Require one well-formed request before applying the block policy."""
    root = ET.fromstring(xml_payload(frame))
    if root.tag != "Request" or not root.get("type"):
        raise ValueError("Expected an NHK Request with a type")
    return root


def blocked_response(root: ET.Element) -> bytes:
    """Return a local error without forwarding a blocked request."""
    attrs = {
        "id": root.get("id", "0"),
        "source": root.get("target", ""),
        "target": root.get("source", ""),
        "type": root.get("type", ""),
        "protocolType": root.get("protocolType", "NHK"),
        "protocolVersion": root.get("protocolVersion", "1.0"),
    }
    response = ET.Element("Response", attrs)
    error = ET.SubElement(response, "Error")
    ET.SubElement(error, "Code").text = "1"
    ET.SubElement(error, "Info").text = "Blocked by local discovery proxy"
    return frame_xml(ET.tostring(response, encoding="unicode"))


def frame_summary(frame: bytes, direction: str, session: int) -> dict[str, object]:
    """Export only structural metadata and outgoing DEP frames for review."""
    summary: dict[str, object] = {
        "time_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "session": session,
        "direction": direction,
        "bytes": len(frame),
    }
    try:
        root = ET.fromstring(xml_payload(frame))
    except ET.ParseError:
        summary["parse_error"] = True
        return summary
    for key, value in (("type", root.get("type", "")), ("protocol", root.findtext(".//Protocol", "").strip())):
        # Unknown strings may contain private data, so export known vocabulary only.
        known = {
            "VERIFY", "CONNECT", "INFO", "PAIR", "PAIRINGS", "CHANGE", "STATUS",
            "T4_CONFIG", "T4_STATUS", "T4_REQUEST", "T4_EVENT", "EVENT", "GET",
            "DEP", "DMP",
        }
        if value:
            summary[key] = value if value in known else "other"
    if direction == "app_to_bidi" and summary.get("protocol") == "DEP":
        try:
            summary["dep_frames_hex"] = [
                payload.hex(" ") for payload in decrypt_t4_payloads_from_frame(frame)
            ]
        except (ValueError, TypeError):
            summary["dep_decode_error"] = True
    return summary


def open_private_file(path: Path) -> TextIO:
    """Create a fresh owner-only capture without overwriting earlier evidence."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


class Capture:
    """Serialize records from both directions without logging raw XML by default."""

    def __init__(self, summary: TextIO, raw: TextIO | None, label: str) -> None:
        self.summary = summary
        self.raw = raw
        self.label = label
        self.lock = threading.Lock()

    def record(self, frame: bytes, direction: str, session: int, *, blocked: bool = False) -> None:
        record = frame_summary(frame, direction, session)
        record.update(label=self.label, blocked=blocked)
        with self.lock:
            self.summary.write(json.dumps(record) + "\n")
            self.summary.flush()
            if self.raw is not None:
                self.raw.write(f"\n== {record['time_utc']} session={session} {direction} blocked={blocked} ==\n")
                self.raw.write(xml_payload(frame) + "\n")
                self.raw.flush()


def relay_session(
    downstream: ssl.SSLSocket,
    upstream: ssl.SSLSocket,
    capture: Capture,
    session: int,
    blocked_types: set[str],
    idle_timeout: float,
) -> None:
    """Forward each direction independently so unsolicited events are preserved."""
    stop = threading.Event()
    downstream_write = threading.Lock()
    activity = [time.monotonic()]

    def pump(source: ssl.SSLSocket, target: ssl.SSLSocket, direction: str) -> None:
        reader = FrameReader(source)
        try:
            while not stop.is_set():
                try:
                    frame = reader.read()
                except TimeoutError:
                    if time.monotonic() - activity[0] >= idle_timeout:
                        break
                    continue
                if not frame:
                    break
                activity[0] = time.monotonic()
                if direction == "app_to_bidi":
                    root = parse_request(frame)
                    blocked = root.get("type", "").upper() in blocked_types
                    capture.record(frame, direction, session, blocked=blocked)
                    if blocked:
                        response = blocked_response(root)
                        capture.record(response, "proxy_to_app", session, blocked=True)
                        with downstream_write:
                            downstream.sendall(response)
                    else:
                        target.sendall(frame)
                else:
                    capture.record(frame, direction, session)
                    with downstream_write:
                        target.sendall(frame)
        except (OSError, ValueError, ET.ParseError):
            print("Session ended: connection or frame error. Raw details stay local.")
        finally:
            stop.set()

    threads = [
        threading.Thread(target=pump, args=(downstream, upstream, "app_to_bidi")),
        threading.Thread(target=pump, args=(upstream, downstream, "bidi_to_app")),
    ]
    try:
        for thread in threads:
            thread.start()
        while not stop.wait(0.2):
            pass
    finally:
        stop.set()
        for stream in (downstream, upstream):
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in threads:
            thread.join()


def parse_args() -> argparse.Namespace:
    """Require explicit addresses and acknowledgement before forwarding commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", required=True, help="Mac address reachable by the phone")
    parser.add_argument("--listen-port", type=int, default=443)
    parser.add_argument("--upstream-host", required=True, help="Real BiDi address")
    parser.add_argument("--upstream-port", type=int, default=443)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True, help="New JSONL report; existing files are refused")
    parser.add_argument("--raw-log", type=Path, help="Optional private XML log; never upload it")
    parser.add_argument("--label", required=True, help="Generic action label, without device names")
    parser.add_argument("--timeout", type=float, default=15, help="Connection/handshake timeout in seconds")
    parser.add_argument("--idle-timeout", type=float, default=60)
    parser.add_argument("--block-types", default="", help="Additional comma-separated request types to block")
    parser.add_argument("--acknowledge-gate-control", action="store_true", required=True,
                        help="Required: forwarded app commands can move the real gate or change its settings")
    args = parser.parse_args()
    if args.timeout <= 0 or args.idle_timeout <= 0:
        parser.error("Timeouts must be positive")
    if not re.fullmatch(r"[a-z0-9_-]{1,48}", args.label):
        parser.error("Use a short generic label such as baseline or partial-open")
    if (args.listen_host, args.listen_port) == (args.upstream_host, args.upstream_port):
        parser.error("The proxy and real BiDi must use different endpoints")
    return args


def serve(args: argparse.Namespace) -> None:
    """Serve one app session at a time and close both sockets on exit."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    upstream_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    upstream_context.check_hostname = False
    upstream_context.verify_mode = ssl.CERT_NONE
    upstream_context.minimum_version = ssl.TLSVersion.TLSv1_2
    upstream_context.maximum_version = ssl.TLSVersion.TLSv1_2
    blocked = {"PAIR"} | {item.strip().upper() for item in args.block_types.split(",") if item.strip()}
    with open_private_file(args.summary) as summary:
        raw = open_private_file(args.raw_log) if args.raw_log else None
        try:
            capture = Capture(summary, raw, args.label)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((args.listen_host, args.listen_port))
                listener.listen(5)
                print("Proxy ready. PAIR is blocked; other app commands are forwarded to the real gate.")
                print("Close the app and press Ctrl-C when finished. Keep raw logs and the TLS key private.")
                session = 0
                while True:
                    raw_client, _ = listener.accept()
                    session += 1
                    try:
                        with raw_client:
                            raw_client.settimeout(args.timeout)
                            with context.wrap_socket(raw_client, server_side=True) as downstream:
                                with socket.create_connection((args.upstream_host, args.upstream_port), args.timeout) as raw_upstream:
                                    with upstream_context.wrap_socket(raw_upstream, server_hostname=None) as upstream:
                                        print(f"Session {session}: app and BiDi TLS established.")
                                        downstream.settimeout(1)
                                        upstream.settimeout(1)
                                        relay_session(downstream, upstream, capture, session, blocked, args.idle_timeout)
                    except (OSError, ValueError):
                        print(f"Session {session}: connection failed. Check the selected proxy and real BiDi address.")
        finally:
            if raw is not None:
                raw.close()


if __name__ == "__main__":
    try:
        serve(parse_args())
    except KeyboardInterrupt:
        print("\nProxy stopped; connections closed.")
