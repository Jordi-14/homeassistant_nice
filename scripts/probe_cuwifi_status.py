#!/usr/bin/env python3
"""Run a live read-only CU_WIFI status and position probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from custom_components.nice_bidiwifi.client import (  # noqa: E402
    STATUS_BY_BYTE,
    NiceBidiClient,
    NiceBidiCredentials,
    NiceBidiDeviceInfo,
    NiceBidiStatus,
    _attr,
    _frame,
    _printable,
    _response_summary,
    _xml_payload,
    build_dmp_read_frame,
    nice_bidi_error_code,
    parse_dmp_response,
    parse_info_xml,
)

INFO_CONTAINERS = {"Commands", "Events", "Properties", "Scheduled", "Services", "Settings"}
PRIORITY_READ_NAMES = {"DoorStatus", "Obstruct", "T4_allowed", "LastEvent", "Name", "Location"}
DEFAULT_NHK_REQUEST_TYPES = ("INFO", "READ")
LIVE_REQUEST_TYPES = ("STATUS", "T4_STATUS", "INFO")
EXHAUSTIVE_NHK_REQUEST_TYPES = ("INFO", "READ", "GET")

SENSITIVE_DATA_KEYS = {
    "device_serial",
    "host",
    "interface_serial",
    "password",
    "password_hex",
    "serial_number",
    "source_id",
    "target_mac",
    "username",
}

SENSITIVE_XML_ATTRS = {
    "mac",
    "serial",
    "serialnr",
    "source",
    "target",
    "username",
}

SENSITIVE_XML_TAGS = {
    "SerialNr",
    "Sign",
}

T4_MSG_TYPES = {
    0x01: "CMD/DEP",
    0x08: "INF/DMP",
}

T4_DEVICE_TYPES = {
    0x00: "all devices",
    0x04: "controller",
    0x0A: "OXI/radio",
}

T4_DMP_OPERATIONS = {
    0x18: "GET response continuation",
    0x19: "GET response",
    0x29: "SET response",
    0x89: "GET support request",
    0x99: "GET request",
    0xA9: "SET request",
}


def _t4_dmp_operation_kind(operation: int) -> str | None:
    if operation in {0x18, 0x19, 0x29}:
        return "response"
    if operation in {0x89, 0x99, 0xA9}:
        return "request"
    return None


T4_AUTO_STATUS = {
    0x01: "stopped",
    0x02: "opening",
    0x03: "closing",
    0x04: "opened",
    0x05: "closed",
    0x06: "pre-flashing/end-time",
    0x07: "pause",
    0x08: "searching devices",
    0x09: "searching positions",
    0x10: "partially opened",
    0x83: "opening",
    0x84: "closing",
}

T4_STOP_REASONS = {
    0x00: "normal",
    0x01: "obstacle by encoder",
    0x02: "obstacle by force",
    0x03: "photo intervention",
    0x04: "halt",
    0x05: "emergency",
    0x06: "electrical anomaly",
    0x07: "blocked",
    0x08: "timeout",
}

T4_MENUS = {
    0x01: "control",
    0x02: "current maneuver",
    0x04: "controller",
    0x80: "main settings",
    0x82: "run",
    0xC0: "status",
}

T4_CONTROL_COMMANDS = {
    0x01: "step-by-step",
    0x02: "stop",
    0x03: "open",
    0x04: "close",
    0x05: "partial open 1",
    0x06: "partial open 2",
    0x07: "partial open 3",
    0x0F: "lock",
    0x10: "unlock",
    0x11: "courtesy light timer",
    0x12: "courtesy light toggle",
}

KNOWN_DMP_LABELS = {
    (0x00, 0x00): "motor type",
    (0x00, 0x04): "WHO / discovery",
    (0x00, 0x08): "manufacturer",
    (0x00, 0x09): "product",
    (0x00, 0x0A): "hardware version",
    (0x00, 0x0B): "firmware version",
    (0x00, 0x0C): "description",
    (0x00, 0x10): "info support",
    (0x04, 0x00): "motor type",
    (0x04, 0x01): "gate state",
    (0x04, 0x11): "current encoder position",
    (0x04, 0x12): "max open encoder position",
    (0x04, 0x18): "position max / open endpoint",
    (0x04, 0x19): "position min / closed endpoint",
    (0x04, 0x21): "partial open 1 position",
    (0x04, 0x22): "partial open 2 position",
    (0x04, 0x23): "partial open 3 position",
    (0x04, 0x42): "opening speed",
    (0x04, 0x43): "closing speed",
    (0x04, 0x4A): "opening force",
    (0x04, 0x4B): "closing force",
    (0x04, 0x71): "input 1",
    (0x04, 0x72): "input 2",
    (0x04, 0x73): "input 3",
    (0x04, 0x74): "input 4",
    (0x04, 0x80): "auto close",
    (0x04, 0x81): "pause time",
    (0x04, 0x84): "photo close",
    (0x04, 0x85): "photo close time",
    (0x04, 0x86): "photo close mode",
    (0x04, 0x88): "always close",
    (0x04, 0x89): "always close time",
    (0x04, 0x8A): "always close mode",
    (0x04, 0x8C): "standby",
    (0x04, 0x94): "pre-flash",
    (0x04, 0x9C): "key lock",
    (0x04, 0xB1): "maintenance threshold",
    (0x04, 0xB2): "maintenance count",
    (0x04, 0xB3): "total maneuver count",
    (0x04, 0xD0): "diagnostics blackbox / stop reason",
    (0x04, 0xD1): "diagnostics I/O",
    (0x04, 0xD2): "diagnostics parameters",
    (0x04, 0xD4): "alternate movement counter",
    (0x0A, 0x04): "OXI/radio WHO",
    (0x0A, 0x09): "OXI/radio product",
    (0x0A, 0x0A): "OXI/radio hardware version",
    (0x0A, 0x0B): "OXI/radio firmware version",
    (0x0A, 0x0C): "OXI/radio description",
}

PRIMARY_DMP_TARGETS = (
    (0x00, 0x03),  # common controller address
    (0x00, 0x0A),  # OXI/radio address seen in community traces
)

FOCUSED_DMP_ENDPOINTS = tuple(dict.fromkeys([*range(0x00, 0x0B), 0x0A]))
FOCUSED_DMP_DADDRS = tuple(dict.fromkeys([*range(0x00, 0x0B), 0x0A]))


@dataclass(frozen=True)
class DmpRead:
    """One read-only DMP register probe."""

    daddr: int
    dendpoint: int
    group: int
    parameter: int
    label: str | None = None


class ProbeClient(NiceBidiClient):
    """Expose read-only lower-level requests for diagnostics."""

    def signed_probe(self, request_type: str, body: str = "") -> bytes:
        """Send a signed NHK request and accept any same-id response type."""

        def operation() -> bytes:
            xml, request_id = self._signed_request_with_id(request_type, body)
            return self._send_locked(xml, expected_type=None, expected_id=request_id)

        return self._run_with_reconnect(operation)

    def signed_probe_trace(
        self,
        request_type: str,
        body: str = "",
        *,
        wait_timeout: float | None = None,
        post_response_listen_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Send a signed NHK request and keep every frame seen around it."""

        def operation() -> dict[str, Any]:
            self._ensure_connected_locked()
            if not self._socket:
                raise RuntimeError("socket is not open")

            xml, request_id = self._signed_request_with_id(request_type, body)
            self._socket.settimeout(self.timeout)
            self._socket.sendall(_frame(xml))

            timeout = wait_timeout if wait_timeout is not None else self.timeout
            frames: list[bytes] = []
            expected_frame_index: int | None = None
            deadline = time.monotonic() + max(0.1, timeout)
            post_deadline: float | None = None

            while True:
                now = time.monotonic()
                active_deadline = post_deadline if post_deadline is not None else deadline
                if now >= active_deadline:
                    break

                response = self._recv_frame_locked(max(0.1, active_deadline - now))
                if not response:
                    break

                frames.append(response)
                if expected_frame_index is None and self._matches_response(response, None, request_id):
                    expected_frame_index = len(frames) - 1
                    if post_response_listen_seconds <= 0:
                        break
                    post_deadline = time.monotonic() + post_response_listen_seconds

            return {
                "request_id": request_id,
                "frames": frames,
                "expected_frame_index": expected_frame_index,
            }

        return self._run_with_reconnect(operation)

    def listen_frames(self, duration_s: float, poll_timeout_s: float) -> list[bytes]:
        """Listen on the current authenticated session without sending commands."""

        def operation() -> list[bytes]:
            self._ensure_connected_locked()
            frames: list[bytes] = []
            deadline = time.monotonic() + max(0.0, duration_s)
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                response = self._recv_frame_locked(max(0.1, min(poll_timeout_s, remaining)))
                if response:
                    frames.append(response)
            return frames

        return self._run_with_reconnect(operation)

    def decrypt_t4_payloads(self, response: bytes) -> list[bytes]:
        """Decrypt T4 payloads contained in a response or async event frame."""
        return self._decrypt_t4_payloads(response)

    def t4_probe(
        self,
        protocol: str,
        plain_payload: bytes,
        daddr: int,
        dendpoint: int,
        tout_ms: int,
    ) -> tuple[bytes, list[bytes]]:
        """Send one read-shaped T4 probe."""
        return self._run_with_reconnect(
            lambda: self._t4_request_locked(protocol, plain_payload, daddr, dendpoint, tout_ms)
        )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="CU_WIFI/BiDi-WiFi IP address or hostname")
    parser.add_argument("--port", type=int, default=443, help="NHK/TLS port")
    parser.add_argument(
        "--credentials",
        type=Path,
        help="JSON file produced by scripts/extract_mynice_credentials.py",
    )
    parser.add_argument("--username", help="NHK username")
    parser.add_argument("--password-hex", help="NHK password as 64 hex characters")
    parser.add_argument("--target-mac", help="CU_WIFI/BiDi-WiFi MAC address")
    parser.add_argument("--source-id", help="Source/controller ID")
    parser.add_argument("--device-id", type=int, default=1, help="NHK device id to inspect")
    parser.add_argument("--timeout", type=float, default=4.0, help="Socket timeout in seconds")
    parser.add_argument("--t4-timeout-ms", type=int, default=200, help="T4 read timeout")
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=60.0,
        help="Seconds to keep a live authenticated session open for async events and status polling",
    )
    parser.add_argument(
        "--manual-stop",
        "--listen-until-interrupted",
        dest="listen_until_interrupted",
        action="store_true",
        help="Keep the live capture running until Ctrl-C; the first interrupt ends live capture and continues the report",
    )
    parser.add_argument(
        "--listen-poll-timeout",
        type=float,
        default=1.0,
        help="Maximum seconds to wait for each passive receive attempt during live capture",
    )
    parser.add_argument(
        "--post-request-listen-seconds",
        type=float,
        default=0.75,
        help="Seconds to keep listening after each live signed response for trailing async events",
    )
    parser.add_argument(
        "--status-poll-interval",
        type=float,
        default=5.0,
        help="Seconds between signed STATUS polls during live capture; set 0 to disable",
    )
    parser.add_argument(
        "--t4-status-poll-interval",
        type=float,
        default=10.0,
        help="Seconds between signed T4_STATUS polls during live capture; set 0 to disable",
    )
    parser.add_argument(
        "--info-poll-interval",
        type=float,
        default=15.0,
        help="Seconds between signed INFO polls during live capture; set 0 to disable",
    )
    parser.add_argument(
        "--skip-live-capture",
        action="store_true",
        help="Skip the 60-second live session capture",
    )
    parser.add_argument(
        "--info-samples",
        type=int,
        default=1,
        help="Number of standalone INFO samples to collect outside the live session",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=5.0,
        help="Seconds between repeated INFO samples",
    )
    parser.add_argument(
        "--nhk-request-types",
        nargs="+",
        default=list(DEFAULT_NHK_REQUEST_TYPES),
        help="Read-shaped NHK request types to try for advertised readable nodes",
    )
    parser.add_argument(
        "--skip-nhk-property-probes",
        action="store_true",
        help="Skip speculative read-shaped NHK property requests",
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="Enable the broadest read-only post-live scan: GET selectors, broad DMP, and longer frame draining",
    )
    parser.add_argument(
        "--dmp-profile",
        choices=("none", "focused", "broad"),
        default="focused",
        help="Optional post-live DMP register scan size",
    )
    parser.add_argument(
        "--max-dmp-reads",
        type=int,
        default=400,
        help="Maximum number of generated DMP register reads",
    )
    parser.add_argument(
        "--dmp-delay",
        type=float,
        default=0.05,
        help="Delay between DMP reads to avoid hammering the interface",
    )
    parser.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include host, username, MAC, source id, and serial numbers. Password is always redacted.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    parser.add_argument("--quiet", action="store_true", help="Do not print progress to stderr")
    return parser.parse_args()


