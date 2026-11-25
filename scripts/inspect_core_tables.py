#!/usr/bin/env python3
"""Read a count-only CORE rule/scene snapshot without creating an HA entry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._standalone_client import load_client_module  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument(
        "--credentials",
        type=Path,
        required=True,
        help="Private JSON with existing CORE NHK username, password_hex, target_mac and optional source_id",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    module = load_client_module(ROOT)
    from custom_components.nice_bidiwifi.core_inspection import CoreTableReader

    client = None
    try:
        # No password command-line option, cloud login, credential import or
        # pairing. The output intentionally has no raw/sensitive export switch.
        if args.credentials.stat().st_size > 8192:
            raise ValueError("Credential file exceeds limit")
        data = json.loads(args.credentials.read_text())
        credentials = module.NiceBidiCredentials(
            data["username"],
            data.get("password_hex") or data["password"],
            data["target_mac"],
            data.get("source_id"),
        )
        _ = credentials.password
        client = module.NiceBidiClient(args.host, args.port, credentials)
        result = CoreTableReader(client).read().export()
        text = json.dumps(result, indent=2) + "\n"
        if args.output:
            with args.output.open("x") as handle:
                handle.write(text)
        else:
            print(text, end="")
    except Exception as error:
        print("CORE inspection failed: " + type(error).__name__, file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
