#!/usr/bin/env python3
"""Run a broad read-only CU_WIFI status and position probe."""

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
DEFAULT_NHK_REQUEST_TYPES = ("INFO", "GET", "READ")

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

KNOWN_DMP_LABELS = {
    (0x04, 0x01): "gate state",
    (0x04, 0x11): "current encoder position",
    (0x04, 0x18): "open endpoint encoder position",
    (0x04, 0x19): "closed endpoint encoder position",
}


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
        "--info-samples",
        type=int,
        default=8,
        help="Number of INFO samples to collect during the run",
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
        "--dmp-profile",
        choices=("none", "focused", "broad"),
        default="broad",
        help="How many read-only DMP register candidates to try",
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
                        "attributes": dict(node.attrib),
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
        response = client.signed_probe(request_type, str(candidate["body"]))
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve all failures.
        result.update(_exception_report(exc, args.include_sensitive))
        return result

    response_report = _response_report(response, args.include_sensitive)
    result.update(
        {
            "ok": not response_report["has_error"],
            "response": response_report,
            "leaf_values": _leaf_values(_xml_payload(response), args.include_sensitive),
        }
    )
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

    for group, parameter in known_registers:
        _add_dmp_read(reads, 0x00, 0x03, group, parameter)

    for endpoint in range(0x00, 0x08):
        for group, parameter in known_registers:
            _add_dmp_read(reads, 0x00, endpoint, group, parameter)

    for daddr in range(0x00, 0x08):
        for group, parameter in known_registers:
            _add_dmp_read(reads, daddr, 0x03, group, parameter)

    if profile == "broad":
        for parameter in range(0x00, 0x40):
            _add_dmp_read(reads, 0x00, 0x03, 0x04, parameter)
        for group in range(0x00, 0x10):
            for parameter in range(0x00, 0x20):
                _add_dmp_read(reads, 0x00, 0x03, group, parameter)

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

    dmp_results = report.get("dmp_register_probes", [])
    dmp_successes = [probe for probe in dmp_results if probe.get("ok")]
    if dmp_successes:
        observations.append(f"{len(dmp_successes)} DMP register probe(s) returned decrypted payloads without XML errors.")

    error_codes = Counter()
    for section_name in ("nhk_read_probes", "dmp_register_probes"):
        for item in report.get(section_name, []):
            code = item.get("error_code") or item.get("response", {}).get("error_code")
            if code:
                error_codes[str(code)] += 1

    return {
        "observations": observations,
        "counts": {
            "info_samples": len(info_samples),
            "nhk_read_probes": len(nhk_results),
            "nhk_read_successes": len(nhk_successes),
            "dmp_register_probes": len(dmp_results),
            "dmp_register_successes": len(dmp_successes),
            "error_codes": dict(sorted(error_codes.items())),
        },
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    """Run the read-only probe and return a JSON-serializable report."""
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
            "description": "This script authenticates locally and sends INFO, read-shaped NHK selectors, and DMP register reads only.",
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
            "info_samples": args.info_samples,
            "sample_interval": args.sample_interval,
            "nhk_request_types": args.nhk_request_types,
            "skip_nhk_property_probes": args.skip_nhk_property_probes,
            "dmp_profile": args.dmp_profile,
            "max_dmp_reads": args.max_dmp_reads,
            "dmp_delay": args.dmp_delay,
        },
        "info_samples": [],
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
