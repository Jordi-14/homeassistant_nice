"""Reviewed feature definitions, evidence gaps and fail-closed extension slots.

App command identifiers are not wire opcodes. Only reviewed bindings below can
reach a runtime handler; the source inventory is documentation, never dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
import json
from pathlib import Path
from typing import Any

from .protocol.t4.actions import T4_ACTIONS
from .protocol.t4.settings import SETTINGS_BY_REGISTER


class Implementation(StrEnum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"
    BLOCKED = "blocked"


class FeatureBlocked(ValueError):
    """A feature cannot start. No work has been queued or sent."""


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    key: str
    summary: str
    missing: str
    completion: str


GAPS = (
    EvidenceGap(
        "native.t4_library.pro_2_18",
        "Pro native encoder/decoder unavailable",
        "The supplied exports contain JNI declarations but no APK or libnative-lib.so.",
        "Recover the matching native library, trace the mapping and add captured request/reply fixtures.",
    ),
    EvidenceGap(
        "apps.unrecovered_methods",
        "Some exported methods have unrecovered bodies",
        "JADX reports Method not decompiled in the indexed source items.",
        "Recover the method or establish its behavior from a matching, sanitized trace.",
    ),
    EvidenceGap(
        "mc824h.motor_parameter_layout",
        "MC824H motor-specific settings unverified",
        "Complete per-motor addressing, scales and write validation are not established.",
        "Capture app reads/writes and verify per-motor addressing and bounds on this model.",
    ),
    EvidenceGap(
        "mc824h.advanced_history_layout",
        "MC824H advanced history format unverified",
        "The full model-specific event/history record layout is unknown.",
        "Compare app history with raw records, including indices, counters and timestamps.",
    ),
    EvidenceGap(
        "fg01h.force_map_learning",
        "FG01H force-map learning procedure unverified",
        "Start, progress, completion and abort/recovery bindings are incomplete.",
        "Recover the complete procedure and verify failure recovery before enabling it.",
    ),
    EvidenceGap(
        "transport.core",
        "CORE access adapter not implemented",
        "A validated transport/session/authentication contract for CORE is missing.",
        "Establish framing, authentication, targeting, lifecycle and sanitized fixtures.",
    ),
    EvidenceGap(
        "transport.proview",
        "ProView access adapter not implemented",
        "A validated transport/session/authentication contract for ProView is missing.",
        "Establish framing, authentication, targeting, lifecycle and sanitized fixtures.",
    ),
    EvidenceGap(
        "runtime.connection.code1",
        "Some Code 1 connection failures remain unresolved",
        "The cause of the reported controller/session rejection is not established.",
        "Compare affected firmware and app/session traces without weakening authentication.",
    ),
)
GAPS_BY_KEY = {gap.key: gap for gap in GAPS}


@dataclass(frozen=True, slots=True)
class FeatureBinding:
    """Null slots are unresolved; an integer zero remains a valid known value."""

    handler: str | None
    operation: str | None = None
    slots: tuple[tuple[str, int | str | None], ...] = ()
    required_slots: tuple[str, ...] = ()

    def require_resolved(self) -> None:
        values = dict(self.slots)
        missing = [key for key in self.required_slots if values.get(key) is None]
        if self.handler is None or self.operation is None or missing:
            raise FeatureBlocked(
                "Unresolved binding" + (": " + ", ".join(missing) if missing else "")
            )


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    key: str
    title: str
    implementation: Implementation
    phase: int
    binding: FeatureBinding | None = None
    capability: str | None = None
    gaps: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def require_implemented(self) -> FeatureBinding:
        if (
            self.implementation is not Implementation.IMPLEMENTED
            or self.binding is None
        ):
            reason = "; ".join(GAPS_BY_KEY[key].summary for key in self.gaps)
            raise FeatureBlocked(
                f"{self.title}: {reason or f'planned for phase {self.phase}'}"
            )
        self.binding.require_resolved()
        return self.binding


def _features() -> tuple[FeatureDefinition, ...]:
    implemented = Implementation.IMPLEMENTED
    rows = [
        FeatureDefinition(
            f"automation.{action}",
            action.title(),
            implemented,
            1,
            FeatureBinding("action", action),
            "high_level_actions",
            evidence=("NHK DoorAction; existing reviewed command path",),
        )
        for action in ("open", "stop", "close")
    ]
    for key, title, operation, capability in (
        (
            "automation.status",
            "Reported automation status",
            "status",
            "readable_status",
        ),
        ("automation.position", "Position with provenance", "position", None),
        ("automation.live_events", "Local protocol events", "events", "local_events"),
        (
            "automation.movement_profile",
            "Learned directional speed bands",
            "movement_profile",
            None,
        ),
        ("diagnostics.motion_trace", "Bounded motion traces", "motion_trace", None),
    ):
        rows.append(
            FeatureDefinition(
                key,
                title,
                implemented,
                1,
                FeatureBinding("snapshot", operation),
                capability,
                evidence=(
                    "Existing status, position, event and motion-trace components",
                ),
            )
        )
    rows += [
        FeatureDefinition(
            f"t4.{action.key}",
            action.name,
            implemented,
            1,
            FeatureBinding("t4", action.key, (("code", action.code),), ("code",)),
            evidence=("protocol/t4/actions.py; INFO T4_allowed",),
        )
        for action in T4_ACTIONS
    ]
    rows += [
        FeatureDefinition(
            f"setting.{setting.key}",
            setting.key.replace("_", " ").title(),
            implemented,
            1,
            FeatureBinding(
                "setting",
                setting.key,
                (("group", setting.group), ("parameter", setting.parameter)),
                ("group", "parameter"),
            ),
            evidence=("protocol/t4/settings.py; controller INFO and parameter engine",),
        )
        for setting in SETTINGS_BY_REGISTER.values()
    ]
    for key, title, handler, capability in (
        ("interface.logs", "Module event logs", "logs", "logs"),
        ("controller.history", "Controller history", "history", None),
        ("interface.groups", "Access group summaries", "groups", "groups"),
        ("interface.name", "Module name", "name", "interface_name_write"),
        ("interface.clock", "Module clock synchronization", "clock", "time_sync"),
        ("interface.reboot", "Module reboot", "reboot", "reboot"),
    ):
        rows.append(
            FeatureDefinition(
                key,
                title,
                implemented,
                1,
                FeatureBinding(handler, key),
                capability,
                evidence=("controllers/administration.py; shared Wi-Fi protocol",),
            )
        )
    for key, title, phase in (
        ("controller.configuration", "Complete controller configuration", 3),
        ("diagnostics.complete", "Complete app diagnostics", 4),
        ("setup.access", "Setup, users and network management", 5),
        ("installer.lifecycle", "Installer, radio and firmware procedures", 6),
        ("cloud.organization", "Cloud, groups, rules and organization", 7),
    ):
        rows.append(
            FeatureDefinition(
                key,
                title,
                Implementation.PARTIAL
                if phase in (3, 4, 5, 7)
                else Implementation.PLANNED,
                phase,
            )
        )
    for key, title, phase, gap in (
        (
            "controller.mc824h.motor_parameters",
            "MC824H per-motor settings",
            3,
            "mc824h.motor_parameter_layout",
        ),
        (
            "controller.mc824h.advanced_history",
            "MC824H advanced history",
            4,
            "mc824h.advanced_history_layout",
        ),
        (
            "installer.fg01h.force_map",
            "FG01H force-map learning",
            6,
            "fg01h.force_map_learning",
        ),
        ("transport.core", "CORE access", 5, "transport.core"),
        ("transport.proview", "ProView access", 5, "transport.proview"),
    ):
        rows.append(
            FeatureDefinition(
                key,
                title,
                Implementation.BLOCKED,
                phase,
                FeatureBinding(None, None, (("opcode", None),), ("opcode",)),
                gaps=(gap,),
            )
        )
    return tuple(rows)


FEATURES = _features()
FEATURES_BY_KEY = {feature.key: feature for feature in FEATURES}


@cache
def app_inventory() -> dict[str, Any]:
    """Read the generated provenance index only when requested, never for dispatch."""
    return json.loads(Path(__file__).with_name("app_inventory.json").read_text())
