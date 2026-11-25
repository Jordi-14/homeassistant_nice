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
    HA_NATIVE = "ha_native"
    MANUFACTURER_APP = "manufacturer_app"


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
        "ecosystem.core_execution", "Cloud records do not verify CORE execution",
        "Scene/rule/timer REST records can be cloud copies of CORE tables. Current device tables, runtime activation and schedule timezone are not established by those records.",
        "Complete a read-only CORE table/runtime adapter with verified identity, table versions and schedule semantics. Never execute or mirror a cloud copy to infer its state.",
    ),
    EvidenceGap(
        "ecosystem.device_adapter", "Additional device categories need their control/state adapters",
        "Welcome AutomationType identifies lights, relays, electrical locks and screens, but the current gate adapter does not establish their telemetry, commands or transport.",
        "Implement the category's verified native HA domain adapter with state/command fixtures and required transport. Do not present it as a garage gate or invent actuator state.",
    ),

    EvidenceGap('installer.bus_discovery', 'Full BusT4 discovery needs scoped profile decoding', 'Pro T4DiscoveryProcedure broadcasts profiles then reads controller, radio and power-unit identities; native framing/decoders and endpoint ownership are not complete.', 'Implement profile broadcast, reply addressing/continuation and the required profile decoders together. NHK INFO inventory is a separate supported subset.'),
    EvidenceGap('installer.commissioning', 'Model-specific commissioning and learning sequences are incomplete', 'App InstallationProcedure models/views and InstallationFinder selection are not fully recovered; start-command symbols do not establish motor selection, terminal progress or abort semantics.', 'Recover the complete model sequence, physical prerequisites, progress/abort and endpoint verification. Keep HA animation calibration separate.'),
    EvidenceGap('installer.radio_memory', 'Receiver memory layout and private-code handling are incomplete', 'T4_RADIO_RD Memory fields are recovered, but model-specific rows may contain rolling codes, certificates and identifiers decoded by native code.', 'Complete scoped receiver discovery, paging, row codecs and allowlisted redaction before reading or retaining memory.'),
    EvidenceGap('installer.radio_mutation', 'Remote and receiver mutations need complete receiver-specific contracts', 'RadioSetCodeProcedure, RadioCodesReadingProcedureObject and certificate/reset procedures depend on receiver modes, exact targets and native payloads.', 'Validate physical consent, credentials/certificates, one-send writes, readback and interruption/recovery before enabling enrollment, edit, removal or receiver reset.'),
    EvidenceGap('installer.backup_restore', 'App-compatible backup and restore are not verified', 'Read-only parameter observations omit receiver memory, indexed/native structures and ordered restoration/ownership rules.', 'Recover complete app manifests, integrity checks, model/hardware/ownership compatibility and ordered restore/recovery. Never restore by replaying cached values.'),
    EvidenceGap('installer.replacement', 'Controller board replacement needs verified motor/logic state', 'The recovered spare-parts sequence resets motor then logic modules and may change T4Source. Native automation-state decoding and ownership transitions are missing.', 'Complete model-specific state checks, rebinding and interrupted-transfer recovery; do not infer force-adaptation reset from position search.'),
    EvidenceGap('installer.reset', 'Reset effects and re-provisioning are not fully verified', 'NHK factory/configuration/Wi-Fi resets differ; CTRL_RESET can simply clear a prior command. Their names are not interchangeable reset opcodes.', 'Recover each exact scope, ownership, data-loss effects, completion and re-provisioning/re-pairing path before enabling it.'),
    EvidenceGap('installer.firmware_catalog', 'Compatible vendor firmware discovery requires validated vendor access', 'Pro ControlUnitFirmwareApi records include model/board, published/test/deleted and file metadata, but authorized catalog and image provenance are not established.', 'Complete authenticated catalog binding, eligibility, exact hardware matching and authenticated artifact provenance. Do not invent latest-version availability.'),
    EvidenceGap('installer.interface_firmware', 'Module firmware image authentication and interrupted OTA recovery are unresolved', 'The app uploads 1024-byte chunks via /wiced_ota_server/upgrade_chunk.html, distinct from NHK BOOT_1. Vendor image verification, target binding and ambiguous-offset reconciliation are incomplete.', 'Verify image provenance and compatibility, local target/power, acknowledged chunk offsets and post-update/recovery behavior. Do not copy blind chunk retry logic.'),
    EvidenceGap('installer.controller_firmware', 'Controller programming needs authenticated images and complete recovery', 'NHK T4_UPDATE BOOT_1 read requests are recovered. FILE_WRITE/START_BOOT/START_PROGRAMMING do not establish authenticated image compatibility or safe resume/abort for each controller.', 'Validate vendor images, exact controller/board targeting, staged CRC/status, power/session exclusivity, interruption recovery and final firmware verification before enabling writes.'),
    EvidenceGap('installer.host_adapter', 'Phone/installer host access has no validated HA adapter', 'The exports contain phone NFC, ProView and native installer paths whose host/hardware/session contracts are not available to this NHK integration.', 'Supply a supported HA host/hardware transport with verified framing, permissions, lifecycle and fixtures. Do not silently send these operations over the NHK socket.'),

    EvidenceGap(
        "setup.wnc_recovery",
        "Wi-Fi configuration needs verified AP state and recovery",
        "WNC INFO/SCAN and CONFIG fields are recovered, but AP-state and target identity validation plus operational rediscovery/rollback are not verified against complete responses.",
        "Complete the provisioning adapter state machine with sanitized INFO/SCAN/CONFIG replies, target binding, success detection and a recoverable interrupted-write path. CONFIG must stay blocked until all prerequisites pass.",
    ),
    EvidenceGap(
        "setup.ownership_removal",
        "Physical pairing deletion is unverified; cloud ownership stays in the app",
        "USER_NEW approval and USER_EDIT permissions are recovered; disabling a paired record is not physical deletion, and module administrator is not cloud ownership.",
        "Recover physical pairing removal, consent, sole-administrator and completion/recovery rules before enabling deletion. Cloud ownership is deliberately managed in the manufacturer app, separate from HA permissions. Existing access is preserved.",
    ),
    EvidenceGap(
        "setup.cloud_registration",
        "Explicit cloud binding registration is unresolved",
        "REGISTER appears in the app, but its ownership, authorization and resulting cloud binding are not established for this integration.",
        "Recover and validate the complete registration workflow and readback. Do not call REGISTER during pairing, import or upgrade.",
    ),

    EvidenceGap(
        "diagnostics.receiver_layouts",
        "Receiver event layouts and redaction are incomplete",
        "Native receiver payloads can contain remote identifiers, rolling codes or keys; their full scoped decoder is not established.",
        "Recover the receiver-specific parser and fixture-backed redaction before retaining any payload. Known controller channel events remain numeric/labelled evidence only.",
    ),
    EvidenceGap(
        "diagnostics.advanced_history",
        "Advanced history requires controller-specific INFO decoding",
        "04/DB is not universally an AdvancedDiagnostic byte array; relation to 04/DA slots is unknown.",
        "Use a complete supported scalar/indexed INFO format or recover the model-specific structured decoder.",
    ),
    EvidenceGap(
        "diagnostics.inverter_format",
        "Inverter hardware descriptor layout is missing",
        "The shared hardware decoder needs the controller's complete 16-bit availability/scale descriptors.",
        "Capture matching INFO and GET and validate them against the app's inverter screen.",
    ),
    EvidenceGap(
        "diagnostics.automation_structure",
        "Automation state structure layout is unverified",
        "The native T4AutomationState layout and format mapping are incomplete in the supplied evidence.",
        "Recover the exact structure, per-motor fields and app rendering with sanitized fixtures.",
    ),
    EvidenceGap(
        "diagnostics.other_layouts",
        "Other/visual diagnostic layouts are unresolved",
        "04/D5 and 04/D6 names do not establish their complete structure or field semantics.",
        "Recover native decoding or matching app INFO/value fixtures before enabling a reader.",
    ),
    EvidenceGap(
        "diagnostics.test_history",
        "Test-history layouts are unverified",
        "04/DD and 04/DE do not have established record identity, ordering and timestamp layouts.",
        "Recover and validate complete history records, without starting a controller test procedure.",
    ),
    EvidenceGap(
        "diagnostics.psu_transport",
        "Dedicated PSU/battery profile access remains unresolved",
        "App PSU symbols do not establish the target transport, addressing and complete telemetry layout.",
        "Complete the device adapter and source-backed power/battery decoder; NHK battery events remain reported codes, not invented percentages.",
    ),
    EvidenceGap(
        "diagnostics.maintenance_procedure",
        "Maintenance reset procedure is not verified",
        "A 04/B4 symbol does not establish execution payload, permissions or safe completion/recovery.",
        "Recover the complete procedure under the shared procedure and authorization contracts.",
    ),
    EvidenceGap(
        "controller.menu_wire",
        "Controller menu permissions need a verified wire decoder",
        "The Java T4MenuEntry permission fields are recovered, but the binary STD_MENU decoder is native. Installer and ordinary-user rights cannot be inferred from INFO ranges.",
        "Recover menu framing/continuation, native record decoding and account-role selection; test explicit denial before enabling additional writes.",
    ),
    EvidenceGap(
        "controller.parameter_format",
        "Controller value format or operation is not verified",
        "A symbol/register alone does not establish its type, scale, unit or procedure semantics.",
        "Verify dynamic INFO and matching value fixtures, then add a scoped decoder and reviewed binding.",
    ),
    EvidenceGap(
        "controller.binding_map",
        "App parameter has no cross-checked wire binding",
        "The exported app ID cannot be used as a DMP register. The research dump contains unresolved and contradictory pairs.",
        "Cross-check the actual native map or an app trace against the Java symbol before enabling access.",
    ),
    EvidenceGap(
        "controller.index_identity",
        "Indexed element identities and writes remain unverified",
        "An index is kept separate but is not assumed to be motor 1 or motor 2; model-specific layouts and write permissions are missing.",
        "Recover the model-specific index-to-motor/leaf mapping, units and SET contract with sanitized fixtures.",
    ),
    EvidenceGap(
        "controller.action_binding",
        "Additional Pro action binding is not verified",
        "The Welcome action codes are implemented; extra Pro enum IDs and a partially inconsistent native extraction do not establish executable codes.",
        "Recover exact command encodings and device authorization; retain explicit unsupported and denied outcomes.",
    ),
    EvidenceGap(
        "native.t4_library.pro_2_18",
        "Pro native decoder audit and version association pending",
        "An arm64 libnative-lib.so and Pro split APK were supplied on 2026-11-25; their library bytes match. Exact app-version association, native contracts and hardware fixtures remain unverified.",
        "Verify the library against the Pro app version, audit native mappings and add sanitized request/reply fixtures before enabling bindings.",
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
        "CORE actuation/domain lifecycle remains unvalidated",
        "CORE inherits NHK authentication and source-backed TABLE_READ inspection is implemented. Continuous device discovery, category-specific control/state codecs and complete actuator lifecycle remain unvalidated; the gate adapter cannot substitute for them.",
        "Complete CORE device-domain, permissions, state/event and actuation lifecycle with scoped fixtures. Keep the separate count-only table reader read-only.",
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
        if self.implementation in {Implementation.HA_NATIVE, Implementation.MANUFACTURER_APP}:
            owner = "native Home Assistant" if self.implementation is Implementation.HA_NATIVE else "the manufacturer app"
            raise FeatureBlocked(f"{self.title} is managed by {owner}; no duplicate resource operation is provided")
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
            Implementation.PARTIAL if setting.requires_menu_permission else implemented,
            3 if setting.requires_menu_permission else 1,
            FeatureBinding(
                "setting",
                setting.key,
                (("group", setting.group), ("parameter", setting.parameter)),
                ("group", "parameter"),
            ),
            evidence=("protocol/t4/settings.py; controller INFO and parameter engine",),
            gaps=("controller.menu_wire",) if setting.requires_menu_permission else (),
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
        ("cloud.organization", "HA-native boundary and Nice automation visibility", 7),
    ):
        rows.append(
            FeatureDefinition(
                key,
                title,
                Implementation.PARTIAL
                if phase in (3, 4, 5, 6, 7)
                else Implementation.PLANNED,
                phase,
                gaps=(
                    "controller.menu_wire",
                    "controller.index_identity",
                    "controller.binding_map",
                )
                if key == "controller.configuration"
                else ("setup.wnc_recovery", "setup.ownership_removal", "setup.cloud_registration", "runtime.connection.code1")
                if key == "setup.access"
                else ('installer.bus_discovery', 'installer.commissioning', 'installer.radio_memory', 'installer.radio_mutation', 'installer.backup_restore', 'installer.replacement', 'installer.reset', 'installer.firmware_catalog', 'installer.interface_firmware', 'installer.controller_firmware', 'installer.host_adapter')
                if key == "installer.lifecycle"
                else ("ecosystem.core_execution", "ecosystem.device_adapter")
                if key == "cloud.organization"
                else tuple(gap.key for gap in GAPS if gap.key.startswith("diagnostics."))
                if key == "diagnostics.complete"
                else (),
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
        ("transport.core", "CORE actuator integration", 5, "transport.core"),
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
    rows.append(
        FeatureDefinition(
            "diagnostics.incident_timeline",
            "Incident evidence timeline",
            implemented,
            4,
            FeatureBinding("snapshot", "incident_timeline"),
            evidence=("incidents.py; bounded sanitized receipt-ordered evidence",),
        )
    )
    from .diagnostic_runtime import DIAGNOSTICS

    for definition in DIAGNOSTICS:
        rows.append(
            FeatureDefinition(
                "diagnostics.screen." + definition.key,
                definition.title,
                Implementation.IMPLEMENTED
                if definition.decoder is not None
                else Implementation.BLOCKED,
                4,
                FeatureBinding("diagnostic", definition.key)
                if definition.decoder is not None
                else None,
                gaps=(definition.gap,) if definition.gap else (),
                evidence=(
                    "diagnostic_runtime.py; reviewed app screen/decoder contract",
                ),
            )
        )
    from .installer_catalog import PROCEDURES

    rows.extend(
        FeatureDefinition(
            "installer." + procedure.key, procedure.title,
            implemented if procedure.state_machine_verified and not procedure.gaps else Implementation.BLOCKED,
            6, FeatureBinding("installer", procedure.key) if procedure.state_machine_verified else None,
            gaps=procedure.gaps, evidence=procedure.evidence,
        )
        for procedure in PROCEDURES
    )
    from .ecosystem_catalog import DISPOSITIONS

    rows.append(FeatureDefinition("ecosystem.inspection", "Read-only Nice cloud inspection", implemented, 7, FeatureBinding("options", "ecosystem"), evidence=("Welcome 1.3.1 NhkApiService GET macro-user/scenes/rules",)))
    rows.append(FeatureDefinition("ecosystem.report", "HA-native boundary and inspection report", implemented, 7, FeatureBinding("snapshot", "ecosystem")))
    rows.extend(
        FeatureDefinition("ecosystem." + item.key, item.title, Implementation(item.disposition), 7,
                          gaps=(item.gap,) if item.gap else (), evidence=(item.explanation,))
        for item in DISPOSITIONS if item.key != "inspection"
    )
    rows.append(FeatureDefinition("core.tables", "Read-only CORE table snapshot", implemented, 8, FeatureBinding("core_inspection", "tables"), evidence=("Welcome CoreProtocol TABLE_READ and version-bracketed count-only reader",)))
    rows.append(FeatureDefinition("parity.report", "Final capability dispositions and validation summary", implemented, 8, FeatureBinding("snapshot", "parity")))
    return tuple(rows)


FEATURES = _features()
FEATURES_BY_KEY = {feature.key: feature for feature in FEATURES}


@cache
def app_inventory() -> dict[str, Any]:
    """Read the generated provenance index only when requested, never for dispatch."""
    return json.loads(Path(__file__).with_name("app_inventory.json").read_text())
