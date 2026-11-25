#!/usr/bin/env python3
"""Read-only probe of the controller registers for docs/register_map.md.

Reads DMP INFO (flags 0x89) of every group 04 parameter of the control unit, then GET (flags 0x99)
of each parameter whose INFO is not "not supported". It also reads the STD identity of the control
unit and of an OXI receiver. It sends no SET, DEP, CHANGE or movement command.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.dump_bidi_capabilities import (  # noqa: E402
    NiceBidiClient,
    _credentials_from_args,
)

_dmp = sys.modules["custom_components.nice_bidiwifi.client"]
build_dmp_read_frame = _dmp.build_dmp_read_frame
checked_dmp_reply = _dmp.checked_dmp_reply
NiceProtocolError = _dmp.NiceProtocolError
NiceResponseError = _dmp.NiceResponseError

INFO, GET = 0x89, 0x99
CONTROLLER, OXI = (0x00, 0x03), (0x00, 0x0A)

# Registers that start a procedure or a move in MyNice Pro (search, delete, reset, activate, go to
# position, test, calibration; docs/register_map.md group F). Their INFO is read, GET is never sent.
NO_GET_04 = frozenset(
    {0x05, 0x09, 0x0A, 0x0B, 0x0C, 0x0F, 0x27, 0x2F, 0x3F, 0xA9, 0xB4}
    | {0xDC, 0xDD, 0xDE, 0xDF, 0xE5, 0xE7, 0xE9, 0xEE, 0xEF}
)
# Secrets: password (FB) and the built-in radio codes (B8, BA, BB). INFO gives only the value format; their value
# is never read, so it cannot end up in the report.
SECRET_04 = frozenset({0xB8, 0xBA, 0xBB, 0xFB})
# STD identity only: no MAC (07, 21), passwords (13, 14), product detail / serial (20), Wi-Fi code (22).
STD_IDENTITY = (0x04, 0x08, 0x09, 0x0A, 0x0B, 0x0C)
# OXI radio group: receiver type and remote coding only. PIN, keys and remote codes are never read.
OXI_RADIO = (0x00, 0x41)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="BiDi-WiFi IP address or hostname")
    parser.add_argument("--port", type=int, default=443, help="BiDi-WiFi NHK/TLS port")
    parser.add_argument("--credentials", type=Path, help="JSON from scripts/extract_mynice_credentials.py")
    parser.add_argument("--username", help="NHK username")
    parser.add_argument("--password-hex", help="NHK password as 64 hex characters")
    parser.add_argument("--target-mac", help="BiDi MAC address")
    parser.add_argument("--source-id", help="Source/controller ID")
    parser.add_argument("--timeout", type=float, default=10.0, help="Socket timeout in seconds")
    parser.add_argument("--t4-timeout-ms", type=int, default=400, help="T4 register read timeout")
    parser.add_argument("--no-oxi", action="store_true", help="Skip the OXI receiver (address 00.0A)")
    parser.add_argument("--output", type=Path, required=True, help="Write the JSON report to this path")
    return parser.parse_args()


def is_read_frame(frame: bytes) -> bool:
    """Return true only for a DMP request (50 91 08) whose flags byte is INFO or GET, never SET (0xA9)."""
    return len(frame) > 11 and frame[0] == 0x55 and frame[4:7] == b"\x50\x91\x08" and frame[11] in (INFO, GET)


def _read(client: Any, target: tuple[int, int], group: int, parameter: int, flags: int) -> dict[str, Any]:
    """Send one DMP read and return the status and data of the matching reply."""
    expected = 0x09 if flags == INFO else 0x19
    frame = build_dmp_read_frame(*target, group, parameter, flags=flags)
    if not is_read_frame(frame):
        raise SystemExit(f"refusing to send a frame that is not a DMP read: {frame.hex(' ')}")
    _, plains = client._t4_request_locked("DMP", frame, *target, client.t4_timeout_ms)
    for plain in plains:
        try:
            reply = checked_dmp_reply(plain, target=target, group=group, parameter=parameter, operation=expected)
        except NiceResponseError as error:
            return {"status": error.code, "data": ""}
        except NiceProtocolError:
            return {"status": "malformed reply", "data": ""}
        if reply is not None:
            return {"status": "OK", "data": reply.value.hex(" ") if reply.value else ""}
    return {"status": "no reply", "data": ""}


def probe(client: Any, include_oxi: bool) -> list[dict[str, Any]]:
    """Read every register of the plan; one entry per register."""
    plan: list[tuple[str, tuple[int, int], int, int, bool]] = [
        ("controller", CONTROLLER, 0x00, p, True) for p in STD_IDENTITY
    ]
    plan += [("controller", CONTROLLER, 0x04, p, p not in NO_GET_04 | SECRET_04) for p in range(0x100)]
    if include_oxi:
        plan += [("oxi", OXI, 0x00, p, True) for p in STD_IDENTITY]
        plan += [("oxi", OXI, 0x0A, p, True) for p in OXI_RADIO]
    rows = []
    with client._lock:
        client._ensure_connected_locked()
        for name, target, group, parameter, get_allowed in plan:
            row: dict[str, Any] = {"target": name, "register": f"{group:02X}/{parameter:02X}"}
            row["info"] = _read(client, target, group, parameter, INFO)
            if row["info"]["status"] not in {"OK", "F0"} or not get_allowed:
                row["get"] = {"status": "not sent" if not get_allowed else "skipped (INFO FD)", "data": ""}
            else:
                row["get"] = _read(client, target, group, parameter, GET)
            rows.append(row)
            print(f"{name:10} {row['register']}  INFO {row['info']['status']:8} GET {row['get']['status']}", file=sys.stderr)
            time.sleep(0.05)
    return rows


def main() -> int:
    """Run the probe and write the report."""
    args = parse_args()
    client = NiceBidiClient(
        args.host, args.port, _credentials_from_args(args), timeout=args.timeout, t4_timeout_ms=args.t4_timeout_ms
    )
    try:
        info = client.read_info()
        rows = probe(client, include_oxi=not args.no_oxi)
    finally:
        client.close()
    answered = [r for r in rows if r["target"] == "controller" and r["register"].startswith("04/")]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "controller": {
            "product": info.device_product,
            "description": info.device_description,
            "firmware": info.device_fw_version,
            "hardware": info.device_hw_version,
            "interface_firmware": info.interface_fw_version,
        },
        "summary": {
            "group_04_info_ok": sum(r["info"]["status"] == "OK" for r in answered),
            "group_04_get_ok": sum(r["get"]["status"] == "OK" for r in answered),
            "group_04_not_supported": sum(r["info"]["status"] == "FD" for r in answered),
        },
        "registers": rows,
    }
    args.output.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report["summary"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
