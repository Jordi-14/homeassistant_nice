#!/usr/bin/env python3
"""Dump a sanitized capability report from a local Nice interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts._standalone_client import load_client_module  # noqa: E402

_client_module = load_client_module(REPO_ROOT)
NiceBidiClient = _client_module.NiceBidiClient
NiceBidiCredentials = _client_module.NiceBidiCredentials
NiceBidiDeviceInfo = _client_module.NiceBidiDeviceInfo
NiceBidiStatus = _client_module.NiceBidiStatus
parse_info_xml = _client_module.parse_info_xml

STATUS_REGISTER_MEANINGS = {
    "04/01": "gate state",
    "04/11": "current encoder position",
    "04/18": "open endpoint encoder position",
    "04/19": "closed endpoint encoder position",
}

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
}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="BiDi-WiFi IP address or hostname")
    parser.add_argument("--port", type=int, default=443, help="BiDi-WiFi NHK/TLS port")
    parser.add_argument(
        "--credentials",
        type=Path,
        help="JSON file produced by scripts/extract_mynice_credentials.py",
    )
    parser.add_argument("--username", help="NHK username")
    parser.add_argument("--password-hex", help="NHK password as 64 hex characters")
    parser.add_argument("--target-mac", help="BiDi MAC address")
    parser.add_argument("--source-id", help="Source/controller ID")
    parser.add_argument("--device-id", type=int, default=1, help="NHK device id to inspect")
    parser.add_argument("--timeout", type=float, default=10.0, help="Socket timeout in seconds")
    parser.add_argument("--t4-timeout-ms", type=int, default=200, help="T4 register read timeout")
    parser.add_argument(
        "--skip-status",
        action="store_true",
        help="Only read INFO; skip the known DMP status registers",
    )
    parser.add_argument(
        "--include-raw-info",
        action="store_true",
        help="Include redacted raw INFO XML in the JSON report",
    )
    parser.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include host, username, MAC, source id, and serial numbers. Password is always redacted.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
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


def _device_info_report(info: NiceBidiDeviceInfo, include_sensitive: bool) -> dict[str, Any]:
    data = asdict(info)
    data.pop("services", None)
    data.pop("properties", None)
    return _redact_mapping(data, include_sensitive)


def _capability_report(capabilities: tuple[Any, ...]) -> list[dict[str, Any]]:
    sorted_capabilities = sorted(
        capabilities,
        key=lambda capability: (capability.owner, capability.owner_id or "", capability.name),
    )
    return [asdict(capability) for capability in sorted_capabilities]


def _status_report(status: NiceBidiStatus | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "state": status.state,
        "position": status.position,
        "current_position": status.current_position,
        "closed_position": status.closed_position,
        "open_position": status.open_position,
        "is_moving": status.is_moving,
        "register_meanings": STATUS_REGISTER_MEANINGS,
        "registers": status.registers,
    }


def _redact_info_xml(info_xml: str, include_sensitive: bool) -> str:
    if include_sensitive:
        return info_xml

    root = ET.fromstring(info_xml)
    for element in root.iter():
        if element.tag in SENSITIVE_XML_TAGS and element.text:
            element.text = str(_redacted(element.text.strip()))
        for key, value in list(element.attrib.items()):
            if key.lower() in SENSITIVE_XML_ATTRS:
                element.set(key, str(_redacted(value)))
    return ET.tostring(root, encoding="unicode")


def _capability_names(capabilities: list[dict[str, Any]], permission_flag: str) -> list[str]:
    names = []
    for capability in capabilities:
        name = capability["name"]
        permission = capability["permission"] or ""
        if permission_flag in permission:
            names.append(name)
    return sorted(set(names))


def _capability_summary(services: list[dict[str, Any]], properties: list[dict[str, Any]]) -> dict[str, list[str]]:
    readable_services = _capability_names(services, "r")
    writable_services = _capability_names(services, "w")
    readable_properties = _capability_names(properties, "r")
    writable_properties = _capability_names(properties, "w")

    return {
        "readable_services": readable_services,
        "writable_services": writable_services,
        "readable_properties": readable_properties,
        "writable_properties": writable_properties,
        "readable": sorted(set(readable_services + readable_properties)),
        "writable": sorted(set(writable_services + writable_properties)),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    """Connect to the BiDi-WiFi and build the JSON-serializable report."""
    credentials = _credentials_from_args(args)
    client = NiceBidiClient(
        args.host,
        args.port,
        credentials,
        device_id=args.device_id,
        timeout=args.timeout,
        t4_timeout_ms=args.t4_timeout_ms,
    )
    try:
        info_xml = client.read_info_xml()
        info = parse_info_xml(info_xml, args.device_id)
        status = None if args.skip_status else client.read_status()
    finally:
        client.close()

    services = _capability_report(info.services)
    properties = _capability_report(info.properties)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
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
        "device_info": _device_info_report(info, args.include_sensitive),
        "summary": _capability_summary(services, properties),
        "services": services,
        "properties": properties,
        "status": _status_report(status),
    }
    if args.include_raw_info:
        report["raw_info_xml"] = _redact_info_xml(info_xml, args.include_sensitive)
    return report


def main() -> int:
    """Run the capability dump."""
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
