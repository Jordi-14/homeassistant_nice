"""Final source dispositions. This evidence index is never an operation dispatcher."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from functools import cache
import json
from pathlib import Path

from .controller_catalog import PARAMETERS_BY_KEY
from .feature_catalog import FEATURES_BY_KEY, GAPS_BY_KEY


@dataclass(frozen=True, slots=True)
class CoverageFamily:
    key: str
    disposition: str
    features: tuple[str, ...]
    gaps: tuple[str, ...]
    reason: str
    boundary: str


FAMILIES = (
    CoverageFamily(
        "controller",
        "supported_subset",
        ("controller.configuration",),
        ("controller.menu_wire", "controller.index_identity", "controller.binding_map"),
        "Existing controller entities and parameter engine cover reviewed bindings, types and permissions; generic app configuration browsers do not prove every native parameter layout.",
        "ParameterRepository.prepare_write / ControllerConfiguration.view",
    ),
    CoverageFamily(
        "diagnostics",
        "supported_subset",
        ("diagnostics.complete",),
        ("diagnostics.automation_structure", "diagnostics.receiver_layouts"),
        "Shared diagnostic/history readers expose verified layouts and suppress unknown/private payloads. Model-specific structures remain separate named gaps.",
        "DiagnosticRuntime.view / async_read",
    ),
    CoverageFamily(
        "module_access",
        "supported_subset",
        ("setup.access",),
        (
            "runtime.connection.code1",
            "setup.ownership_removal",
            "setup.cloud_registration",
        ),
        "Direct enrollment, verification and paired-user inspection/approval are implemented; role checks and exact targets govern writes. Rejection is not permission to reset or register a device.",
        "ConfigFlow pairing / AccessManagement / client change_user",
    ),
    CoverageFamily(
        "wifi_provisioning",
        "blocked",
        ("setup.access",),
        ("setup.wnc_recovery",),
        "WNC information/scan and CONFIG fields are recovered; AP identity/state and operational recovery are not verified. No Wi-Fi mutation is enabled.",
        "ProvisioningMutationAdapter.configure",
    ),
    CoverageFamily(
        "core",
        "supported_subset",
        ("core.tables", "transport.core"),
        ("transport.core", "ecosystem.core_execution"),
        "CORE shares NHK session framing and has a source-backed count-only TABLE_READ adapter. Full actuator domains, current execution and schedule semantics are not inferred from table counts.",
        "CoreTableReader / generic CORE actuation remains blocked",
    ),
    CoverageFamily(
        "receiver",
        "blocked",
        ("installer.radio_inspect", "installer.radio_enroll", "installer.radio_edit"),
        ("installer.radio_memory", "installer.radio_mutation"),
        "Physical receiver/remote workflows require scoped discovery, native memory/code/certificate layouts, channel permissions and recovery. App remote widgets are not wire commands.",
        "InstallerProcedure.preflight / receiver adapter boundary",
    ),
    CoverageFamily(
        "commissioning",
        "blocked",
        (
            "installer.search_devices",
            "installer.search_bluebus",
            "installer.learn_positions",
        ),
        ("installer.commissioning", "controller.index_identity"),
        "Model-specific commissioning requires the complete profile sequence, motor ownership, terminal/abort state and recovery. It is separate from HA animation calibration.",
        "InstallerProcedure.preflight / ProcedureRunner",
    ),
    CoverageFamily(
        "firmware",
        "supported_subset",
        (
            "installer.inspect_boot",
            "installer.firmware_catalog",
            "installer.update_controller_firmware",
        ),
        (
            "installer.firmware_catalog",
            "installer.controller_firmware",
            "installer.interface_firmware",
        ),
        "Read-only BOOT_1 inspection is implemented. Staging/programming/HTTP OTA need authenticated images, exact board compatibility and interrupted-write recovery.",
        "FirmwareAdapter.validate_vendor_image / installer preflight",
    ),
    CoverageFamily(
        "backup",
        "blocked",
        (
            "installer.backup_configuration",
            "installer.restore_configuration",
            "installer.replace_controller",
        ),
        ("installer.backup_restore", "installer.replacement"),
        "Observed scalar snapshots are not complete app backups. Ordered restore, board/motor ownership and target rebinding must be recovered together.",
        "InstallerProcedure.preflight",
    ),
    CoverageFamily(
        "reset",
        "blocked",
        (
            "installer.reset_controller",
            "installer.reset_interface",
            "installer.radio_reset",
        ),
        ("installer.reset", "installer.radio_mutation"),
        "Command reset, factory/configuration reset, network reset and receiver reset are distinct. No reset is dispatched from a name or ordinal.",
        "InstallerProcedure.preflight",
    ),
    CoverageFamily(
        "host_adapter",
        "external_dependency",
        ("transport.proview", "installer.discover_bus"),
        ("installer.host_adapter", "transport.proview", "installer.bus_discovery"),
        "Phone NFC/ProView/installer hardware paths need a supported host transport and device/certificate lifecycle. Android presentation/hardware APIs are not an HA backend driver.",
        "BlockedAccessAdapter / installer adapter preflight",
    ),
    CoverageFamily(
        "device_categories",
        "blocked",
        ("ecosystem.device_adapters",),
        ("ecosystem.device_adapter",),
        "Additional physical sensors/actuators require their own verified state, command and transport adapters. Category names alone cannot instantiate fake gate/light/lock entities.",
        "CategoryAdapter / require_gate_adapter",
    ),
    CoverageFamily(
        "ha_organization",
        "ha_native",
        ("ecosystem.areas", "ecosystem.dashboards"),
        (),
        "HA owns organization and presentation; the integration does not create or synchronize app homes, areas or dashboards.",
        "FeatureDefinition.require_implemented deliberate HA disposition",
    ),
    CoverageFamily(
        "ha_automation",
        "ha_native",
        ("ecosystem.scenes", "ecosystem.schedules", "ecosystem.notifications"),
        (),
        "Use native HA scenes/scripts, schedules, rules and notification actions. No app/cloud automation is mirrored or executed.",
        "FeatureDefinition.require_implemented deliberate HA disposition",
    ),
    CoverageFamily(
        "vendor_account",
        "manufacturer_app",
        ("ecosystem.sharing", "ecosystem.service_linking"),
        (),
        "Vendor accounts, invitations, cloud ownership and service linking stay in the manufacturer app and are not HA/module permissions.",
        "FeatureDefinition.require_implemented manufacturer disposition",
    ),
    CoverageFamily(
        "vendor_resources",
        "manufacturer_app",
        ("ecosystem.vendor_resources",),
        (),
        "Manage existing Nice resources in the manufacturer app. Read-only inspection is separate from editing or executing them.",
        "require_inspection rejects resource operations",
    ),
    CoverageFamily(
        "cloud_inspection",
        "implemented_read_only",
        ("ecosystem.inspection", "ecosystem.report"),
        (),
        "Bounded, explicitly requested accessory-scoped REST inspection retains safe summaries and no account session. Cloud copies do not prove device execution.",
        "NiceCloudAutomationInspector fixed GET allowlist",
    ),
    CoverageFamily(
        "app_prototype",
        "app_prototype",
        ("parity.report",),
        (),
        "Supplied alarm/demo screens use DummyFlowsManager, DummyAlarmStatus or explicit dummy initialization; they establish no operational device protocol to reproduce.",
        "Evidence only; no operation is registered",
    ),
    CoverageFamily(
        "app_catalog",
        "manufacturer_app",
        ("parity.report",),
        (),
        "App manuals, commercial catalogs, support/assistance and app-update UI remain manufacturer-app content, not a device operation or HA component update service.",
        "Evidence only; no operation is registered",
    ),
    CoverageFamily(
        "phone_presentation",
        "ha_native",
        ("ecosystem.dashboards", "ecosystem.permissions"),
        (),
        "Android navigation, input widgets, phone permissions, language/appearance and local app-database presentation have native HA/Companion-app equivalents, not a server-side device command.",
        "Evidence only; no Android UI/permission engine is installed",
    ),
    CoverageFamily(
        "shared_evidence",
        "supporting_evidence",
        ("parity.report",),
        (),
        "Shared model, utility, event-bus, serializer or container class is supporting evidence, not an independent app capability. Source methods and hashes remain indexed; it cannot dispatch an operation.",
        "Evidence index is never used for dispatch",
    ),
)
FAMILIES_BY_KEY = {family.key: family for family in FAMILIES}


def classify_source(row, facts):
    symbol = row["symbol"]
    low = symbol.lower()
    name = symbol.rsplit(".", 1)[-1]
    if name in {"R", "R2", "BuildConfig"}:
        return (
            "generated_reference",
            (),
            (),
            "Generated resource/build constants, not an app capability.",
            "shared_evidence",
        )
    if row["kind"] == "controller_command":
        definition = PARAMETERS_BY_KEY.get(symbol.lower())
        if (
            row.get("app_command_id") is not None
            and row["app_command_id"] < 0
            or symbol in {"UNDEFINED", "NONE"}
        ):
            return (
                "non_capability",
                (),
                (),
                "Sentinel/undefined enum item is not an executable device function.",
                "controller",
            )
        if definition is not None:
            if definition.reviewed_action:
                return (
                    "implemented",
                    ("t4." + definition.reviewed_action,),
                    (),
                    "Reviewed action wire binding with advertised permission/support checks.",
                    "controller",
                )
            if any(
                token in symbol
                for token in ("PASSWORD", "KEY1_", "KEY2_", "KEY3_", "PIN", "CERT")
            ):
                return (
                    "privacy_protected",
                    ("controller.configuration",),
                    (),
                    "Credential/certificate/security material is deliberately excluded from probing/export; no generic setter exists.",
                    "controller",
                )
            if definition.setting is not None:
                feature = "setting." + definition.setting.key
                if not definition.setting.requires_menu_permission:
                    return (
                        "implemented",
                        (feature,),
                        (),
                        "Reviewed existing scalar setting; runtime INFO, model and administrator checks still apply.",
                        "controller",
                    )
                return (
                    "supported_read_blocked_write",
                    (feature,),
                    ("controller.menu_wire",),
                    "Read metadata does not grant installer-menu write rights.",
                    "controller",
                )
            if definition.safe_read:
                return (
                    "supported_read_only",
                    ("controller.configuration",),
                    ("controller.parameter_format",),
                    "Read only when complete device INFO validates the scalar/indexed format; unknown formats remain blocked.",
                    "controller",
                )
        if symbol.startswith("PSU_"):
            return (
                "blocked",
                ("diagnostics.complete",),
                ("diagnostics.psu_transport",),
                "Dedicated PSU transport/addressing and telemetry layout must be established.",
                "device_categories",
            )
        if "RADIO" in symbol:
            return _family("receiver")
        if "SEARCH_" in symbol:
            return _family("commissioning")
        if "RESET" in symbol:
            return _family("reset")
        gap = (
            "controller.action_binding"
            if definition and definition.protocol == "DEP"
            else "controller.binding_map"
        )
        return (
            "blocked",
            ("controller.configuration",),
            (gap,),
            "App enum ID is not a verified wire binding, permission or complete state-changing procedure.",
            "controller",
        )
    # Reviewed phase-7 destinations are deliberate boundaries, never new writes.
    destination = row["destination"]
    if destination.startswith("ecosystem.") and destination in FEATURES_BY_KEY:
        feature = FEATURES_BY_KEY[destination]
        status = str(feature.implementation)
        if status in {"ha_native", "manufacturer_app"}:
            return (
                status,
                (destination,),
                (),
                "Reviewed HA-native/manufacturer-app boundary; no duplicate resource operation.",
                "ha_automation" if status == "ha_native" else "vendor_resources",
            )
        if destination == "ecosystem.inspection":
            return _family("cloud_inspection")
    if "nhk_core" in low:
        return _family("core")
    if any(
        x in low
        for x in (
            "alarmmanagement",
            "configuration.alarmunit",
            "dummyflows",
            "entity.alarmunit",
            "safetyandprotection",
        )
    ):
        return _family("app_prototype")
    if any(
        x in low for x in ("proview", "nfcmodule", "nfc.", "operakeys", "pln2pkeys")
    ):
        return _family("host_adapter")
    if any(
        x in low
        for x in (
            "firmware",
            "boot1",
            "bootloader",
            "fwdevice",
            "t4fileloaded",
            "updatestatus",
        )
    ):
        return _family("firmware")
    if any(
        x in low
        for x in (
            "manuals",
            "catalog",
            "assistance",
            "supportfragment",
            "tutorial",
            "tutorials",
        )
    ):
        return _family("app_catalog")
    if any(x in low for x in ("sparepart", "backup", "replacement", "restore")):
        return _family("backup")
    if "reset" in low:
        return _family("reset")
    if any(
        x in low
        for x in (
            "radio",
            "receiver",
            "transmitter",
            "remotecontrol",
            "remote_controller",
            "t4radi",
        )
    ):
        return _family("receiver")
    if any(
        x in low
        for x in (
            "installation",
            "quote",
            "commission",
            "t4procedures.scan",
            "t4_discovery",
            "searchpositions",
            "searchdevices",
        )
    ):
        return _family("commissioning")
    if any(
        x in low
        for x in ("wnc", "wificonfiguration", "wificonfig", "changewifinetwork")
    ):
        return _family("wifi_provisioning")
    if any(
        x in low
        for x in (
            "usersettings",
            "login.",
            "signup",
            "signin",
            "forgotpassword",
            "registration",
            "settings.profile",
            "settings.people",
            "telemanagement",
            "invitation",
            "redeemcode",
        )
    ):
        return _family("vendor_account")
    if any(
        x in low
        for x in (
            "userrepository",
            "tempuserrepository",
            "registrationinformationrepository",
            "telemanagementrepository",
        )
    ):
        return _family("vendor_account")
    if "automationrepository" in low:
        return _family("controller")
    if "dictionaryrepository" in low:
        return _family("shared_evidence")
    if "nhk.extra.device.http" in low:
        return _family("wifi_provisioning")
    if any(
        x in low for x in ("locationmanager", "onlocationprovider", "resourceprovider")
    ):
        return _family("phone_presentation")
    if "connectionhandler" in low:
        return _family("module_access")
    if any(
        x in low
        for x in ("rulerepository", "schedule_repository", "syncaccessorycloud")
    ):
        return _family("vendor_resources")
    if any(x in low for x in ("devicerepository", "sensorrepository")):
        return _family("device_categories")
    if symbol.startswith("com.nice.sdk.web.api") or low.startswith(
        "it.twospecials.networking"
    ):
        if "deviceconfigured" in low:
            return _family("wifi_provisioning")
        if any(
            x in low
            for x in (
                ".auth.",
                "password",
                "login",
                "token",
                "mail",
                ".company.",
                "installer",
                "credential",
                "permission",
            )
        ):
            return _family("vendor_account")
        if any(
            x in low
            for x in (
                "geocode",
                "countr",
                "currenc",
                "languag",
                "regions",
                "privacy",
                "handbook",
                "faq",
                "filemanagement",
                "intervention",
            )
        ):
            return _family("app_catalog")
        if name in {
            "BaseResponse",
            "HttpResponse",
            "HttpBaseResponse",
            "BaseCommandResponse",
            "ErrorListResponse",
            "DownloadFileResponse",
            "GetAllDictionariesVersionsResponse",
            "GetAllDiagnosticCauseCodesResponse",
        }:
            return _family("shared_evidence")
        return _family("vendor_resources")

    if any(
        x in low
        for x in (
            "notification",
            "weather",
            "geocode",
            "geolocation",
            "schedule",
            "scenario",
            "scene",
            "rules",
            "favourite",
            "favorite",
            "dashboard",
            "homeevent",
        )
    ):
        return _family("ha_automation")
    if any(
        x in low
        for x in (
            "nhk.protocol",
            "nhk.communication",
            "nhkconnectionlibrary",
            "nhk_wifi_accessory",
            "connection.wifi_interfaces",
            "connection.bonjour",
            "pairing",
            "credentials",
            "authentication",
            "verifyresponse",
            "useredit",
            "usernew",
        )
    ):
        return _family("module_access")
    if any(
        x in low
        for x in (
            "diagnostic",
            "history",
            "maintenance",
            "fault",
            "t4log",
            "battery",
            "power_supply",
            "inverter",
        )
    ):
        return _family("diagnostics")
    if any(
        x in low
        for x in (
            "sensorconfiguration",
            "entity.sensor",
            "inputrepository",
            "outputrepository",
            "deviceoutputs",
            "smartdevice",
            "bidirectional",
            "monodirectional",
        )
    ):
        return _family("device_categories")
    if any(
        x in low
        for x in (
            "libraryt4",
            "niceoview",
            "json_views",
            "controlunit",
            "control_unit",
            "configuration",
            "rxjavat4",
            "t4procedures",
            "connection.receivers",
            "connection.events",
        )
    ):
        return _family("controller")
    if any(
        x in low
        for x in (
            "settings.home",
            "devicesandenvironments",
            "environments",
            "environmentrepository",
            "homerepository",
            "homefragment",
            "homeviewmodel",
            "view.home",
        )
    ):
        return _family("ha_organization")
    if row["kind"] == "screen" or any(
        x in low
        for x in (
            "presentation.view",
            "presentation.utils",
            "connectivity_utils",
            "base_settings",
            "commons.widgets",
            "commons.adapters",
            "data.view_utils",
        )
    ):
        return _family("phone_presentation")
    if row["kind"] in {"request", "response", "procedure", "protocol", "repository"}:
        # Unmatched operation-bearing classes require a reviewed family route.
        return (
            "review_required",
            (),
            (),
            "No final operation-bearing family route has been established.",
            "shared_evidence",
        )
    return _family("shared_evidence")


def _family(key):
    family = FAMILIES_BY_KEY[key]
    return family.disposition, family.features, family.gaps, family.reason, key


def reconcile(rows, facts):
    records = []
    for row in rows:
        fact = facts[row["id"]]
        if fact["source_sha256"] != row["sha256"]:
            raise ValueError("Source evidence hash changed: " + row["id"])
        disposition, features, gaps, reason, family = classify_source(row, fact)
        if disposition == "review_required":
            raise ValueError("Unreconciled operation-bearing source: " + row["id"])
        if any(key not in FEATURES_BY_KEY for key in features) or any(
            key not in GAPS_BY_KEY for key in gaps
        ):
            raise ValueError("Unknown closure dependency: " + row["id"])
        records.append(
            {
                "id": row["id"],
                "app": row["app"],
                "version": row["version"],
                "source": row["source"],
                "sha256": row["sha256"],
                "original_kind": row["kind"],
                "effective_role": "protocol"
                if "nhk_core.CoreProtocol" in row["symbol"]
                else row["kind"],
                "disposition": disposition,
                "family": family,
                "features": list(features),
                "blocking_gaps": list(gaps),
                "reason": reason,
                "source_evidence": fact,
                "evidence_limit": "Static/source and named-runtime reconciliation; not universal hardware validation.",
            }
        )
    return {
        "schema": 1,
        "dispatch_source": False,
        "records": records,
        "families": [
            asdict(f) | {"features": list(f.features), "gaps": list(f.gaps)}
            for f in FAMILIES
        ],
    }


def summary(index):
    rows = index["records"]
    return {
        "schema": 1,
        "source_items": len(rows),
        "all_sources_dispositioned": True,
        "index_used_for_dispatch": False,
        "apps": dict(Counter(r["app"] for r in rows)),
        "dispositions": dict(Counter(r["disposition"] for r in rows)),
        "families": dict(Counter(r["family"] for r in rows)),
        "hardware_validation": "See validation_matrix.json; source closure does not assert all app methods or hardware variants work.",
    }


@cache
def parity_summary():
    return json.loads(Path(__file__).with_name("parity_summary.json").read_text())


@cache
def validation_matrix():
    return json.loads(Path(__file__).with_name("validation_matrix.json").read_text())


def validate_closure(index, matrix, root):
    allowed = {
        "implemented",
        "implemented_read_only",
        "supported_subset",
        "supported_read_only",
        "supported_read_blocked_write",
        "blocked",
        "external_dependency",
        "ha_native",
        "manufacturer_app",
        "app_prototype",
        "supporting_evidence",
        "privacy_protected",
        "non_capability",
        "generated_reference",
    }
    seen = set()
    for row in index["records"]:
        if row["id"] in seen or row["disposition"] not in allowed:
            raise ValueError("Duplicate or unfinished closure disposition")
        seen.add(row["id"])
        if (
            row["sha256"] != row["source_evidence"]["source_sha256"]
            or Path(row["source"]).is_absolute()
            or ".." in Path(row["source"]).parts
        ):
            raise ValueError("Invalid source provenance")
        if (
            row["disposition"]
            in {"blocked", "external_dependency", "supported_read_blocked_write"}
            and not row["blocking_gaps"]
        ):
            raise ValueError("Blocked capability has no precise gap")
        if not row["reason"] or row["family"] not in FAMILIES_BY_KEY:
            raise ValueError("Closure has no named family/reason")
    prototype = [r for r in index["records"] if r["family"] == "app_prototype"]
    if prototype and not any(
        r["source_evidence"]["prototype_markers"] for r in prototype
    ):
        raise ValueError("Prototype disposition has no direct source evidence")
    for gap in GAPS_BY_KEY.values():
        if not gap.missing or not gap.completion:
            raise ValueError("Gap has no completion contract")
    if matrix["universal_hardware_validation"] or matrix["stable_release_authorized"]:
        raise ValueError(
            "Beta closure cannot claim universal hardware or authorize stable"
        )
    for case in matrix["cases"]:
        if not case["limitation"] or not case["tests"]:
            raise ValueError("Validation claim lacks limits or tests")
        for test in case["tests"]:
            if (
                not test.startswith("tests/")
                or ".." in Path(test).parts
                or not (root / test).is_file()
            ):
                raise ValueError("Missing compatibility regression: " + test)
