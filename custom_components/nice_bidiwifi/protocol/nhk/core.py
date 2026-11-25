"""Source-backed CORE TABLE_READ inspection; no command, table or binding writes."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ...errors import NiceParameterError, NiceProtocolError
from .access import parse_response

TABLES = {"Rules": "Rule", "Scena_In": "Scena", "Scena_Out": "Scena"}
MAX_ROWS = 256


def read_body(table=None):
    if table is None:
        return "<Tables/>\r\n"
    if table not in TABLES:
        raise NiceParameterError("Only CORE rule/scene table inspection is available")
    return f'<Tables><Table id="{table}"/></Tables>\r\n'


def _root(frame, kind):
    root = parse_response(frame, kind)
    if root.get("protocolType") not in {None, "NHK"} or root.get(
        "more", "false"
    ).lower() in {"true", "1"}:
        raise NiceProtocolError("Unverified CORE namespace or continuation")
    return root


def _version(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or any(ord(c) < 32 for c in value)
    ):
        raise NiceProtocolError("CORE table version is missing or invalid")
    return value


def require_core_info(frame):
    root = _root(frame, "INFO")
    interface = root.find("./Interface")
    if interface is None:
        raise NiceProtocolError("CORE INFO has no interface")
    product = re.sub(r"[^a-z0-9]", "", (interface.findtext("Prod") or "").lower())
    if product != "core" and not product.startswith("core"):
        raise NiceParameterError(
            "The authenticated endpoint did not identify itself as CORE"
        )
    return {"product_family": "CORE", "raw_info_retained": False}


def table_versions(frame):
    root = _root(frame, "TABLE_READ")
    nodes = root.findall("./Tables/Table")
    if len(nodes) > 64:
        raise NiceProtocolError("CORE version list exceeds retention limit")
    versions = {}
    for node in nodes:
        name = node.get("id")
        if name not in TABLES:
            continue
        if name in versions:
            raise NiceProtocolError("Duplicate CORE table version")
        versions[name] = _version(node.get("ver"))
    if set(versions) != set(TABLES):
        raise NiceProtocolError("CORE did not report all requested table versions")
    return versions


def table_count(frame, table, expected_version):
    read_body(table)
    root = _root(frame, "TABLE_READ")
    nodes = root.findall("./Tables/Table")
    if len(nodes) != 1 or nodes[0].get("id") != table:
        raise NiceProtocolError("CORE returned the wrong table scope")
    node = nodes[0]
    if node.get("more", "false").lower() in {"true", "1"}:
        raise NiceProtocolError("CORE table continuation is unverified")
    if _version(node.get("ver")) != expected_version:
        raise NiceParameterError(
            "CORE table changed during inspection; no coherent snapshot was captured"
        )
    rows = list(node)
    if len(rows) > MAX_ROWS or any(row.tag != TABLES[table] for row in rows):
        raise NiceProtocolError("CORE table layout is unsupported or oversized")
    # Names, RF codes, row IDs, action lists, user IDs and raw statuses are
    # deliberately not decoded/retained by this count-only inspection.
    return len(rows)


@dataclass(frozen=True, slots=True)
class CoreTableSnapshot:
    rules: int
    scene_inputs: int
    scene_outputs: int
    captured_at: str

    def export(self):
        return {
            "source": "Authenticated CORE TABLE_READ responses",
            "captured_at": self.captured_at,
            "rules": self.rules,
            "scene_inputs": self.scene_inputs,
            "scene_outputs": self.scene_outputs,
            "table_versions_consistent": True,
            "device_execution_confirmed": False,
            "schedule_semantics_verified": False,
            "private_rows_retained": False,
            "write_operations_available": False,
        }
