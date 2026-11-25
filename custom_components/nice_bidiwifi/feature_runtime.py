"""Feature preflight, device access contracts and procedure extension boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .feature_catalog import (
    FEATURES,
    FEATURES_BY_KEY,
    GAPS_BY_KEY,
    FeatureBinding,
    FeatureBlocked,
    FeatureDefinition,
)
from .models.capabilities import ProductFamily
from .parameters import SupportState
from .protocol.t4.settings import SETTINGS_BY_REGISTER


@dataclass(frozen=True, slots=True)
class AccessContract:
    key: str
    families: frozenset[ProductFamily]
    routes: tuple[str, ...]
    implemented: bool
    gap: str | None = None

    def require(self) -> None:
        if not self.implemented:
            reason = GAPS_BY_KEY[self.gap].summary if self.gap else "implementation unavailable"
            raise FeatureBlocked("Blocked access adapter: " + reason)


SHARED_WIFI_ACCESS = AccessContract(
    "shared_wifi",
    frozenset({ProductFamily.BIDI_WIFI, ProductFamily.IT4_WIFI, ProductFamily.CU_WIFI}),
    ("local_tls", "relay_tls"),
    True,
)
CORE_ACCESS = AccessContract(
    "core", frozenset({ProductFamily.CORE}), (), False, "transport.core"
)
PROVIEW_ACCESS = AccessContract(
    "proview", frozenset({ProductFamily.PROVIEW}), (), False, "transport.proview"
)
ACCESS_CONTRACTS = (SHARED_WIFI_ACCESS, CORE_ACCESS, PROVIEW_ACCESS)


class AccessAdapter(Protocol):
    """Adapters retain the same target, lifecycle and single-session contract."""

    contract: AccessContract

    def dispatch(self, binding: FeatureBinding, payload: Mapping[str, Any]) -> Any: ...
    def close(self) -> None: ...


class BlockedAccessAdapter:
    """No transport is constructed for an unresolved device access path."""

    def __init__(self, contract: AccessContract) -> None:
        if contract.implemented:
            raise ValueError("Use a real adapter for an implemented contract")
        self.contract = contract

    def dispatch(self, binding: FeatureBinding, payload: Mapping[str, Any]) -> Any:
        self.contract.require()
        raise FeatureBlocked("Access adapter unresolved")

    def close(self) -> None:
        pass


@dataclass(frozen=True, slots=True)
class FeatureAvailability:
    implementation: str
    hardware_supported: bool | None
    permission: bool | None
    runtime_available: bool
    reasons: tuple[str, ...]
    gaps: tuple[str, ...]
    phase: int
    support_basis: str


class FeatureRuntime:
    """All extension calls validate before any cancellation, queue or I/O."""

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator

    def definition(self, key: str) -> FeatureDefinition:
        try:
            return FEATURES_BY_KEY[key]
        except KeyError as err:
            raise FeatureBlocked(f"Unknown Nice feature: {key}") from err

    def availability(self, key: str) -> FeatureAvailability:
        definition = self.definition(key)
        owner = self._coordinator
        caps = owner.capabilities
        support = (
            getattr(caps, definition.capability, None)
            if definition.capability
            else None
        )
        permission = None
        basis = "unknown"
        reasons = []
        try:
            binding = definition.require_implemented()
        except FeatureBlocked as err:
            reasons.append(str(err))
            binding = None
        if binding is not None and binding.handler == "diagnostic":
            observed = owner.diagnostic_runtime.view(binding.operation)
            if observed["state"] == "unsupported":
                support = False
                basis = "observed_diagnostic_rejection"
            elif observed["state"] == "blocked":
                reasons.append(observed.get("reason", "Diagnostic format is not verified"))
        if binding is not None and binding.handler == "t4":
            support = owner.t4_action_supported(binding.operation)
            basis = "advertised_or_existing_compatibility_action"
        elif definition.capability and caps is not None:
            basis = str(caps.confidence)
        if binding is not None and binding.handler == "setting":
            setting = next(
                item
                for item in SETTINGS_BY_REGISTER.values()
                if item.key == binding.operation
            )
            reason = owner.setting_write_block_reason(setting)
            if reason:
                reasons.append(reason)
            address = owner.parameters.address(setting.group, setting.parameter)
            observation = owner.parameters.observation_for(address)
            support_state = owner.parameters.support_for(address, "set")
            if support_state in {SupportState.SUPPORTED, SupportState.UNSUPPORTED}:
                support = support_state is SupportState.SUPPORTED
                basis = "observed_write_support"
            elif observation is not None:
                support = None
                basis = "read_observed_write_support_unknown"
            if observation is not None and support_state is SupportState.SUPPORTED:
                support = True
                basis = "observed_parameter"
        # Permissions are reported independently from support. Missing permission
        # is unknown, never silently converted to granted administrator rights.
        if caps is not None and binding is not None:
            name = {
                "action": "DoorAction",
                "t4": "T4Action",
                "name": "Name",
                "clock": "Date",
                "reboot": "Reboot",
            }.get(binding.handler)
            if name:
                matches = [
                    item
                    for item in (*caps.services, *caps.properties)
                    if item.name == name
                    and (
                        item.owner != "Device"
                        or item.owner_id in {None, str(caps.device_id)}
                    )
                ]
                permissions = [
                    str(item.permission).casefold()
                    for item in matches
                    if item.permission is not None
                ]
                if permissions:
                    permission = any("w" in value for value in permissions)
        if binding is not None and binding.handler in {"setting", "name", "clock", "reboot"} and hasattr(owner,"access"):
            role=str(owner.access.role)
            if permission is not False:
                permission = True if role == "admin" else None if role == "unknown" else False
            if role!="admin":
                reasons.append("Administrator permission has not been verified" if role=="unknown" else "Module role does not allow administration")
        if binding is not None and binding.handler == "installer":
            reasons.extend(owner.lifecycle.availability(binding.operation))
        if binding is not None and binding.handler in {"setting", "name", "clock", "reboot"} and hasattr(owner, "lifecycle"):
            if reason := owner.lifecycle.block_reason():
                reasons.append(reason)
        if binding is not None and binding.handler in {"action", "t4"}:
            from .ecosystem_catalog import blocks_gate_adapter
            if blocks_gate_adapter(getattr(owner, "device_info", None)):
                reasons.append("The reported category requires its own device adapter")
        if binding is not None and binding.handler == "core_inspection":
            if caps is None or caps.family is not ProductFamily.CORE:
                support = False
                reasons.append("An explicitly identified CORE endpoint is required")
        if support is False:
            reasons.append("Device does not advertise this operation")
        if permission is False:
            reasons.append("Account permission does not allow writing this operation")
        if caps is not None:
            for contract in (CORE_ACCESS, PROVIEW_ACCESS):
                if caps.family in contract.families and not (binding is not None and binding.handler == "core_inspection"):
                    reasons.append(GAPS_BY_KEY[contract.gap].summary)
        if binding is not None and binding.handler in {
            "setting",
            "name",
            "clock",
            "reboot",
        }:
            if owner.reported_moving:
                reasons.append("Mutation is blocked while the gate is moving")
            if owner.calibration_controller.calibration_state == "running":
                reasons.append("Mutation is blocked during calibration")
        if owner.observations.closed:
            reasons.append("Integration is shutting down")
        connected = str(owner.connection_state) == "connected"
        runtime_reasons = list(reasons)
        if not connected:
            runtime_reasons.append("Connection is not currently established")
        return FeatureAvailability(
            str(definition.implementation),
            support,
            permission,
            not runtime_reasons,
            tuple(runtime_reasons),
            definition.gaps,
            definition.phase,
            basis,
        )

    def require(self, key: str) -> FeatureBinding:
        definition = self.definition(key)
        binding = definition.require_implemented()
        report = self.availability(key)
        # Existing controls retain their ability to reconnect on demand.
        reasons = [
            reason
            for reason in report.reasons
            if reason != "Connection is not currently established"
        ]
        if reasons:
            raise FeatureBlocked(f"{definition.title}: {'; '.join(reasons)}")
        return binding

    def report(self) -> dict:
        return {
            definition.key: asdict(self.availability(definition.key))
            for definition in FEATURES
        }

    async def execute(self, key: str, **payload) -> Any:
        binding = self.require(key)
        allowed = {"core_inspection": {"confirmed"}, "installer": {"confirmed"}, "setting": {"value"}, "name": {"name"}, "history": {"max_rows"}}.get(
            binding.handler, set()
        )
        if set(payload) - allowed:
            raise FeatureBlocked("Unexpected feature arguments")
        required = {"setting": "value", "name": "name"}.get(binding.handler)
        if required is not None and required not in payload:
            raise FeatureBlocked(f"Missing feature argument: {required}")
        owner = self._coordinator
        if binding.handler == "core_inspection":
            return await owner.ecosystem.async_inspect_core(confirmed=payload.get("confirmed", False))
        if binding.handler == "options":
            return await owner.hass.config_entries.options.async_init(owner.config_entry.entry_id)
        if binding.handler == "installer":
            return await owner.lifecycle.async_run(binding.operation, confirmed=payload.get("confirmed", False))
        if binding.handler == "diagnostic":
            return await owner.diagnostic_runtime.async_read(binding.operation)
        if binding.handler == "snapshot":
            from .parity import parity_summary
            if binding.operation == "parity":
                return await owner.hass.async_add_executor_job(parity_summary)
            return {
                "ecosystem": lambda: owner.ecosystem.diagnostics(),
                "status": lambda: owner.observations.snapshot(),
                "incident_timeline": lambda: owner.incidents.export(),
                "position": lambda: owner.position_controller.position_snapshot,
                "events": lambda: tuple(owner.event_controller.event_history),
                "movement_profile": lambda: (
                    owner.position_controller.movement_speed_profile
                ),
                "motion_trace": lambda: tuple(owner.motion_trace.traces),
            }[binding.operation]()
        if binding.handler == "action":
            return await owner.async_send_action(binding.operation)
        if binding.handler == "t4":
            return await owner.async_send_dep_action(binding.operation)
        if binding.handler == "setting":
            setting = next(
                item
                for item in SETTINGS_BY_REGISTER.values()
                if item.key == binding.operation
            )
            return await owner.async_write_setting(setting, payload["value"])
        admin = owner.administration_controller
        if binding.handler == "logs":
            return await admin.async_refresh_interface_logs()
        if binding.handler == "history":
            return await admin.async_refresh_controller_history(**payload)
        if binding.handler == "groups":
            return await admin.async_refresh_access_groups()
        if binding.handler == "name":
            return await admin.async_update_interface_name(payload["name"])
        if binding.handler == "clock":
            return await admin.async_sync_interface_time()
        if binding.handler == "reboot":
            return await admin.async_reboot_interface()
        raise FeatureBlocked("No reviewed runtime handler for this feature")


@dataclass(frozen=True, slots=True)
class ProcedureDefinition:
    key: str
    steps: tuple[str, ...]
    recovery: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()
    # A state-changing procedure cannot be enabled by filling one start opcode.
    # Its complete state machine, completion and abort paths need review too.
    state_machine_verified: bool = False
    gaps: tuple[str, ...] = ()

    def preflight(self, require: Callable[[str], FeatureBinding]) -> None:
        if (
            self.gaps
            or not self.state_machine_verified
            or not self.steps
            or not self.recovery
        ):
            detail = "; ".join(GAPS_BY_KEY[key].summary for key in self.gaps)
            raise FeatureBlocked(
                f"Procedure {self.key} is blocked: {detail or 'state machine and recovery need verification'}"
            )
        for key in (*self.prerequisites, *self.steps, *self.recovery):
            require(key)

    async def run(
        self,
        require: Callable[[str], FeatureBinding],
        runner: Callable[[ProcedureDefinition], Awaitable[Any]],
    ) -> Any:
        self.preflight(require)
        return await runner(self)


FORCE_MAP_PROCEDURE = ProcedureDefinition(
    "installer.fg01h.force_map",
    ("installer.fg01h.force_map",),
    (),
    gaps=("fg01h.force_map_learning",),
)
