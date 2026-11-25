"""Installer runtime adapters and sanitized lifecycle observations."""

from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import replace
from functools import partial

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .connection import CONNECTION_STATE_RECONNECTING
from .controller_catalog import PARAMETERS
from .errors import NiceParameterError
from .feature_catalog import FeatureBlocked
from .installer_catalog import ADAPTERS, PROCEDURES, PROCEDURES_BY_KEY
from .procedures import ProcedureRecord, ProcedureRunner


class LifecycleRuntime:
    def __init__(self, owner):
        self.owner = owner
        self.inventory = None
        self.inventory_at = None
        self.bus_inventory = None
        self.bus_inventory_at = None
        self._bus_scope = None
        self.boot = {}
        self.boot_at = None
        self._programming_hold = False
        self._programming_hold_at = None
        self.snapshot = None
        self._context = None
        self._store = Store(
            owner.hass, 1, f"nice_bidiwifi.procedure.{owner.config_entry.entry_id}"
        )
        self.runner = ProcedureRunner(
            {
                "inventory": self._inventory,
                "bus_profiles": self._bus_profiles,
                "file_info": partial(self._boot_read, "FILE_INFO"),
                "boot_devices": partial(self._boot_read, "DEV_ON_BOOT"),
                "programming_status": partial(self._boot_read, "STATUS_PROGRAMMING"),
                "snapshot": self._snapshot,
                "radio_page": self._radio_page,
                "restart": self._restart,
            },
            prepare=self._prepare,
            guard=self._guard,
            persist=self._store.async_save,
            notify=owner.async_update_listeners,
        )

    async def async_load(self):
        try:
            self.runner.restore(await self._store.async_load())
        except Exception:
            # Corrupt/unreadable persistence must not silently enable mutations.
            self.runner.record = ProcedureRecord(
                "unrecognized",
                state="needs_recovery",
                recovery_required=True,
                error_type="JournalLoadFailed",
            )

    @property
    def programming_observed(self):
        return self._programming_hold

    def block_reason(self):
        if (
            self.runner.closing
            or self.runner.busy
            or self.runner.record is not None
            and self.runner.record.recovery_required
        ):
            return "An installer procedure is active or needs recovery inspection"
        if self.programming_observed:
            return "The device reports programming in progress; inspect bootloader state again before administration"
        return None

    def require_idle(self):
        if reason := self.block_reason():
            raise HomeAssistantError(reason)

    def require_idle_at_dispatch(self):
        try:
            self.require_idle()
        except HomeAssistantError as error:
            raise NiceParameterError(str(error)) from error

    def control_guard(self, action):
        if action in {"stop", "stop_remote"}:
            return
        if self.programming_observed or (
            self.runner.busy
            and self.runner._spec is not None
            and self.runner._spec.mutates
        ):
            raise HomeAssistantError(
                "Gate commands are blocked while an installer mutation is in flight"
            )

    def availability(self, key):
        spec = PROCEDURES_BY_KEY[key]
        reasons = []
        if self.runner.busy or self.runner.closing:
            reasons.append(
                "Another installer procedure is active or the integration is closing"
            )
        if (
            spec.mutates
            and self.runner.record is not None
            and self.runner.record.recovery_required
        ):
            reasons.append("An interrupted procedure needs recovery inspection")
        if spec.mutates and self.programming_observed:
            reasons.append("Controller programming is reported in progress")
        if (
            "administrator" in spec.prerequisites
            and str(self.owner.access.role) != "admin"
        ):
            reasons.append("Administrator permission has not been verified")
        if (
            "local" in spec.prerequisites
            and self.owner.active_connection_route != "local"
        ):
            reasons.append("An active local connection is required")
        if "stationary" in spec.prerequisites:
            try:
                self.owner.access.stationary_guard()
            except Exception:
                reasons.append(
                    "A confirmed stationary gate without calibration is required"
                )
        if (
            "reboot_capability" in spec.prerequisites
            and not self.owner.administration_capability("reboot")
        ):
            reasons.append("Restart capability is not advertised")
        return reasons

    async def _prepare(self, spec):
        owner = self.owner
        if owner.observations.closed:
            raise FeatureBlocked("Integration is shutting down")
        known = {
            "administrator",
            "stationary",
            "local",
            "advertised_interface_id",
            "reboot_capability",
            "pro_controls",
        }
        if set(spec.prerequisites) - known:
            raise FeatureBlocked("An installer prerequisite has no verified adapter")
        if "pro_controls" in spec.prerequisites and not owner.controller_menu.enabled:
            raise FeatureBlocked("Enable Pro controls before receiver management")
        if "local" in spec.prerequisites and owner.active_connection_route != "local":
            raise FeatureBlocked("This inspection requires an active local connection")
        if spec.mutates and self.programming_observed:
            raise FeatureBlocked(
                "The module reports controller programming in progress"
            )
        if "administrator" in spec.prerequisites:
            await owner.access.async_prepare_admin()
        elif "stationary" in spec.prerequisites:
            owner.access.stationary_guard()
        if (
            "reboot_capability" in spec.prerequisites
            and not owner.administration_capability("reboot")
        ):
            raise FeatureBlocked(
                "This interface does not support the restart operation"
            )
        if spec.key == "inspect_boot":
            from .models.capabilities import ProductFamily

            if owner.capabilities is None or owner.capabilities.family not in {
                ProductFamily.BIDI_WIFI,
                ProductFamily.IT4_WIFI,
                ProductFamily.CU_WIFI,
            }:
                raise FeatureBlocked(
                    "BOOT_1 inspection has no adapter for this interface family"
                )
            self.boot = {}
            self.boot_at = None
        self._context = (
            owner.observations.generation,
            getattr(owner.client, "access_session_scope", None),
        )

    def _guard(self, spec):
        owner = self.owner
        try:
            if owner.observations.closed or self.runner.closing:
                raise FeatureBlocked("Integration is shutting down")
            if self._context != (
                owner.observations.generation,
                getattr(owner.client, "access_session_scope", None),
            ):
                raise FeatureBlocked("The device session changed during the procedure")
            if (
                "pro_controls" in spec.prerequisites
                and not owner.controller_menu.enabled
            ):
                raise FeatureBlocked("Pro controls were disabled")
            if "stationary" in spec.prerequisites:
                owner.access.stationary_guard()
            if "administrator" in spec.prerequisites:
                owner.access.require_admin()
            if (
                "reboot_capability" in spec.prerequisites
                and not owner.administration_capability("reboot")
            ):
                raise FeatureBlocked("The interface restart capability changed")
            if (
                "local" in spec.prerequisites
                and owner.active_connection_route != "local"
            ):
                raise FeatureBlocked("The active route changed")
        except Exception as error:
            error.delivery = "not_sent"
            raise

    async def _radio_page(self):
        receiver = self.owner.receiver
        async with receiver._lock:
            receiver.memory_page = None
            generation = receiver.generation
            observation_generation = self.owner.observations.generation
            result = await self.owner.hass.async_add_executor_job(
                self.owner.client.read_receiver_memory_page, 0, 8
            )
            self._guard(self.runner._spec)
            receiver._store_operator_result(
                "memory_page", result, generation, observation_generation
            )
            return {"observed_slots": 8, "complete_inventory": False}

    async def _bus_profiles(self):
        result = await self.owner.hass.async_add_executor_job(self.owner.client.read_bus_profiles)
        self._guard(self.runner._spec)
        if result.get("session_scope") is None or result["session_scope"] != getattr(self.owner.client, "access_session_scope", None):
            raise FeatureBlocked("Bus inventory belongs to an obsolete session")
        self._bus_scope = (self.owner.observations.generation, result["session_scope"])
        self.bus_inventory = {k:v for k,v in result.items() if k != "session_scope"}
        self.bus_inventory_at = datetime.now(UTC).isoformat()
        return dict(self.bus_inventory)

    async def _inventory(self):
        inventory = await self.owner.hass.async_add_executor_job(
            self.owner.client.read_device_inventory
        )
        self._guard(self.runner._spec)
        self.inventory = inventory
        self.inventory_at = datetime.now(UTC).isoformat()
        return inventory.export()

    async def _boot_read(self, operation):
        if self.inventory is None or self.inventory.interface_id is None:
            raise FeatureBlocked(
                "The interface did not advertise an identity; BOOT_1 cannot be targeted"
            )
        result = await self.owner.hass.async_add_executor_job(
            partial(
                self.owner.client.read_boot_state,
                operation,
                self.inventory.interface_id,
                before_dispatch=partial(self._guard, PROCEDURES_BY_KEY["inspect_boot"]),
            )
        )
        self._guard(self.runner._spec)
        self.boot_at = datetime.now(UTC).isoformat()
        if operation == "STATUS_PROGRAMMING":
            status = result.get("status_reported")
            if status == "RUN":
                self._programming_hold = True
                self._programming_hold_at = self.boot_at
            elif status in {"IDLE", "SUCCESS", "STOP", "ALREADY_UPDATED"}:
                self._programming_hold = False
                self._programming_hold_at = None
        self.boot[operation] = {"observed_at": self.boot_at, "data": result}
        return result

    async def _snapshot(self):
        owner = self.owner
        records = []
        for definition in PARAMETERS:
            if not definition.safe_read or definition.binding is None:
                continue
            address = owner.configuration.address(definition)
            observation = owner.parameters.observation_for(address)
            view = owner.configuration.view(definition.key)
            if observation is None or observation.raw_value is None:
                continue
            records.append(
                {
                    "key": definition.key,
                    "register": f"{address.group:02X}/{address.parameter:02X}",
                    "device_id": address.device_id,
                    "raw_value": observation.raw_value,
                    "value": view.value,
                    "unit": view.unit,
                    "observed_at": observation.observed_at.isoformat(),
                    "source": observation.source,
                }
            )
        previous = self.snapshot
        current = {row["key"]: row for row in records}
        older = {row["key"]: row for row in previous["parameters"]} if previous else {}
        changed = [
            key
            for key in older.keys() & current.keys()
            if older[key]["raw_value"] != current[key]["raw_value"]
        ]
        info = owner.device_info
        profile = (
            {
                "product": info.device_product,
                "hardware": info.device_hw_version,
                "firmware": info.device_fw_version,
            }
            if info
            else None
        )
        comparable = previous is not None and previous.get("profile") == profile
        self.snapshot = {
            "schema": 1,
            "kind": "observed_configuration",
            "captured_at": datetime.now(UTC).isoformat(),
            "complete": False,
            "restorable": False,
            "profile": profile,
            "parameters": records,
            "comparison": {
                "same_profile": comparable,
                "changed_keys": sorted(changed) if comparable else [],
                "added": sorted(current.keys() - older.keys()) if comparable else [],
                "missing": sorted(older.keys() - current.keys()) if comparable else [],
            },
            "identity_basis": "entry-scoped observations; not authorization for device transfer",
        }
        return self.snapshot

    async def _restart(self):
        owner = self.owner
        spec = PROCEDURES_BY_KEY["restart_interface"]
        await owner.hass.async_add_executor_job(
            partial(
                owner.client.reboot_interface,
                before_dispatch=partial(self._guard, spec),
            )
        )
        return {"acknowledged": True, "physical_reboot_verified": False}

    async def async_run(self, key, *, confirmed=False):
        try:
            spec = PROCEDURES_BY_KEY[key]
        except KeyError as error:
            raise FeatureBlocked("Unknown installer procedure") from error
        result = await self.runner.run(spec, confirmed=confirmed)
        if key == "restart_interface" and not self.owner.observations.closed:
            await self.owner.hass.async_add_executor_job(self.owner.client.close)
            if not self.owner.observations.closed:
                self.owner._set_connection_state(CONNECTION_STATE_RECONNECTING)
                self.owner.event_controller.mark_reconnecting()
        return result

    async def async_acknowledge_recovery(self, *, confirmed=False):
        if confirmed is not True:
            raise FeatureBlocked("Explicit recovery acknowledgement is required")
        if self.runner.busy or self.runner.closing:
            raise FeatureBlocked("A procedure is still active")
        record = self.runner.record
        if record is None or not record.recovery_required:
            return
        if record.key != "restart_interface":
            raise FeatureBlocked(
                "This procedure requires its model-specific recovery adapter"
            )
        spec = PROCEDURES_BY_KEY["inspect_devices"]

        async def reconcile():
            if self.runner.record is not record or not record.recovery_required:
                raise FeatureBlocked("The recovery journal changed")
            await self.owner.access.async_prepare_admin()
            await self._prepare(spec)
            await self._inventory()
            self.owner.access.require_admin()
            self.owner.access.stationary_guard()
            updated = replace(
                record,
                state="recovery_acknowledged",
                recovery_required=False,
                completion_basis="Operator checked the module; previous physical restart outcome remains unverified",
                updated_at=datetime.now(UTC).isoformat(),
            )
            await self._store.async_save(updated.export())
            self.runner.record = updated
            self.owner.async_update_listeners()

        # Inspection and durable acknowledgement own the same procedure slot;
        # a new restart cannot race an older acknowledgement's storage write.
        await self.runner.reconcile(spec, reconcile)

    def summary(self):
        record = self.runner.record
        state = record.state if record else "idle"
        parts = [f"Procedure state: {state}."]
        if record and record.key == "discover_bus" and self.bus_inventory is not None:
            count = len(self.bus_inventory["devices"])
            parts.append(f"BusT4 sources observed: {count} (partial inventory).")
        elif self.inventory is not None:
            parts.append(f"Advertised devices: {len(self.inventory.devices)}.")
        if self.snapshot is not None:
            parts.append(f"Observed configuration values: {len(self.snapshot['parameters'])}.")
        parts.append("Read-only details and missing bindings are included in integration diagnostics.")
        return " ".join(parts)

    def diagnostics(self):
        return {
            "procedure": self.runner.record.export() if self.runner.record else None,
            "busy": self.runner.busy,
            "inventory": self.inventory.export() if self.inventory else None,
            "inventory_at": self.inventory_at,
            "bus_discovery": {
                "state": "not_inspected" if self.bus_inventory is None else "observed"
                if self._bus_scope == (self.owner.observations.generation, getattr(self.owner.client,"access_session_scope",None)) and not self.owner.observations.closed else "stale",
                "observed_at": self.bus_inventory_at, "data": self.bus_inventory,
            },
            "boot_observations": self.boot,
            "boot_at": self.boot_at,
            "programming_observed_hold": self._programming_hold,
            "programming_observed_at": self._programming_hold_at,
            "configuration_snapshot": self.snapshot,
            "procedures": {p.key: p.report() for p in PROCEDURES},
            "adapters": ADAPTERS,
            "raw_radio_memory_retained": False,
            "firmware_images_retained": False,
            "animation_calibration_programs_controller": False,
        }

    async def async_close(self):
        await self.runner.close()
