#!/usr/bin/env python3
"""Index the supplied app exports without executing or publishing their code.

Only symbols, source hashes and relative provenance are retained. App requests,
responses, repositories, screens, procedures, protocol classes and command/enum
definitions receive a delivery destination. Native and unrecovered methods are
recorded as evidence gaps rather than executable bindings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = 1


def destination(symbol: str) -> tuple[str, int]:
    name = symbol.casefold()
    if any(
        x in name
        for x in (
            "firmware",
            "radio",
            "transmitter",
            "remote",
            "sparepart",
            "installation",
            "bluetooth",
            "bootloader",
            "reset",
            "quote",
            "devicesscan",
        )
    ):
        return "installer.lifecycle", 6
    if any(
        x in name
        for x in (
            "pair",
            "user",
            "login",
            "password",
            "credential",
            "registration",
            "wnc",
            "wifi",
            "identify",
            "connect",
            "register",
            "authentication",
        )
    ):
        return "setup.access", 5
    if any(
        x in name
        for x in (
            "diagnostic",
            "log",
            "battery",
            "maintenance",
            "event",
            "history",
            "fault",
        )
    ):
        return "diagnostics.complete", 4
    if any(
        x in name
        for x in (
            "home",
            "ambient",
            "room",
            "group",
            "rule",
            "scenario",
            "macro",
            "schedule",
            "cloud",
            "account",
            "ifttt",
            "voice",
            "notification",
            "google",
            "alexa",
            "push",
        )
    ):
        return "cloud.organization", 7
    return "controller.configuration", 3


def inventory(root: Path, app: str, version: str) -> list[dict]:
    chosen = {}
    for path in sorted(root.rglob("*.java")):
        if "$" in path.name or "/sources/" not in str(path):
            continue
        relative = str(path).split("/sources/", 1)[1]
        if not relative.startswith(
            (
                "com/niceforyou/",
                "com/nice/",
                "it/twospecials/",
                "it/buildingapp/",
                "it/nice/",
            )
        ):
            continue
        data = path.read_text(errors="replace")
        # Prefer the pass with the fewest unrecovered methods, then more content.
        score = (data.count("Method not decompiled:"), -len(data))
        if relative not in chosen or score < chosen[relative][0]:
            chosen[relative] = (score, path, data)
    entries = []
    for relative, (_, path, text) in sorted(chosen.items()):
        name = Path(relative).stem
        if name.endswith(("Args", "Directions", "Binding", "Factory")):
            continue
        kind = None
        if name.endswith(("Request", "Response")):
            kind = "request" if name.endswith("Request") else "response"
        elif (
            "/repositories/" in relative
            or "/protocols/" in relative
            or "/protocol/" in relative
        ):
            kind = "repository" if "/repositories/" in relative else "protocol"
        elif "/t4procedures/" in relative:
            kind = "procedure"
        elif (
            "/screens/" in relative or "/presentation/view/" in relative
        ) and name.endswith(("Activity", "Fragment", "Presenter", "ViewModel")):
            kind = "screen"
        elif "enum " in text and (
            "/libraryt4/" in relative
            or "ProductType" in name
            or "AccessoryType" in name
            or name == "AutomationAction"
        ):
            kind = "enum"
        elif (
            "/libraryt4/msg/" in relative
            or "/json_views/" in relative
            or "/connection/wifi_interfaces/" in relative
        ):
            kind = "codec_or_menu"
        if kind is None:
            if name in {"R", "R2", "BuildConfig"}:
                continue
            kind = "event_handler" if "event" in name.casefold() else "supporting_class"
        target, phase = destination(relative)
        public = sorted(
            set(
                re.findall(
                    r"\bpublic\s+(?:final\s+|static\s+|abstract\s+)*(?:[\w<>?,.\[\] ]+\s+)?(\w+)\s*\(",
                    text,
                )
            )
        )
        native = sorted(set(re.findall(r"\bnative\s+[\w<>\[\]]+\s+(\w+)\s*\(", text)))
        unrecovered = sorted(
            set(re.findall(r"Method not decompiled: ([^\"\n]+)", text))
        )
        gaps = ["native.t4_library.pro_2_18"] if native and app == "pro" else []
        if unrecovered:
            gaps.append("apps.unrecovered_methods")
        symbol = relative.removesuffix(".java").replace("/", ".")
        entries.append(
            {
                "id": f"{app}.{kind}.{symbol}",
                "app": app,
                "version": version,
                "kind": kind,
                "symbol": symbol,
                "source": str(path.relative_to(root)),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "members": public,
                "native_methods": native,
                "unrecovered_methods": unrecovered,
                "destination": target,
                "phase": phase,
                "gaps": gaps,
            }
        )
        if name == "T4Command":
            for command, expression in re.findall(
                r"^\s{4}([A-Z][A-Z0-9_]*)\(([^\n]*)\)[,;]", text, re.M
            ):
                match = re.match(r'(-?\d+), "([^"\n]*)"', expression)
                numeric_id, label = (
                    (int(match[1]), match[2]) if match else (None, command)
                )
                dest, ph = destination(command)
                entries.append(
                    {
                        "id": f"pro.command.{command.lower()}",
                        "app": app,
                        "version": version,
                        "kind": "controller_command",
                        "symbol": command,
                        "label": label,
                        "app_command_id": numeric_id,
                        "app_constructor": expression,
                        "source": str(path.relative_to(root)),
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "members": [],
                        "native_methods": [],
                        "unrecovered_methods": [],
                        "destination": dest,
                        "phase": ph,
                        "gaps": [],
                    }
                )
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--welcome", type=Path, required=True)
    parser.add_argument("--pro", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entries = inventory(args.welcome, "welcome", "1.3.1") + inventory(
        args.pro, "pro", "2.18 r188"
    )
    entries.sort(key=lambda row: row["id"])
    assert len({row["id"] for row in entries}) == len(entries)
    result = {
        "schema": SCHEMA_VERSION,
        "apps": {"welcome": "1.3.1", "pro": "2.18 r188"},
        "scope": "All matching primary symbols in the supplied exports; a source item is evidence, not proof of device support.",
        "entries": entries,
    }
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Indexed {len(entries)} source items; {sum(row['kind'] == 'controller_command' for row in entries)} controller commands."
    )


if __name__ == "__main__":
    main()
