#!/usr/bin/env python3
"""Cross-check app symbols against the supplied controller table, without I/O to hardware."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("custom_components/nice_bidiwifi/app_inventory.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("custom_components/nice_bidiwifi/controller_catalog_data.py"),
    )
    args = parser.parse_args()
    commands = [
        row
        for row in json.loads(args.inventory.read_text())["entries"]
        if row["kind"] == "controller_command"
    ]
    by_symbol = {row["symbol"]: row for row in commands}
    table = list(csv.DictReader(args.table.open()))
    bindings = {}
    unmatched = []
    for row in table:
        source = by_symbol.get(row["ctrl_name"])
        if source is None or str(source["app_command_id"]) != row["ctrl_id"]:
            unmatched.append((row["ctrl_name"], row["register"]))
            continue
        group, parameter = (int(part, 16) for part in row["register"].split("/"))
        assert group == 4 and parameter == int(row["param_dec"])
        bindings[row["ctrl_name"]] = (group, parameter)
    # Identification bindings already exercised by the integration's OXI reader.
    std = {
        "STD_PROFILE": 4,
        "STD_MAKER": 8,
        "STD_PRODUCT": 9,
        "STD_HARDWARE_VERSION": 10,
        "STD_FIRMWARE_VERSION": 11,
        "STD_DESCRIPTION": 12,
        "STD_RADIO_DESCRIPTION": 12,
        "STD_MENU": 16,
        "STD_INFO_FUNCTION": 17,
    }
    rows = []
    for source in commands:
        symbol = source["symbol"]
        binding = bindings.get(symbol)
        if binding is None and symbol in std:
            binding = (0, std[symbol])
        rows.append(
            (
                symbol,
                source["label"],
                source["app_command_id"],
                binding,
                "DEP" if "BusProtocols.DEP" in source["app_constructor"] else "DMP",
                source["id"],
            )
        )
    for symbol, register in unmatched:
        rows.append(
            (
                symbol,
                "Unresolved controller parameter " + register,
                None,
                None,
                "DMP",
                "controller-table:" + register,
            )
        )
    content = "\n".join(
        [
            '"""Generated source-backed controller inventory. Never dispatch app IDs as wire codes."""',
            "",
            "TABLE_SHA256 = "
            + repr(hashlib.sha256(args.table.read_bytes()).hexdigest()),
            'SOURCE_TABLE = "07-t4-parameter-table.csv"',
            "CONTROLLER_SOURCE_ROWS = (",
            *("    " + repr(row) + "," for row in sorted(rows)),
            ")",
            "",
        ]
    )
    args.output.write_text(content)
    print(
        f"{len(rows)} definitions; {len(bindings)} cross-checked group-04 bindings; {len(unmatched)} unmatched table rows"
    )


if __name__ == "__main__":
    main()
