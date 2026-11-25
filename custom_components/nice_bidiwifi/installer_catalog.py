"""Installer procedures are semantic contracts, never executable app enum IDs."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class InstallerStep:
    key: str
    handler: str | None
    mutates: bool = False
    timeout: float = 20
    cancellable_before: bool = True


@dataclass(frozen=True, slots=True)
class InstallerProcedure:
    key: str
    title: str
    steps: tuple[InstallerStep, ...]
    prerequisites: tuple[str, ...]
    completion: str | None
    recovery: str | None
    gaps: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    state_machine_verified: bool = False

    @property
    def mutates(self):
        return any(step.mutates for step in self.steps)

    def preflight(self, handlers):
        from .feature_catalog import FeatureBlocked, GAPS_BY_KEY

        missing = [
            step.key
            for step in self.steps
            if step.handler not in handlers
            or not callable(handlers.get(step.handler))
            or not isinstance(step.timeout, (int, float))
            or isinstance(step.timeout, bool)
            or not math.isfinite(step.timeout)
            or not 0 < step.timeout <= 300
        ]
        if (
            self.gaps
            or not self.state_machine_verified
            or not self.steps
            or len(self.steps) > 32
            or len({step.key for step in self.steps}) != len(self.steps)
            or not self.completion
            or not self.recovery
            or missing
        ):
            reason = "; ".join(GAPS_BY_KEY[g].summary for g in self.gaps)
            raise FeatureBlocked(
                f"{self.title}: {reason or 'complete bindings, completion and recovery are required'}"
            )

    def report(self):
        return {
            "title": self.title,
            "implemented": self.state_machine_verified and not self.gaps,
            "mutates": self.mutates,
            "steps": [step.key for step in self.steps],
            "prerequisites": self.prerequisites,
            "completion": self.completion,
            "recovery": self.recovery,
            "gaps": self.gaps,
            "evidence": self.evidence,
        }


def blocked(key, title, gap, steps, prerequisites, evidence):
    return InstallerProcedure(
        key,
        title,
        tuple(InstallerStep(step, None, True) for step in steps),
        prerequisites,
        None,
        None,
        (gap,),
        (evidence,),
    )


PROCEDURES = (
    InstallerProcedure(
        "inspect_devices",
        "Inspect advertised devices",
        (InstallerStep("read_info", "inventory"),),
        (),
        "Fresh bounded NHK INFO inventory; no complete BusT4 scan is claimed",
        "Repeat the read after checking connectivity",
        evidence=("NHK INFO Devices/Device",),
        state_machine_verified=True,
    ),
    InstallerProcedure(
        "inspect_boot",
        "Inspect existing bootloader state",
        (
            InstallerStep("read_info", "inventory"),
            InstallerStep("file_info", "file_info"),
            InstallerStep("boot_devices", "boot_devices"),
            InstallerStep("programming_status", "programming_status"),
        ),
        ("administrator", "stationary", "local", "advertised_interface_id"),
        "Read-only BOOT_1 observations; no controller is put into boot mode",
        "Repeat reads only; programming state is observed and never automatically modified",
        evidence=(
            "Pro 2.18 FirmwareManagerImpl; T4UpdateBoot1Requests; FwInterfaceResponse",
        ),
        state_machine_verified=True,
    ),
    InstallerProcedure(
        "snapshot_configuration",
        "Snapshot observed configuration",
        (InstallerStep("capture_observations", "snapshot"),),
        (),
        "Incomplete read-only observation snapshot, explicitly not a restorable app backup",
        "Capture again; no restore or replay endpoint exists",
        evidence=("ParameterRepository; reviewed configuration GET allowlist",),
        state_machine_verified=True,
    ),
    InstallerProcedure(
        "restart_interface",
        "Restart the Wi-Fi interface",
        (InstallerStep("send_restart_once", "restart", True, 45, False),),
        ("administrator", "stationary", "reboot_capability"),
        "CHANGE acknowledgement only; subsequent connectivity is not proof of a physical reboot",
        "Never replay an uncertain restart; inspect device health and explicitly acknowledge recovery",
        evidence=("Shared Wi-Fi CHANGE Interface/Commands/Reboot",),
        state_machine_verified=True,
    ),
    blocked(
        "discover_bus",
        "Discover BusT4 peripherals",
        "installer.bus_discovery",
        ("profile_broadcast", "scoped_identity_reads", "decode_profiles"),
        ("installer_adapter",),
        "Pro T4DiscoveryProcedure.profileBroadcast and model-specific profile decoders",
    ),
    blocked(
        "search_devices",
        "Program controller device search",
        "installer.commissioning",
        ("validate_model_sequence", "start_search", "observe_completion"),
        ("installer", "stationary", "physical_setup"),
        "CTRL_SEARCH_DEVICES is a command symbol, not a verified sequence",
    ),
    blocked(
        "search_bluebus",
        "Program BlueBus search",
        "installer.commissioning",
        ("validate_model_sequence", "start_bluebus_search", "observe_completion"),
        ("installer", "stationary", "physical_setup"),
        "CTRL_SEARCH_BLUEBUS_DEVICES and app installation model views",
    ),
    blocked(
        "learn_positions",
        "Controller position learning",
        "installer.commissioning",
        ("validate_motor_ownership", "model_learning_sequence", "verify_endpoints"),
        ("installer", "physical_setup", "gate_visible"),
        "InstallationProcedure models/views; InstallationFinder native/incomplete mappings",
    ),
    blocked(
        "learn_force_map",
        "FG01H force-map learning",
        "fg01h.force_map_learning",
        ("validate_fg01h", "learn_force_map", "verify_adaptation"),
        ("installer", "physical_setup"),
        "Force-map procedure remains independent of HA animation calibration",
    ),
    InstallerProcedure(
        "radio_inspect",
        "Inspect the first eight receiver memory slots",
        (InstallerStep("read_redacted_page", "radio_page", timeout=90),),
        ("administrator", "stationary", "pro_controls"),
        "Eight addressed OXI memory replies with verified position echoes; private codes discarded; not a complete inventory",
        "Retry the read after checking connectivity; no receiver setting is changed",
        evidence=(
            "Pro RadioQueryMemory; native QueryMemoryResult; strict position/length validation",
        ),
        state_machine_verified=True,
    ),
    blocked(
        "radio_enroll",
        "Enroll a physical remote",
        "installer.radio_mutation",
        ("physical_consent", "receiver_mode", "write_remote", "verify_remote"),
        ("installer", "receiver_adapter", "physical_remote"),
        "RadioSetCodeProcedure, certificates and rolling-code state",
    ),
    blocked(
        "radio_edit",
        "Edit receiver channels or remote permissions",
        "installer.radio_mutation",
        ("read_target", "authorize_channel", "write_once", "readback"),
        ("installer", "receiver_adapter"),
        "Receiver target identity and payload encoding must be established together",
    ),
    blocked(
        "radio_remove",
        "Remove a physical remote",
        "installer.radio_mutation",
        ("identify_exact_remote", "remove_once", "verify_removal"),
        ("installer", "receiver_adapter"),
        "Deleting a radio memory record is distinct from NHK paired-user access",
    ),
    blocked(
        "radio_reset",
        "Reset a receiver",
        "installer.radio_mutation",
        ("verify_receiver_scope", "reset_once", "verify_receiver_recovery"),
        ("installer", "receiver_adapter"),
        "RadioReceiverResetProcedure is receiver-specific, not a generic gate command",
    ),
    blocked(
        "backup_configuration",
        "Create a complete controller backup",
        "installer.backup_restore",
        ("native_snapshot", "integrity_and_ownership", "backup_manifest"),
        ("installer_adapter",),
        "App controller/radio backup formats exceed the current parameter observations",
    ),
    blocked(
        "restore_configuration",
        "Restore a controller backup",
        "installer.backup_restore",
        ("validate_manifest_and_target", "ordered_restore", "verify_and_recover"),
        ("installer", "physical_setup"),
        "No generic replay of parameter GET values is a validated restore",
    ),
    blocked(
        "replace_controller",
        "Transfer configuration or replace controller boards",
        "installer.replacement",
        (
            "motor_module_state",
            "logic_module_state",
            "rebind_target",
            "verify_replacement",
        ),
        ("installer", "physical_setup"),
        "T4ResetToSparePartsProcedure changes motor then logic modules and can change T4Source",
    ),
    blocked(
        "reset_controller",
        "Reset controller configuration",
        "installer.reset",
        ("validate_reset_semantics", "reset_once", "verify_recovery"),
        ("installer", "physical_setup"),
        "CTRL_RESET in ListDeviceCommand clears a previous command; it is not proof of factory-reset semantics",
    ),
    blocked(
        "reset_interface",
        "Factory-reset the Wi-Fi interface",
        "installer.reset",
        ("verify_ownership", "reset_once", "reprovision_and_repair"),
        ("administrator", "physical_setup"),
        "NHK FactoryReset, ConfigurationReset and WifiConfigurationReset have distinct effects",
    ),
    blocked(
        "firmware_catalog",
        "Discover compatible vendor firmware",
        "installer.firmware_catalog",
        ("authorize_catalog", "match_board_and_model", "verify_provenance"),
        ("vendor_account",),
        "ControlUnitFirmwareApi published/test/deleted, board/model and file metadata",
    ),
    blocked(
        "update_interface_firmware",
        "Update module firmware",
        "installer.interface_firmware",
        (
            "validate_vendor_image",
            "verify_local_target",
            "upload_chunks",
            "verify_version_and_recover",
        ),
        ("administrator", "local", "stable_power"),
        "InterfaceFirmwareUpgradeHelper 1024-byte HTTP OTA chunks; missing image authentication and interrupted-upload reconciliation",
    ),
    blocked(
        "update_controller_firmware",
        "Program controller firmware",
        "installer.controller_firmware",
        (
            "validate_vendor_image",
            "stage_file",
            "enter_boot",
            "program",
            "verify_and_recover",
        ),
        ("installer", "local", "stable_power"),
        "FirmwareManager BOOT_1 FILE_WRITE/START_BOOT/START_PROGRAMMING; checksums do not prove vendor authenticity",
    ),
)
PROCEDURES_BY_KEY = {p.key: p for p in PROCEDURES}

ADAPTERS = (
    {
        "key": "shared_nhk",
        "available": True,
        "scope": "NHK INFO and bounded BOOT_1 reads, existing shared Wi-Fi restart",
    },
    {"key": "bus_installer", "available": False, "gap": "installer.bus_discovery"},
    {"key": "radio_receiver", "available": False, "gap": "installer.radio_memory"},
    {"key": "core", "available": False, "gap": "transport.core"},
    {"key": "proview", "available": False, "gap": "transport.proview"},
    {"key": "phone_nfc", "available": False, "gap": "installer.host_adapter"},
)
