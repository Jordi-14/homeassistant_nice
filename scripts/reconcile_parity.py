#!/usr/bin/env python3
"""Reconcile indexed app source to explicit capability dispositions, never dispatch."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from custom_components.nice_bidiwifi.feature_catalog import app_inventory  # noqa: E402
from custom_components.nice_bidiwifi.parity import (  # noqa: E402
    reconcile,
    summary,
    validate_closure,
    validation_matrix,
)

INDEX = ROOT / "custom_components/nice_bidiwifi/parity_inventory.json"
SUMMARY = ROOT / "custom_components/nice_bidiwifi/parity_summary.json"


def source_facts(rows, roots):
    facts, cached = {}, {}
    for row in rows:
        key = (row["app"], row["source"])
        if key not in cached:
            path = roots[row["app"]] / row["source"]
            text = path.read_text(errors="replace")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest != row["sha256"]:
                raise ValueError("Source differs from indexed export: " + row["id"])
            # Do not copy app code, private example values or arbitrary strings.
            clean = "\n".join(
                line
                for line in text.splitlines()
                if not line.strip().startswith("@Metadata")
            )
            cached[key] = {
                "source_sha256": digest,
                "prototype_markers": bool(
                    re.search(
                        r"\b(?:DummyFlowsManager|DummyAlarmStatus|initDummy\w*|loadDummy\w*)\b",
                        clean,
                    )
                ),
                "phone_hardware_imports": sorted(
                    set(
                        re.findall(
                            r"import android\.(nfc|bluetooth|hardware\.usb)\b", clean
                        )
                    )
                ),
                "t4_command_references": sorted(
                    set(re.findall(r"\bT4Command\.([A-Z][A-Z0-9_]*)\b", clean))
                ),
                "core_table_read_references": bool(
                    re.search(
                        r"TableReadVersionsRequest|TableRuleReadRequest|TableScenaReadRequest",
                        clean,
                    )
                ),
            }
        fact = dict(cached[key])
        fact["native_method_count"] = len(row["native_methods"])
        fact["unrecovered_method_count"] = len(row["unrecovered_methods"])
        facts[row["id"]] = fact
    return facts


def build(facts):
    return reconcile(app_inventory()["entries"], facts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--welcome", type=Path)
    parser.add_argument("--pro", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = app_inventory()["entries"]
    if args.welcome and args.pro:
        facts = source_facts(rows, {"welcome": args.welcome, "pro": args.pro})
    else:
        saved = json.loads(INDEX.read_text())
        facts = {row["id"]: row["source_evidence"] for row in saved["records"]}
    result = build(facts)
    validate_closure(result, validation_matrix(), ROOT)
    for path, data in [(INDEX, result), (SUMMARY, summary(result))]:
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if path.read_text() != text:
                raise SystemExit("Outdated parity closure: " + path.name)
        else:
            path.write_text(text)
    print(json.dumps(summary(result)))


if __name__ == "__main__":
    main()