def _apply_exhaustive_defaults(args: argparse.Namespace) -> None:
    if not args.exhaustive:
        return
    args.nhk_request_types = list(dict.fromkeys([*args.nhk_request_types, *EXHAUSTIVE_NHK_REQUEST_TYPES]))
    args.dmp_profile = "broad"
    args.max_dmp_reads = max(args.max_dmp_reads, 4096)
    args.post_request_listen_seconds = max(args.post_request_listen_seconds, 1.0)


def _load_credentials(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text())


def _value_from_args(args: argparse.Namespace, data: dict[str, Any], arg_name: str, *json_names: str) -> str | None:
    value = getattr(args, arg_name)
    if value:
        return value
    for name in json_names:
        json_value = data.get(name)
        if json_value:
            return str(json_value)
    return None


def _credentials_from_args(args: argparse.Namespace) -> NiceBidiCredentials:
    data = _load_credentials(args.credentials)
    username = _value_from_args(args, data, "username", "username")
    password_hex = _value_from_args(args, data, "password_hex", "password_hex", "password")
    target_mac = _value_from_args(args, data, "target_mac", "target_mac")
    source_id = _value_from_args(args, data, "source_id", "source_id", "source")

    missing = [
        name
        for name, value in (
            ("username", username),
            ("password_hex", password_hex),
            ("target_mac", target_mac),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing credential field(s): {', '.join(missing)}")

    return NiceBidiCredentials(
        username=username,
        password_hex=password_hex,
        target_mac=target_mac,
        source_id=source_id,
    )


def _progress(args: argparse.Namespace, message: str) -> None:
    if not args.quiet:
        print(message, file=sys.stderr, flush=True)


def _redacted(value: object | None) -> object | None:
    if value in (None, ""):
        return value
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"<redacted:{digest}>"


def _maybe_sensitive(key: str, value: object, include_sensitive: bool) -> object:
    if key in {"password", "password_hex"}:
        return "<redacted>"
    if include_sensitive:
        return value
    if key in SENSITIVE_DATA_KEYS:
        return _redacted(value)
    return value


def _redact_mapping(data: dict[str, Any], include_sensitive: bool) -> dict[str, Any]:
    return {key: _maybe_sensitive(key, value, include_sensitive) for key, value in data.items()}


def _redact_text(text: str, include_sensitive: bool) -> str:
    if include_sensitive:
        return text

    redacted = text
    for attr in SENSITIVE_XML_ATTRS:
        redacted = re.sub(
            rf'({attr}=")([^"]*)(")',
            lambda match: f"{match.group(1)}{_redacted(match.group(2))}{match.group(3)}",
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def _redact_xml(xml_text: str, include_sensitive: bool) -> str:
    if include_sensitive:
        return xml_text

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return _redact_text(xml_text, include_sensitive)

    for element in root.iter():
        if element.tag in SENSITIVE_XML_TAGS and element.text:
            element.text = str(_redacted(element.text.strip()))
        for key, value in list(element.attrib.items()):
            if key.lower() in SENSITIVE_XML_ATTRS:
                element.set(key, str(_redacted(value)))
    return ET.tostring(root, encoding="unicode")


def _response_xml(response: bytes, include_sensitive: bool) -> str:
    return _redact_xml(_xml_payload(response), include_sensitive)


def _response_report(response: bytes, include_sensitive: bool) -> dict[str, Any]:
    text = _printable(response)
    return {
        "summary": _response_summary(response),
        "type": _attr(text, "type"),
        "id": _attr(text, "id"),
        "has_error": "<Error" in text,
        "error_code": nice_bidi_error_code(text),
        "xml": _response_xml(response, include_sensitive),
    }


def _frame_kind(response: bytes) -> str:
    try:
        return ET.fromstring(_xml_payload(response)).tag
    except ET.ParseError:
        text = _printable(response)
        if "<Event " in text:
            return "Event"
        if "<Response " in text:
            return "Response"
        if "<Request " in text:
            return "Request"
        return "Unknown"


def _hex_byte(value: int) -> str:
    return f"{value:02X}"


def _t4_address(row: int, address: int) -> str:
    return f"{row:02X}.{address:02X}"


def _t4_address_role(row: int, address: int) -> str | None:
    if address == 0xFF:
        return "broadcast"
    if (row, address) in {(0x50, 0x90), (0x50, 0x91)}:
        return "BiDi-WiFi hub"
    if row == 0x00 and address == 0x03:
        return "possible controller"
    if row == 0x00 and address == 0x0A:
        return "possible OXI/radio"
    return None


def _t4_register_label(device_type: int, register_id: int) -> str | None:
    return KNOWN_DMP_LABELS.get((device_type, register_id)) or KNOWN_DMP_LABELS.get((0x00, register_id))


def _data_summary(data: bytes) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "length": len(data),
        "hex": data.hex(" "),
    }
    if data:
        summary["uint_be"] = int.from_bytes(data, "big")
        summary["uint_le"] = int.from_bytes(data, "little")
        summary["all_zero"] = all(byte == 0x00 for byte in data)
        summary["all_ff"] = all(byte == 0xFF for byte in data)
        if all(32 <= byte < 127 for byte in data):
            summary["ascii"] = data.decode("ascii")
    return summary


def _decode_position_data(data: bytes) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    if len(data) == 1:
        decoded["position"] = data[0]
        decoded["format"] = "1-byte"
    elif len(data) == 2:
        decoded["position"] = int.from_bytes(data, "big")
        decoded["format"] = "2-byte big-endian"
    elif len(data) >= 3:
        decoded["prefix"] = data[0]
        decoded["position"] = data[1]
        decoded["fine"] = data[2]
        decoded["format"] = "3-byte prefix/position/fine"
    return decoded


def _decode_dmp_value(device_type: int, register_id: int, data: bytes) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    if not data:
        return decoded

    if register_id == 0x04:
        decoded["reported_device_type"] = _hex_byte(data[0])
        decoded["reported_device_type_name"] = T4_DEVICE_TYPES.get(data[0])

    if register_id == 0x01:
        decoded["status"] = T4_AUTO_STATUS.get(data[0])
        decoded["status_byte"] = _hex_byte(data[0])

    if register_id in {0x11, 0x12, 0x18, 0x19, 0x21, 0x22, 0x23}:
        decoded["position"] = _decode_position_data(data)

    if register_id == 0xD0:
        decoded["stop_reason"] = T4_STOP_REASONS.get(data[0])
        decoded["stop_reason_byte"] = _hex_byte(data[0])
        decoded["obstacle"] = data[0] in {0x01, 0x02}

    if register_id == 0xD1:
        io_byte = data[2] if len(data) >= 3 else data[0]
        decoded["io_byte"] = _hex_byte(io_byte)
        decoded["limit_closed"] = bool(io_byte & 0x01)
        decoded["limit_open"] = bool(io_byte & 0x02)
        decoded["photocell"] = bool(io_byte & 0x04)

    if register_id in {0x71, 0x72, 0x73, 0x74, 0x80, 0x84, 0x88, 0x8C, 0x94, 0x9C}:
        decoded["enabled"] = data[0] != 0

    if device_type == 0x0A and register_id in {0x04, 0x09, 0x0A, 0x0B, 0x0C}:
        decoded["radio_related"] = True

    return decoded


def _parse_t4_inf_message(message: bytes) -> dict[str, Any]:
    parsed: dict[str, Any] = {"raw_hex": message.hex(" ")}
    if len(message) < 3:
        return parsed

    device_type = message[0]
    register_id = message[1]
    operation = message[2]
    parsed.update(
        {
            "device_type": _hex_byte(device_type),
            "device_type_name": T4_DEVICE_TYPES.get(device_type),
            "register": _hex_byte(register_id),
            "register_label": _t4_register_label(device_type, register_id),
            "operation": _hex_byte(operation),
            "operation_name": T4_DMP_OPERATIONS.get(operation),
            "operation_kind": _t4_dmp_operation_kind(operation),
        }
    )

    data = b""
    if len(message) >= 5:
        if operation in {0x18, 0x19, 0x29}:
            data_len = message[3]
            next_data = message[4]
            data_start = 5
        else:
            next_data = message[3]
            data_len = message[4]
            data_start = 5
        data = message[data_start : data_start + data_len]
        parsed["data_len"] = data_len
        parsed["next_data"] = _hex_byte(next_data)
        parsed["data"] = _data_summary(data)
        parsed["value_decode"] = _decode_dmp_value(device_type, register_id, data)

    return parsed


def _parse_t4_cmd_message(message: bytes) -> dict[str, Any]:
    parsed: dict[str, Any] = {"raw_hex": message.hex(" ")}
    if not message:
        return parsed

    menu = message[0]
    parsed["menu"] = _hex_byte(menu)
    parsed["menu_name"] = T4_MENUS.get(menu)
    if len(message) >= 2:
        subcommand = message[1]
        parsed["subcommand"] = _hex_byte(subcommand)
        parsed["subcommand_name"] = T4_MENUS.get(subcommand)
    if len(message) >= 3:
        byte_2 = message[2]
        parsed["byte_2"] = _hex_byte(byte_2)
        if menu == 0x01 and len(message) >= 2 and message[1] == 0x82:
            parsed["control_command"] = T4_CONTROL_COMMANDS.get(byte_2)
        if byte_2 in T4_AUTO_STATUS:
            parsed["status"] = T4_AUTO_STATUS[byte_2]
    if len(message) >= 5:
        parsed["possible_position"] = int.from_bytes(message[3:5], "big")
    return parsed


def _parse_bus_t4_payload(plain: bytes) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "length": len(plain),
        "plain_hex": plain.hex(" "),
    }
    if len(plain) < 3:
        return parsed

    framed = plain[0] == 0x55
    parsed["framed"] = framed
    if framed:
        body_size = plain[1]
        parsed["body_size"] = body_size
        parsed["trailing_size"] = plain[-1]
        parsed["size_matches"] = body_size == plain[-1] == len(plain) - 3
        body = plain[2:-1]
    else:
        body = plain

    parsed["body_hex"] = body.hex(" ")
    if len(body) < 8:
        return parsed

    to_row, to_address, from_row, from_address, msg_type, msg_size, crc1 = body[:7]
    crc2 = body[-1]
    message = body[7:-1]
    parsed.update(
        {
            "to": _t4_address(to_row, to_address),
            "to_role": _t4_address_role(to_row, to_address),
            "from": _t4_address(from_row, from_address),
            "from_role": _t4_address_role(from_row, from_address),
            "message_type": _hex_byte(msg_type),
            "message_type_name": T4_MSG_TYPES.get(msg_type),
            "message_size": msg_size,
            "message_length": len(message),
            "message_size_matches": msg_size == len(message) + 1,
            "crc1": _hex_byte(crc1),
            "crc2": _hex_byte(crc2),
            "message_hex": message.hex(" "),
        }
    )

    if msg_type == 0x08:
        parsed["inf"] = _parse_t4_inf_message(message)
        if len(message) >= 3 and message[0] in {0x02, 0x04} and message[2] in T4_AUTO_STATUS:
            parsed["rsp_status"] = _parse_t4_cmd_message(message)
    elif msg_type == 0x01:
        parsed["cmd"] = _parse_t4_cmd_message(message)

    return parsed


def _t4_payload_reports(client: ProbeClient, response: bytes) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for plain in client.decrypt_t4_payloads(response):
        parsed = parse_dmp_response(plain)
        parsed["value_interpretations"] = _value_interpretations(parsed)
        bus_t4 = _parse_bus_t4_payload(plain)
        payloads.append(
            {
                "length": len(plain),
                "plain_hex": plain.hex(" "),
                "dmp_parse": parsed,
                "bus_t4_parse": bus_t4,
            }
        )
    return payloads


def _frame_report(client: ProbeClient, response: bytes, include_sensitive: bool) -> dict[str, Any]:
    report = _response_report(response, include_sensitive)
    report["frame_kind"] = _frame_kind(response)
    report["leaf_values"] = _leaf_values(_xml_payload(response), include_sensitive)

    t4_payloads = _t4_payload_reports(client, response)
    if t4_payloads:
        report["decrypted_t4_payloads"] = t4_payloads
    return report


def _exception_report(exc: Exception, include_sensitive: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": exc.__class__.__name__,
        "message": _redact_text(str(exc), include_sensitive),
        "error_code": nice_bidi_error_code(exc),
    }


def _device_info_report(info: NiceBidiDeviceInfo, include_sensitive: bool) -> dict[str, Any]:
    data = asdict(info)
    data.pop("services", None)
    data.pop("properties", None)
    return _redact_mapping(data, include_sensitive)


def _element_label(element: ET.Element) -> str:
    element_id = element.get("id")
    return element.tag if element_id is None else f'{element.tag}[@id="{element_id}"]'


def _split_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _text_value(element: ET.Element) -> str | None:
    value = (element.text or "").strip()
    return value or None


def _redact_xml_attrs(attrs: dict[str, str], include_sensitive: bool) -> dict[str, str]:
    if include_sensitive:
        return dict(attrs)
    return {
        key: str(_redacted(value)) if key.lower() in SENSITIVE_XML_ATTRS else value
        for key, value in attrs.items()
    }


def _info_inventory(info_xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(info_xml)
    inventory: list[dict[str, Any]] = []

    def walk(node: ET.Element, path: str) -> None:
        for child in list(node):
            child_path = f"{path}/{_element_label(child)}"
            if child.tag in INFO_CONTAINERS:
                for item in list(child):
                    values_raw = item.get("values")
                    inventory.append(
                        {
                            "owner": node.tag,
                            "owner_id": node.get("id"),
                            "container": child.tag,
                            "container_attrs": dict(child.attrib),
                            "name": item.tag,
                            "path": f"{path}/{child.tag}/{_element_label(item)}",
                            "value_type": item.get("type"),
                            "permission": item.get("perm"),
                            "values_raw": values_raw,
                            "values": _split_values(values_raw),
                            "attributes": dict(item.attrib),
                            "current_value": _text_value(item),
                        }
                    )
            walk(child, child_path)

    walk(root, _element_label(root))
    return sorted(
        inventory,
        key=lambda item: (
            str(item["owner"]),
            str(item["owner_id"] or ""),
            str(item["container"]),
            str(item["name"]),
        ),
    )


def _inventory_summary(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "readable": [],
        "writable": [],
        "with_current_value": [],
    }
    for item in inventory:
        permission = item["permission"] or ""
        name = item["name"]
        container_key = item["container"].lower()
        if "r" in permission:
            summary["readable"].append(name)
            summary.setdefault(f"readable_{container_key}", []).append(name)
        if "w" in permission:
            summary["writable"].append(name)
            summary.setdefault(f"writable_{container_key}", []).append(name)
        if item["current_value"] is not None:
            summary["with_current_value"].append(item["path"])

    return {
        key: sorted(set(value)) if isinstance(value, list) else value
        for key, value in summary.items()
    }


def _leaf_values(xml_text: str, include_sensitive: bool) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    values: list[dict[str, Any]] = []

    def walk(node: ET.Element, path: str) -> None:
        children = list(node)
        if not children:
            value = _text_value(node)
            if value is not None:
                values.append(
                    {
                        "path": path,
                        "name": node.tag,
                        "value": value if include_sensitive or node.tag not in SENSITIVE_XML_TAGS else _redacted(value),
                        "attributes": _redact_xml_attrs(dict(node.attrib), include_sensitive),
                    }
                )
        for child in children:
            walk(child, f"{path}/{_element_label(child)}")

    walk(root, _element_label(root))
    return values


def _read_info_sample(
    client: ProbeClient,
    args: argparse.Namespace,
    sample_index: int,
    started: float,
) -> dict[str, Any]:
    sample: dict[str, Any] = {
        "sample": sample_index,
        "elapsed_s": round(time.monotonic() - started, 3),
        "ok": False,
    }
    try:
        response = client.signed_probe("INFO")
        xml = _xml_payload(response)
        info = parse_info_xml(xml, args.device_id)
        inventory = _info_inventory(xml)
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        sample.update(_exception_report(exc, args.include_sensitive))
        return sample

    sample.update(
        {
            "ok": True,
            "response": _response_report(response, args.include_sensitive),
            "device_info": _device_info_report(info, args.include_sensitive),
            "inventory": inventory,
            "inventory_summary": _inventory_summary(inventory),
            "leaf_values": _leaf_values(xml, args.include_sensitive),
        }
    )
    return sample


def _run_signed_trace_probe(
    client: ProbeClient,
    args: argparse.Namespace,
    request_type: str,
    body: str,
    label: str,
    started: float,
    *,
    wait_timeout: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "elapsed_s": round(time.monotonic() - started, 3),
        "label": label,
        "request_type": request_type,
        "ok": False,
    }
    if body:
        result["request_body_xml"] = _redact_xml(body, args.include_sensitive)

    try:
        trace = client.signed_probe_trace(
            request_type,
            body,
            wait_timeout=wait_timeout,
            post_response_listen_seconds=args.post_request_listen_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        result.update(_exception_report(exc, args.include_sensitive))
        return result

    frames = [_frame_report(client, frame, args.include_sensitive) for frame in trace["frames"]]
    expected_frame_index = trace["expected_frame_index"]
    expected_frame = frames[expected_frame_index] if expected_frame_index is not None else None
    result.update(
        {
            "request_id": trace["request_id"],
            "expected_frame_index": expected_frame_index,
            "ok": bool(expected_frame) and not bool(expected_frame.get("has_error")),
            "frames": frames,
        }
    )
    if expected_frame is None:
        result["message"] = "No matching same-id response was received before timeout."
    return result


def _selector_body(owner: str, owner_id: str | None, container: str, names: list[str], device_id: int) -> str | None:
    elements = "".join(f"<{name} />\r\n" for name in names)
    if owner == "Interface":
        id_attr = f' id="{owner_id}"' if owner_id else ""
        return f"<Interface{id_attr}>\r\n<{container}>\r\n{elements}</{container}>\r\n</Interface>\r\n"
    if owner == "Device":
        selected_device_id = owner_id or str(device_id)
        return (
            "<Devices>\r\n"
            f'<Device id="{selected_device_id}">\r\n'
            f"<{container}>\r\n"
            f"{elements}"
            f"</{container}>\r\n"
            "</Device>\r\n"
            "</Devices>\r\n"
        )
    return None


def _nhk_selector_candidates(inventory: list[dict[str, Any]], device_id: int) -> list[dict[str, Any]]:
    readable = [item for item in inventory if "r" in (item["permission"] or "")]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(label: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        first = items[0]
        names = sorted({str(item["name"]) for item in items})
        body = _selector_body(
            str(first["owner"]),
            first["owner_id"],
            str(first["container"]),
            names,
            device_id,
        )
        if body is None:
            return
        key = (label, body)
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "label": label,
                "owner": first["owner"],
                "owner_id": first["owner_id"],
                "container": first["container"],
                "names": names,
                "body": body,
            }
        )

    for item in sorted(readable, key=lambda entry: (str(entry["name"]) not in PRIORITY_READ_NAMES, str(entry["name"]))):
        add_candidate(f'{item["path"]} only', [item])

    grouped: dict[tuple[str, str | None, str], list[dict[str, Any]]] = {}
    for item in readable:
        grouped.setdefault((str(item["owner"]), item["owner_id"], str(item["container"])), []).append(item)
    for (owner, owner_id, container), items in sorted(grouped.items()):
        owner_label = owner if owner_id is None else f'{owner}[@id="{owner_id}"]'
        add_candidate(f"{owner_label}/{container} all readable", items)

    return candidates


def _run_nhk_probe(
    client: ProbeClient,
    args: argparse.Namespace,
    request_type: str,
    candidate: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "elapsed_s": round(time.monotonic() - started, 3),
        "request_type": request_type,
        "candidate": candidate,
        "ok": False,
    }
    try:
        trace = client.signed_probe_trace(
            request_type,
            str(candidate["body"]),
            post_response_listen_seconds=args.post_request_listen_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        result.update(_exception_report(exc, args.include_sensitive))
        return result

    frames = [_frame_report(client, frame, args.include_sensitive) for frame in trace["frames"]]
    expected_frame_index = trace["expected_frame_index"]
    response_report = frames[expected_frame_index] if expected_frame_index is not None else None
    result.update(
        {
            "request_id": trace["request_id"],
            "expected_frame_index": expected_frame_index,
            "ok": bool(response_report) and not response_report["has_error"],
            "response": response_report,
            "frames": frames,
            "leaf_values": response_report.get("leaf_values", []) if response_report else [],
        }
    )
    if response_report is None:
        result["message"] = "No matching same-id response was received before timeout."
    return result


def _add_dmp_read(reads: dict[tuple[int, int, int, int], DmpRead], daddr: int, dendpoint: int, group: int, parameter: int) -> None:
    label = KNOWN_DMP_LABELS.get((group, parameter))
    key = (daddr, dendpoint, group, parameter)
    reads.setdefault(key, DmpRead(daddr, dendpoint, group, parameter, label))


def _generate_dmp_reads(profile: str, max_reads: int) -> list[DmpRead]:
    if profile == "none" or max_reads <= 0:
        return []

    reads: dict[tuple[int, int, int, int], DmpRead] = {}
    known_registers = sorted(KNOWN_DMP_LABELS)

    for daddr, dendpoint in PRIMARY_DMP_TARGETS:
        for group, parameter in known_registers:
            _add_dmp_read(reads, daddr, dendpoint, group, parameter)

    for endpoint in FOCUSED_DMP_ENDPOINTS:
        for group, parameter in known_registers:
            _add_dmp_read(reads, 0x00, endpoint, group, parameter)

    for daddr in FOCUSED_DMP_DADDRS:
        for endpoint in (0x03, 0x0A):
            for group, parameter in known_registers:
                _add_dmp_read(reads, daddr, endpoint, group, parameter)

    if profile == "broad":
        for daddr, dendpoint in PRIMARY_DMP_TARGETS:
            for parameter in range(0x00, 0x100):
                _add_dmp_read(reads, daddr, dendpoint, 0x04, parameter)
            for group in range(0x00, 0x10):
                for parameter in range(0x00, 0x40):
                    _add_dmp_read(reads, daddr, dendpoint, group, parameter)

    return list(reads.values())[:max_reads]


def _value_interpretations(parsed: dict[str, Any]) -> dict[str, Any]:
    value_hex = parsed.get("value_hex")
    if not value_hex:
        return {}
    try:
        value = bytes.fromhex(str(value_hex))
    except ValueError:
        return {}
    if not value:
        return {}

    interpretations: dict[str, Any] = {
        "uint_be": int.from_bytes(value, "big"),
        "uint_le": int.from_bytes(value, "little"),
        "all_ff": all(byte == 0xFF for byte in value),
        "all_zero": all(byte == 0x00 for byte in value),
    }
    first_byte_state = STATUS_BY_BYTE.get(value[0])
    if first_byte_state:
        interpretations["state_by_first_byte"] = first_byte_state
    if all(32 <= byte < 127 for byte in value):
        interpretations["ascii"] = value.decode("ascii")
    return interpretations


def _run_dmp_probe(
    client: ProbeClient,
    args: argparse.Namespace,
    read: DmpRead,
    started: float,
) -> dict[str, Any]:
    frame = build_dmp_read_frame(read.daddr, read.dendpoint, read.group, read.parameter)
    result: dict[str, Any] = {
        "elapsed_s": round(time.monotonic() - started, 3),
        "ok": False,
        "request": {
            "daddr": f"{read.daddr:02X}",
            "dendpoint": f"{read.dendpoint:02X}",
            "group": f"{read.group:02X}",
            "parameter": f"{read.parameter:02X}",
            "label": read.label,
            "plain_hex": frame.hex(" "),
        },
    }
    try:
        response, plains = client.t4_probe(
            "DMP",
            frame,
            read.daddr,
            read.dendpoint,
            args.t4_timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        result.update(_exception_report(exc, args.include_sensitive))
        return result

    response_report = _response_report(response, args.include_sensitive)
    parsed_payloads = []
    for plain in plains:
        parsed = parse_dmp_response(plain)
        parsed["value_interpretations"] = _value_interpretations(parsed)
        parsed["bus_t4_parse"] = _parse_bus_t4_payload(plain)
        parsed_payloads.append(parsed)

    result.update(
        {
            "ok": bool(parsed_payloads) and not response_report["has_error"],
            "response": response_report,
            "plain_payloads": parsed_payloads,
        }
    )
    return result


def _status_report(status: NiceBidiStatus) -> dict[str, Any]:
    return {
        "state": status.state,
        "position": status.position,
        "current_position": status.current_position,
        "closed_position": status.closed_position,
        "open_position": status.open_position,
        "is_moving": status.is_moving,
        "registers": status.registers,
    }


def _read_current_integration_status(client: ProbeClient, started: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "elapsed_s": round(time.monotonic() - started, 3),
        "ok": False,
        "description": "Current integration DMP status path: 04/01, 04/11, 04/18, 04/19 at address 00 endpoint 03",
    }
    try:
        status = client.read_status()
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        result.update(_exception_report(exc, False))
        return result
    result.update({"ok": True, "status": _status_report(status)})
    return result


def _poll_schedule(args: argparse.Namespace, live_started: float) -> dict[str, dict[str, float | str]]:
    schedule: dict[str, dict[str, float | str]] = {}
    for request_type, interval in (
        ("STATUS", args.status_poll_interval),
        ("T4_STATUS", args.t4_status_poll_interval),
        ("INFO", args.info_poll_interval),
    ):
        if interval > 0:
            schedule[request_type] = {
                "request_type": request_type,
                "interval": interval,
                "next_due": live_started + interval,
            }
    return schedule


def _listen_once(
    client: ProbeClient,
    args: argparse.Namespace,
    started: float,
    duration_s: float,
) -> list[dict[str, Any]]:
    try:
        frames = client.listen_frames(duration_s, args.listen_poll_timeout)
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        return [
            {
                "elapsed_s": round(time.monotonic() - started, 3),
                "ok": False,
                **_exception_report(exc, args.include_sensitive),
            }
        ]

    return [
        {
            "elapsed_s": round(time.monotonic() - started, 3),
            "ok": True,
            "frame": _frame_report(client, frame, args.include_sensitive),
        }
        for frame in frames
    ]


def _run_live_capture(client: ProbeClient, args: argparse.Namespace, started: float) -> dict[str, Any]:
    live_started = time.monotonic()
    manual_stop = args.listen_until_interrupted
    deadline = None if manual_stop else live_started + max(0.0, args.listen_seconds)
    result: dict[str, Any] = {
        "ok": True,
        "started_elapsed_s": round(live_started - started, 3),
        "duration_requested_s": None if manual_stop else args.listen_seconds,
        "manual_stop": manual_stop,
        "ended_by_interrupt": False,
        "listen_poll_timeout_s": args.listen_poll_timeout,
        "post_request_listen_seconds": args.post_request_listen_seconds,
        "poll_intervals_s": {
            "STATUS": args.status_poll_interval,
            "T4_STATUS": args.t4_status_poll_interval,
            "INFO": args.info_poll_interval,
        },
        "initial_request_traces": [],
        "request_traces": [],
        "passive_frames": [],
    }

    try:
        for request_type in LIVE_REQUEST_TYPES:
            _progress(args, f"Live capture initial {request_type}")
            result["initial_request_traces"].append(
                _run_signed_trace_probe(
                    client,
                    args,
                    request_type,
                    "",
                    f"live initial {request_type}",
                    started,
                    wait_timeout=args.timeout,
                )
            )

        schedule = _poll_schedule(args, live_started)
        last_progress_bucket = -1
        while deadline is None or time.monotonic() < deadline:
            now = time.monotonic()
            elapsed_live = now - live_started
            progress_bucket = int(elapsed_live // 10)
            if progress_bucket != last_progress_bucket:
                last_progress_bucket = progress_bucket
                if manual_stop:
                    _progress(args, f"Live capture running: {elapsed_live:.0f}s; press Ctrl-C once when finished")
                else:
                    _progress(
                        args,
                        f"Live capture running: {min(elapsed_live, args.listen_seconds):.0f}/{args.listen_seconds:.0f}s",
                    )

            due = [
                item
                for item in schedule.values()
                if float(item["next_due"]) <= now
            ]
            if due:
                for item in due:
                    request_type = str(item["request_type"])
                    _progress(args, f"Live capture poll {request_type}")
                    result["request_traces"].append(
                        _run_signed_trace_probe(
                            client,
                            args,
                            request_type,
                            "",
                            f"live poll {request_type}",
                            started,
                            wait_timeout=args.timeout,
                        )
                    )
                    item["next_due"] = time.monotonic() + float(item["interval"])
                continue

            upcoming = [float(item["next_due"]) for item in schedule.values()]
            if deadline is not None:
                upcoming.append(deadline)
            listen_for = args.listen_poll_timeout
            if upcoming:
                listen_for = min(args.listen_poll_timeout, max(0.0, min(upcoming) - time.monotonic()))
            if listen_for > 0:
                result["passive_frames"].extend(_listen_once(client, args, started, listen_for))
    except KeyboardInterrupt:
        result["ended_by_interrupt"] = True
        _progress(args, "Live capture stopped; continuing with post-live read-only probes")

    result["duration_actual_s"] = round(time.monotonic() - live_started, 3)
    return result


def _iter_trace_frames(trace: dict[str, Any]) -> list[dict[str, Any]]:
    frames = trace.get("frames", [])
    return frames if isinstance(frames, list) else []


def _iter_reported_frames(report: dict[str, Any]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    live_capture = report.get("live_capture") or {}
    for trace in live_capture.get("initial_request_traces", []):
        frames.extend(_iter_trace_frames(trace))
    for trace in live_capture.get("request_traces", []):
        frames.extend(_iter_trace_frames(trace))
    for item in live_capture.get("passive_frames", []):
        frame = item.get("frame")
        if isinstance(frame, dict):
            frames.append(frame)
    for probe in report.get("nhk_read_probes", []):
        frames.extend(_iter_trace_frames(probe))
    return frames


def _iter_bus_t4_parses(report: dict[str, Any]) -> list[dict[str, Any]]:
    parses: list[dict[str, Any]] = []
    for frame in _iter_reported_frames(report):
        for payload in frame.get("decrypted_t4_payloads", []):
            bus_t4 = payload.get("bus_t4_parse")
            if isinstance(bus_t4, dict):
                parses.append(bus_t4)
    for probe in report.get("dmp_register_probes", []):
        for payload in probe.get("plain_payloads", []):
            bus_t4 = payload.get("bus_t4_parse")
            if isinstance(bus_t4, dict):
                parses.append(bus_t4)
    return parses


def _summarize_results(report: dict[str, Any]) -> dict[str, Any]:
    observations: list[str] = []
    info_samples = report.get("info_samples", [])
    first_inventory = info_samples[0].get("inventory", []) if info_samples and info_samples[0].get("ok") else []
    property_names = {
        item["name"]
        for item in first_inventory
        if item["container"] == "Properties"
    }
    if "DoorStatus" in property_names:
        observations.append("INFO advertises Device/Properties/DoorStatus.")
    if "Obstruct" in property_names:
        observations.append("INFO advertises Device/Properties/Obstruct.")

    info_current_values = [
        value
        for sample in info_samples
        for value in sample.get("leaf_values", [])
        if value.get("name") in {"DoorStatus", "Obstruct"}
    ]
    if info_current_values:
        observations.append("At least one INFO sample returned a non-empty DoorStatus/Obstruct value.")
    elif {"DoorStatus", "Obstruct"} & property_names:
        observations.append("INFO samples advertised status properties but did not include live values for them.")

    nhk_results = report.get("nhk_read_probes", [])
    nhk_successes = [probe for probe in nhk_results if probe.get("ok")]
    if nhk_successes:
        observations.append(f"{len(nhk_successes)} read-shaped NHK property probe(s) returned without an XML error.")

    live_capture = report.get("live_capture") or {}
    live_frames = _iter_reported_frames(report)
    live_events = [frame for frame in live_frames if frame.get("frame_kind") == "Event"]
    live_t4_payloads = [
        payload
        for frame in live_frames
        for payload in frame.get("decrypted_t4_payloads", [])
    ]
    live_status_values = [
        value
        for frame in live_frames
        for value in frame.get("leaf_values", [])
        if value.get("name") in {"DoorStatus", "Obstruct", "Status", "Event", "T4"}
    ]
    if live_events:
        observations.append(f"Live capture recorded {len(live_events)} async Event frame(s).")
    if live_t4_payloads:
        observations.append(f"Live capture decrypted {len(live_t4_payloads)} T4 payload(s).")
    if live_status_values:
        observations.append("Live capture included leaf values that may describe status or events.")

    bus_t4_parses = _iter_bus_t4_parses(report)
    bus_t4_statuses = Counter()
    bus_t4_registers = Counter()
    bus_t4_from_roles = Counter()
    bus_t4_to_roles = Counter()
    bus_t4_message_types = Counter()
    bus_t4_positions = Counter()
    bus_t4_stop_reasons = Counter()
    bus_t4_diag_io = Counter()
    bus_t4_radio_related = 0
    for parsed in bus_t4_parses:
        if parsed.get("message_type_name"):
            bus_t4_message_types[str(parsed["message_type_name"])] += 1
        if parsed.get("from_role"):
            bus_t4_from_roles[str(parsed["from_role"])] += 1
        if parsed.get("to_role"):
            bus_t4_to_roles[str(parsed["to_role"])] += 1
        inf = parsed.get("inf") or {}
        if inf.get("register_label"):
            bus_t4_registers[str(inf["register_label"])] += 1
        value_decode = inf.get("value_decode") or {}
        if value_decode.get("status"):
            bus_t4_statuses[str(value_decode["status"])] += 1
        position = value_decode.get("position") or {}
        if isinstance(position, dict) and position.get("position") is not None:
            position_label = inf.get("register_label") or inf.get("register") or "unknown register"
            bus_t4_positions[f"{position_label}: {position['position']}"] += 1
        if value_decode.get("stop_reason"):
            bus_t4_stop_reasons[str(value_decode["stop_reason"])] += 1
        for key in ("limit_closed", "limit_open", "photocell"):
            if value_decode.get(key):
                bus_t4_diag_io[key] += 1
        if value_decode.get("radio_related"):
            bus_t4_radio_related += 1
        cmd = parsed.get("cmd") or parsed.get("rsp_status") or {}
        if cmd.get("status"):
            bus_t4_statuses[str(cmd["status"])] += 1
        if cmd.get("possible_position") is not None:
            bus_t4_positions[f"CMD/DEP possible position: {cmd['possible_position']}"] += 1
    if bus_t4_statuses:
        observations.append("Decoded BusT4 payloads included gate status values.")
    if bus_t4_positions:
        observations.append("Decoded BusT4 payloads included possible position values.")
    if bus_t4_stop_reasons or bus_t4_diag_io:
        observations.append("Decoded BusT4 payloads included diagnostic values.")
    if bus_t4_from_roles.get("possible OXI/radio") or bus_t4_to_roles.get("possible OXI/radio"):
        observations.append("Decoded BusT4 payloads included possible OXI/radio traffic.")

    dmp_results = report.get("dmp_register_probes", [])
    dmp_successes = [probe for probe in dmp_results if probe.get("ok")]
    if dmp_successes:
        observations.append(f"{len(dmp_successes)} DMP register probe(s) returned decrypted payloads without XML errors.")

    error_codes = Counter()
    for section_name in ("nhk_read_probes", "dmp_register_probes"):
        for item in report.get(section_name, []):
            response = item.get("response") or {}
            code = item.get("error_code") or response.get("error_code")
            if code:
                error_codes[str(code)] += 1
    for frame in live_frames:
        code = frame.get("error_code")
        if code:
            error_codes[str(code)] += 1

    frame_kinds = Counter(str(frame.get("frame_kind") or "Unknown") for frame in live_frames)

    return {
        "observations": observations,
        "counts": {
            "info_samples": len(info_samples),
            "live_initial_request_traces": len(live_capture.get("initial_request_traces", [])),
            "live_request_traces": len(live_capture.get("request_traces", [])),
            "live_passive_frame_entries": len(live_capture.get("passive_frames", [])),
            "live_frames": len(live_frames),
            "live_frame_kinds": dict(sorted(frame_kinds.items())),
            "live_event_frames": len(live_events),
            "live_decrypted_t4_payloads": len(live_t4_payloads),
            "decoded_bus_t4_payloads": len(bus_t4_parses),
            "decoded_bus_t4_message_types": dict(sorted(bus_t4_message_types.items())),
            "decoded_bus_t4_from_roles": dict(sorted(bus_t4_from_roles.items())),
            "decoded_bus_t4_to_roles": dict(sorted(bus_t4_to_roles.items())),
            "decoded_bus_t4_registers": dict(sorted(bus_t4_registers.items())),
            "decoded_bus_t4_statuses": dict(sorted(bus_t4_statuses.items())),
            "decoded_bus_t4_positions": dict(sorted(bus_t4_positions.items())),
            "decoded_bus_t4_stop_reasons": dict(sorted(bus_t4_stop_reasons.items())),
            "decoded_bus_t4_diag_io": dict(sorted(bus_t4_diag_io.items())),
            "decoded_bus_t4_radio_related": bus_t4_radio_related,
            "nhk_read_probes": len(nhk_results),
            "nhk_read_successes": len(nhk_successes),
            "dmp_register_probes": len(dmp_results),
            "dmp_register_successes": len(dmp_successes),
            "error_codes": dict(sorted(error_codes.items())),
        },
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    """Run the read-only probe and return a JSON-serializable report."""
    _apply_exhaustive_defaults(args)
    credentials = _credentials_from_args(args)
    client = ProbeClient(
        args.host,
        args.port,
        credentials,
        device_id=args.device_id,
        timeout=args.timeout,
        t4_timeout_ms=args.t4_timeout_ms,
    )
    started = time.monotonic()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "safety": {
            "moves_gate": False,
            "sends_change_requests": False,
            "sends_dep_actions": False,
            "description": "This script authenticates locally, listens for async frames, sends read-only STATUS/T4_STATUS/INFO polls, and can optionally send read-shaped selectors and DMP register reads.",
        },
        "connection": _redact_mapping(
            {
                "host": args.host,
                "port": args.port,
                "target_mac": credentials.target_mac,
                "username": credentials.username,
                "source_id": credentials.source_id,
                "device_id": args.device_id,
            },
            args.include_sensitive,
        ),
        "probe_config": {
            "timeout": args.timeout,
            "t4_timeout_ms": args.t4_timeout_ms,
            "listen_seconds": args.listen_seconds,
            "listen_until_interrupted": args.listen_until_interrupted,
            "listen_poll_timeout": args.listen_poll_timeout,
            "post_request_listen_seconds": args.post_request_listen_seconds,
            "status_poll_interval": args.status_poll_interval,
            "t4_status_poll_interval": args.t4_status_poll_interval,
            "info_poll_interval": args.info_poll_interval,
            "skip_live_capture": args.skip_live_capture,
            "info_samples": args.info_samples,
            "sample_interval": args.sample_interval,
            "nhk_request_types": args.nhk_request_types,
            "skip_nhk_property_probes": args.skip_nhk_property_probes,
            "exhaustive": args.exhaustive,
            "dmp_profile": args.dmp_profile,
            "max_dmp_reads": args.max_dmp_reads,
            "dmp_delay": args.dmp_delay,
        },
        "info_samples": [],
        "live_capture": None,
        "current_integration_status": None,
        "nhk_read_probes": [],
        "dmp_register_probes": [],
    }

    try:
        info_sample_count = max(1, args.info_samples)
        _progress(args, "Reading initial INFO sample")
        first_sample = _read_info_sample(client, args, 1, started)
        report["info_samples"].append(first_sample)
        inventory = first_sample.get("inventory", []) if first_sample.get("ok") else []

        if not args.skip_live_capture and (args.listen_until_interrupted or args.listen_seconds > 0):
            if args.listen_until_interrupted:
                _progress(args, "Starting manual live capture; move the gate now, then press Ctrl-C once when finished")
            else:
                _progress(args, f"Starting {args.listen_seconds:.0f}s live capture; move the gate during this window")
            report["live_capture"] = _run_live_capture(client, args, started)

        _progress(args, "Running current integration status read")
        report["current_integration_status"] = _read_current_integration_status(client, started)

        if not args.skip_nhk_property_probes and inventory:
            candidates = _nhk_selector_candidates(inventory, args.device_id)
            total_nhk = len(candidates) * len(args.nhk_request_types)
            probe_index = 0
            for request_type in args.nhk_request_types:
                for candidate in candidates:
                    probe_index += 1
                    _progress(
                        args,
                        f"Running NHK read-shaped probe {probe_index}/{total_nhk}: {request_type} {candidate['label']}",
                    )
                    report["nhk_read_probes"].append(
                        _run_nhk_probe(client, args, request_type, candidate, started)
                    )

        dmp_reads = _generate_dmp_reads(args.dmp_profile, args.max_dmp_reads)
        total_dmp = len(dmp_reads)
        for index, read in enumerate(dmp_reads, start=1):
            if index == 1 or index == total_dmp or index % 25 == 0:
                _progress(args, f"Running DMP read probe {index}/{total_dmp}")
            report["dmp_register_probes"].append(_run_dmp_probe(client, args, read, started))
            if args.dmp_delay > 0 and index < total_dmp:
                time.sleep(args.dmp_delay)

        for sample_index in range(2, info_sample_count + 1):
            if args.sample_interval > 0:
                time.sleep(args.sample_interval)
            _progress(args, f"Reading INFO sample {sample_index}/{info_sample_count}")
            report["info_samples"].append(_read_info_sample(client, args, sample_index, started))
    finally:
        client.close()

    report["duration_s"] = round(time.monotonic() - started, 3)
    report["summary"] = _summarize_results(report)
    return report


def main() -> int:
    """Run the probe."""
    args = parse_args()
    report = build_report(args)
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(output + "\n")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
