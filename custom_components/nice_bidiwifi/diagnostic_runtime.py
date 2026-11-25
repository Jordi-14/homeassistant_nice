"""Reviewed diagnostic screens, bounded read-only adapters and explicit gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
import time

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.exceptions import HomeAssistantError

from .models.capabilities import ProductFamily
from .configuration import scalar_info_valid
from .controller_catalog import ENUM_FUNCTIONS
from .errors import NiceBidiError, NiceParameterError, NiceReadPreemptedError
from .parameters import SupportState
from .protocol.t4.automation import decode_automation_state
from .protocol.t4.bluebus import decode_bluebus_devices
from .protocol.t4.counters import decode_counter
from .protocol.t4.io import decode_hardware_diagnostic, decode_io_diagnostic
from .protocol.t4.maneuvers import maneuver_evidence

MAX_DIAGNOSTIC_ELEMENTS = 16
MAX_DIAGNOSTIC_BYTES = 256
AUTOMATION_REFRESH_SECONDS = 5
AUTOMATION_STALE_SECONDS = 10


@dataclass(frozen=True, slots=True)
class DiagnosticDefinition:
    key: str
    title: str
    register: tuple[int, int] | None
    decoder: str | None
    gap: str | None = None


DIAGNOSTICS = (
    DiagnosticDefinition("controller_menu", "Controller menu permissions", (0, 0x10), "menu", "controller.role_binding"),
    DiagnosticDefinition(
        "counters", "Controller counters and maintenance", (4, 0xB3), "counters"
    ),
    DiagnosticDefinition("bluebus", "BlueBus diagnostics", (4, 0xD0), "bluebus"),
    DiagnosticDefinition("io", "Controller inputs and outputs", (4, 0xD1), "io"),
    DiagnosticDefinition(
        "hardware", "Motor and power diagnostics", (4, 0xD2), "hardware"
    ),
    DiagnosticDefinition(
        "maneuvers", "Maneuver history evidence", (4, 0xDA), "maneuvers"
    ),
    DiagnosticDefinition("inverter", "Inverter diagnostics", (4, 0xD4), "hardware"),
    DiagnosticDefinition(
        "advanced_history",
        "Advanced history",
        (4, 0xDB),
        "metadata",
        "diagnostics.advanced_history",
    ),
    DiagnosticDefinition(
        "automation_state",
        "Automation diagnostic structure",
        (4, 0x01),
        "automation",
        "diagnostics.automation_structure",
    ),
    DiagnosticDefinition(
        "other",
        "Other controller diagnostics",
        (4, 0xD3),
        None,
        "diagnostics.other_layouts",
    ),
    DiagnosticDefinition(
        "visual",
        "Visual controller diagnostics",
        (4, 0xD5),
        None,
        "diagnostics.other_layouts",
    ),
    DiagnosticDefinition(
        "test_history",
        "Controller test history",
        (4, 0xDD),
        None,
        "diagnostics.test_history",
    ),
    DiagnosticDefinition(
        "advanced_test_history",
        "Advanced test history",
        (4, 0xDE),
        None,
        "diagnostics.test_history",
    ),
    DiagnosticDefinition(
        "receiver_events",
        "Receiver diagnostic events",
        None,
        None,
        "diagnostics.receiver_layouts",
    ),
    DiagnosticDefinition(
        "power_supply",
        "Power supply and battery profile",
        None,
        None,
        "diagnostics.psu_transport",
    ),
    DiagnosticDefinition(
        "maintenance_reset",
        "Maintenance counter reset",
        (4, 0xB4),
        None,
        "diagnostics.maintenance_procedure",
    ),
)
DIAGNOSTICS_BY_KEY = {row.key: row for row in DIAGNOSTICS}


class DiagnosticRuntime:
    def __init__(self, owner):
        self._owner = owner
        self._watched = {}
        self._task = None
        self._remove_tick = None
        self._attempts = {}
        self._samples = {}
        self._generation = 0
        self.event_counters = {}

    def extra_registers(self):
        if self._owner.data is None or not self._owner.data.is_moving:
            return ()
        registers = []
        if set(self._watched) & {"hardware", "io"}:
            registers.extend(((4, 0xD1), (4, 0xD2)))
        if "automation_state" in self._watched:
            registers.append((4, 0x01))
        if "inverter" in self._watched:
            info = self._owner.parameters.info_for(
                self._owner.parameters.address(4, 0xD4)
            )
            if info and info.size == 2 and info.values and len(info.values) >= 12:
                registers.append((4, 0xD4))
        return tuple(registers)

    def record_poll(self, status):
        for key, register in (
            ("hardware", "04/D2"),
            ("io", "04/D1"),
            ("inverter", "04/D4"),
            ("automation_state", "04/01"),
        ):
            if (
                status.fresh_registers is not None
                and register not in status.fresh_registers
            ):
                continue
            raw = status.registers.get(register)
            if not raw:
                continue
            definition = DIAGNOSTICS_BY_KEY[key]
            address = self._owner.parameters.address(*definition.register)
            info = self._owner.parameters.info_for(address)
            if key == "inverter" and (
                info is None
                or info.size != 2
                or not info.values
                or len(info.values) < 12
            ):
                continue
            try:
                result = self._decode(definition, bytes.fromhex(raw), info)
            except ValueError:
                continue
            observation = self._owner.parameters.observation_for(address)
            captured = observation.monotonic if observation else time.monotonic()
            result.update(
                observed_monotonic=captured, received_at=datetime.now(UTC).isoformat(), source="poll"
            )
            self._samples[key] = result
            self._attempts[key] = captured
            if key in {"hardware", "inverter"}:
                self._owner.incidents.add(
                    "load", "poll", {"register": register, **result.get("values", {})}
                )

    def definition(self, key):
        try:
            return DIAGNOSTICS_BY_KEY[key]
        except KeyError as err:
            raise NiceParameterError("Unknown diagnostic screen") from err

    def require_read(self, key):
        definition = self.definition(key)
        capabilities = self._owner.capabilities
        if capabilities is not None and capabilities.family in {
            ProductFamily.CORE,
            ProductFamily.PROVIEW,
        }:
            raise NiceParameterError(
                "Diagnostic access adapter is unresolved: transport."
                + str(capabilities.family)
            )
        if definition.decoder is None or definition.register is None:
            raise NiceParameterError(
                "Diagnostic binding is blocked: " + str(definition.gap)
            )
        if self._owner.observations.closed:
            raise NiceParameterError("Integration is shutting down")
        return definition

    def watch(self, key):
        self.require_read(key)
        self._watched[key] = self._watched.get(key, 0) + 1
        if key in {"automation_state", "controller_menu"} and self._remove_tick is None:
            self._remove_tick = async_track_time_interval(self._owner.hass, self._tick, timedelta(seconds=AUTOMATION_REFRESH_SECONDS))
        self.schedule()

        def remove():
            self._watched[key] -= 1
            if self._watched[key] <= 0:
                del self._watched[key]
            if not set(self._watched) & {"automation_state", "controller_menu"}:
                self.close()

        return remove

    def close(self):
        if self._remove_tick is not None:
            self._remove_tick()
            self._remove_tick = None

    @callback
    def _tick(self, now):
        if self._owner.observations.closed:
            self.close()
            return
        self._owner.async_update_listeners()
        self.schedule()

    def _refresh_interval(self, key):
        if key == "automation_state" and self._samples.get(key, {}).get("last_read_error"):
            return 30
        return AUTOMATION_REFRESH_SECONDS if key == "automation_state" else 30 if key == "controller_menu" else 300

    def invalidate(self):
        self._generation += 1
        self._attempts.clear()
        self._samples.clear()
        self.event_counters.clear()

    def schedule(self):
        if (
            self._owner.observations.closed
            or self._owner.reported_moving
            or not self._watched
        ):
            return
        if self._task is None or self._task.done():
            self._task = self._owner.tasks.create(
                self._refresh(), "nice-diagnostic-screens"
            )

    async def _refresh(self):
        try:
            due = sorted(
                (
                    key
                    for key in self._watched
                    if time.monotonic() - self._attempts.get(key, float("-inf")) >= self._refresh_interval(key)
                ),
                key=lambda key: (key != "automation_state", self._attempts.get(key, float("-inf"))),
            )
            for key in due[:2]:
                try:
                    await self.async_read(key)
                except NiceReadPreemptedError:
                    break
                except (NiceBidiError, OSError, HomeAssistantError) as error:
                    self._attempts[key] = time.monotonic()
                    self._samples.setdefault(key, {}).update(
                        state="unavailable", last_read_error=type(error).__name__
                    )
        finally:
            self._task = None
            self._owner.async_update_listeners()

    async def async_read(self, key):
        definition = self.require_read(key)
        owner = self._owner
        if (
            owner.reported_moving
            or owner.calibration_controller.calibration_state == "running"
        ):
            raise NiceReadPreemptedError("Diagnostic read waits until stationary")
        if key == "controller_menu":
            await owner.controller_menu.async_refresh()
            self._samples.pop(key, None)
            self._attempts[key] = time.monotonic()
            return
        generation = self._generation
        address = owner.parameters.address(*definition.register)
        if key == "counters":
            values = {}
            raw_values = {}
            for field, parameter in (
                ("maintenance_threshold", 0xB1),
                ("maintenance_count", 0xB2),
                ("total_maneuver_count", 0xB3),
            ):
                if owner.reported_moving:
                    raise NiceReadPreemptedError("Counters yielded to movement")
                raw = await owner.hass.async_add_executor_job(
                    partial(
                        owner.client.read_dmp_value,
                        4,
                        parameter,
                        preemptible=True,
                        background=True,
                    )
                )
                if generation != self._generation or owner.observations.closed:
                    return
                metadata = owner.parameters.info_for(
                    owner.parameters.address(4, parameter)
                )
                raw_values[field], values[field] = decode_counter(raw, metadata)

            self._attempts[key] = time.monotonic()
            self._samples[key] = {
                "state": "available"
                if any(v is not None for v in values.values())
                else "unavailable",
                "values": values,
                "raw_values": raw_values,
                "value_basis": "controller_INFO_scaling_when_available_otherwise_reviewed_u32",
                "source": "dmp_get",
                "counter_epoch_relationship": "unknown",
                "observed_monotonic": time.monotonic(),
                "received_at": datetime.now(UTC).isoformat(),
            }
            owner.async_update_listeners()
            return
        if owner.parameters.support_for(address, "get") is SupportState.UNSUPPORTED:
            self._attempts[key] = time.monotonic()
            self._samples[key] = {"state": "unsupported", "reason": "Controller rejected this diagnostic GET"}
            return
        info = owner.parameters.info_for(address)
        if key not in {"bluebus", "maneuvers", "automation_state"}:
            info = await owner.hass.async_add_executor_job(
                partial(
                    owner.client.read_dmp_info, *definition.register, preemptible=True
                )
            )
            if generation != self._generation or owner.observations.closed:
                return
            if info is not None:
                owner.parameters.record_info(address, info)
            else:
                state = owner.parameters.support_for(address, "info")
                owner.parameters.record_info(
                    address,
                    None,
                    state=state
                    if state is SupportState.UNSUPPORTED
                    else SupportState.UNAVAILABLE,
                )
        if key in {"inverter", "advanced_history"} and info is None:
            self._attempts[key] = time.monotonic()
            self._samples[key] = {
                "state": "unsupported"
                if owner.parameters.support_for(address, "info")
                is SupportState.UNSUPPORTED
                else "unavailable",
                "reason": "No complete INFO format was returned",
                "gap": definition.gap or "diagnostics.inverter_format",
            }
            return
        if key == "advanced_history" and not scalar_info_valid(info):
            self._samples[key] = {
                "state": "blocked",
                "gap": definition.gap,
                "reason": "INFO requires an unverified structured or signed decoder",
            }
            self._attempts[key] = time.monotonic()
            return
        if key == "inverter" and (
            info.size != 2 or info.values is None or len(info.values) < 12
        ):
            self._samples[key] = {
                "state": "blocked",
                "gap": "diagnostics.inverter_format",
                "reason": "Missing hardware descriptor layout",
            }
            self._attempts[key] = time.monotonic()
            return
        if info is not None and info.indexed:
            if (
                key != "advanced_history"
                or info.array_size is None
                or not 0 < info.array_size <= MAX_DIAGNOSTIC_ELEMENTS
            ):
                self._samples[key] = {
                    "state": "blocked",
                    "gap": definition.gap,
                    "reason": "Indexed diagnostic layout is not verified",
                }
            else:
                elements = []
                for index in range(info.array_size):
                    if owner.reported_moving:
                        raise NiceReadPreemptedError(
                            "Diagnostic scan yielded to movement"
                        )
                    raw = await owner.hass.async_add_executor_job(
                        partial(
                            owner.client.read_dmp_indexed,
                            *definition.register,
                            index,
                            preemptible=True,
                            background=True,
                        )
                    )
                    if generation != self._generation or owner.observations.closed:
                        return
                    observed = owner.parameters.observe(
                        owner.parameters.address(*definition.register, index=index), raw
                    )
                    elements.append(
                        {
                            "index": index,
                            "value": observed.value,
                            "raw_value": observed.raw_value,
                        }
                    )
                self._samples[key] = {
                    "state": "available"
                    if any(row["value"] is not None for row in elements)
                    else "unavailable",
                    "elements": elements,
                    "confidence": "INFO_scalar_elements",
                    "slot_relation_to_04_DA": "unknown",
                }
        else:
            raw = await owner.hass.async_add_executor_job(
                partial(
                    owner.client.read_dmp_value,
                    *definition.register,
                    preemptible=True,
                    background=True,
                )
            )
            if generation != self._generation or owner.observations.closed:
                return
            if raw is not None and len(raw) > MAX_DIAGNOSTIC_BYTES:
                self._samples[key] = {
                    "state": "blocked",
                    "gap": definition.gap,
                    "reason": "Diagnostic payload exceeds retention limit",
                }
            else:
                self._samples[key] = self._decode(definition, raw, info)
        self._attempts[key] = time.monotonic()
        self._samples.setdefault(key, {})["observed_monotonic"] = time.monotonic()
        self._samples[key]["received_at"] = datetime.now(UTC).isoformat()
        owner.async_update_listeners()

    def _decode(self, definition, raw, info):
        if definition.decoder == "automation":
            values = decode_automation_state(raw)
            return {
                "state": "available" if values is not None else "unavailable",
                "values": values or {},
                "confidence": "native_and_Java_static_audit_hardware_validation_pending",
                "layout": "CTRL_AUTOMATION_STATUS_byte_fields",
                "field_semantics": "app_reported_interpretation_not_verified_procedure_state",
                "flag_validity": "unverified_all_ones" if raw and len(raw) > 1 and raw[1] == 255 else "reported_when_present",
                "physical_cause": "unknown",
                "affects_control_or_permissions": False,
            }
        if definition.decoder == "maneuvers":
            return maneuver_evidence(
                raw,
                stale=bool(self._owner.data and self._owner.data.last_maneuvers_stale),
            )
        decode = {
            "bluebus": decode_bluebus_devices,
            "io": decode_io_diagnostic,
            "hardware": decode_hardware_diagnostic,
        }.get(definition.decoder)
        if decode:
            values = (
                decode(raw)
                if definition.decoder == "bluebus"
                else decode(raw, info, include_primary=True)
                if definition.decoder == "hardware"
                else decode(raw, info)
            )
            return {
                "state": "available" if values is not None else "unavailable",
                "values": values or {},
                "confidence": "reviewed_decoder",
                "installation_metadata": "available"
                if info is not None and info.values is not None
                else "unverified_presence",
                "physical_cause": "unknown",
            }
        if definition.decoder == "metadata" and info is not None:
            observation = self._owner.parameters.observe(
                self._owner.parameters.address(*definition.register), raw
            )
            value = observation.value
            if info.scalar_options and observation.raw_value is not None:
                value = ENUM_FUNCTIONS.get(info.function, {}).get(
                    observation.raw_value, "code_" + str(observation.raw_value)
                )
            return {
                "state": "available" if value is not None else "unavailable",
                "value": value,
                "confidence": "INFO_scalar_value",
                "slot_relation_to_04_DA": "unknown",
                "advanced_diagnostic_enum_assumed": False,
            }
        return {"state": "blocked", "gap": definition.gap}

    def _counter_channels(self, view):
        result = dict(view)
        result.update(
            nhk_counter_channel=dict(self.event_counters),
            nhk_to_dmp_mapping="not_established",
            dmp_channel_state=view["state"],
            nhk_channel_state="available" if self.event_counters else "not_observed",
        )
        if self.event_counters and view["state"] != "available":
            result["state"] = "available"
        return result

    def view(self, key):
        definition = self.definition(key)
        capabilities = self._owner.capabilities
        if capabilities is not None and capabilities.family in {
            ProductFamily.CORE,
            ProductFamily.PROVIEW,
        }:
            return {
                "state": "blocked",
                "gap": "transport." + str(capabilities.family),
                "reason": "Diagnostic access adapter is unresolved",
            }
        if definition.decoder is None:
            return {
                "state": "blocked",
                "gap": definition.gap,
                "reason": "Diagnostic binding is unresolved",
            }
        if key == "controller_menu":
            view = self._owner.controller_menu.report()
            if error := self._samples.get(key, {}).get("last_read_error"):
                view["last_read_error"] = error
            return view
        if key == "counters" and key not in self._samples:
            data = self._owner.data
            values = {
                field: getattr(data, field, None)
                for field in (
                    "maintenance_threshold",
                    "maintenance_count",
                    "total_maneuver_count",
                )
            }
            raw_values = dict(values)
            field_ages = {}
            for field, parameter in (
                ("maintenance_threshold", 0xB1),
                ("maintenance_count", 0xB2),
                ("total_maneuver_count", 0xB3),
            ):
                address = self._owner.parameters.address(4, parameter)
                metadata = self._owner.parameters.info_for(address)
                observation = self._owner.parameters.observation_for(address)
                raw_values[field], values[field] = decode_counter(
                    observation.raw if observation is not None else None,
                    metadata,
                    fallback=values[field] if observation is None else None,
                )
                if observation is not None:
                    field_ages[field] = max(
                        0, round(time.monotonic() - observation.monotonic, 3)
                    )

            return self._counter_channels(
                {
                    "state": "available"
                    if any(value is not None for value in values.values())
                    else "unavailable",
                    "values": values,
                    "raw_values": raw_values,
                    "value_basis": "controller_INFO_scaling_when_available_otherwise_reviewed_u32",
                    "source": "cached_controller_observations",
                    "field_age_seconds": field_ages,
                    "counter_epoch_relationship": "unknown",
                    "nhk_counter_channel": dict(self.event_counters),
                    "nhk_to_dmp_mapping": "not_established",
                }
            )
        if key in self._samples:
            sample = {
                k: v for k, v in self._samples[key].items() if k != "observed_monotonic"
            }
            if key == "counters":
                sample.update(
                    nhk_counter_channel=dict(self.event_counters),
                    nhk_to_dmp_mapping="not_established",
                )
            captured = self._samples[key].get("observed_monotonic")
            result = {
                **sample,
                "source": sample.get("source", "selective_get"),
                "age_seconds": max(0, round(time.monotonic() - captured, 3))
                if captured is not None
                else None,
                "last_attempt_age_seconds": max(
                    0,
                    round(
                        time.monotonic() - self._attempts.get(key, time.monotonic()), 3
                    ),
                ),
            }

            if key == "automation_state":
                from .protocol.t4.dmp import STATUS_BY_BYTE
                gate = self._owner.data
                gate_state = gate.state if gate is not None else None
                reported = STATUS_BY_BYTE.get(result.get("values", {}).get("status_code"))
                stationary = {"open", "closed", "stopped", "partially_open"}
                generic_stop = "stopped" in {reported, gate_state} and reported in stationary and gate_state in stationary
                mismatch = reported is not None and gate_state is not None and reported != gate_state and not generic_stop
                stale = captured is None or time.monotonic() - captured > AUTOMATION_STALE_SECONDS or mismatch
                result.update(current_gate_state=gate_state, snapshot_stale=stale, refresh_interval_seconds=AUTOMATION_REFRESH_SECONDS)
                if result["state"] == "available" and stale:
                    result["state"] = "stale"
                    result["reason"] = "snapshot_differs_from_current_gate_state" if mismatch else "snapshot_expired"
            return self._counter_channels(result) if key == "counters" else result

        owner = self._owner
        address = owner.parameters.address(*definition.register)
        if (
            owner.parameters.support_for(address, "get") is SupportState.UNSUPPORTED
            or key in {"inverter", "advanced_history"}
            and owner.parameters.support_for(address, "info")
            is SupportState.UNSUPPORTED
        ):
            return {
                "state": "unsupported",
                "reason": "Controller rejected this diagnostic operation",
                "gap": definition.gap,
            }
        raw = None
        if owner.data is not None:
            text = owner.data.registers.get(
                "/".join(f"{part:02X}" for part in definition.register)
            )
            if text:
                try:
                    raw = bytes.fromhex(text)
                except ValueError:
                    pass
        if raw is not None and definition.key in {
            "bluebus",
            "io",
            "hardware",
            "maneuvers",
        }:
            result = self._decode(definition, raw, owner.parameters.info_for(address))
            result["source"] = "cached_status"
            observation = owner.parameters.observation_for(address)
            result["age_seconds"] = (
                max(0, round(time.monotonic() - observation.monotonic, 3))
                if observation
                else None
            )
            return result
        return {
            "state": "unavailable",
            "reason": "No diagnostic read completed",
            "gap": definition.gap,
        }

    def report(self):
        return {row.key: self.view(row.key) for row in DIAGNOSTICS}
