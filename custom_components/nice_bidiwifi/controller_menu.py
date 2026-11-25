"""Session-scoped controller menu evidence and a separate controller-role contract.

Module administration is not controller installer authentication. The shipped
role adapter is unresolved; complete menu inspection alone cannot unlock writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import time
from typing import Protocol

from .controller_catalog import PARAMETERS_BY_REGISTER
from homeassistant.exceptions import HomeAssistantError

from .errors import NiceBidiError, NiceParameterError
from .protocol.t4.menu import MenuEntry, MenuPermission, MenuRole

MENU_MAX_AGE = 30


@dataclass(frozen=True, slots=True)
class ControllerRoleProof:
    """Result supplied only by a verified controller-role protocol adapter."""

    role: MenuRole
    session_scope: tuple
    device_identity: tuple
    verified_monotonic: float
    contract: str


class ControllerRoleReader(Protocol):
    """Passive protocol evidence only; configuration cannot select a role."""

    def read_verified_role(
        self, session_scope: tuple, device_identity: tuple
    ) -> ControllerRoleProof: ...


class ControllerMenu:
    def __init__(self, owner):
        self._owner = owner
        self.snapshot = None
        self.received_at = None
        self.captured = None
        self.identity = None
        self.proof = None
        self.role_adapter: ControllerRoleReader | None = None  # controller.role_binding
        self.last_error = None
        self._task = None
        self._last_attempt = float("-inf")

    def schedule(self):
        owner = self._owner
        if (
            self.role_adapter is None
            or not owner.configuration.menu_watched
            or owner.observations.closed
            or owner.reported_moving
            or owner.calibration_controller.calibration_state == "running"
            or self._task is not None
            or time.monotonic() - self._last_attempt < 20
            or self.valid()
            and time.monotonic() - self.captured < 20
        ):
            return
        self._task = owner.tasks.create(
            self._background_refresh(), "nice-controller-menu"
        )

    async def _background_refresh(self):
        try:
            await self.async_refresh()
        except (NiceBidiError, OSError, HomeAssistantError) as error:
            self.last_error = type(error).__name__
        finally:
            self._task = None

    def device_identity(self):
        owner = self._owner
        info = owner.device_info
        return (
            owner.parameters.device_id,
            getattr(info, "device_product", None),
            getattr(info, "device_hw_version", None),
            getattr(info, "device_fw_version", None),
        )

    def invalidate(self):
        self.snapshot = None
        self.proof = None
        self.captured = None
        self.received_at = None
        self.identity = None
        self._owner.parameters.invalidate_menu_permissions()

    def valid(self):
        snapshot = self.snapshot
        return (
            snapshot is not None
            and not self._owner.observations.closed
            and snapshot.complete
            and self.captured is not None
            and 0 <= time.monotonic() - self.captured <= MENU_MAX_AGE
            and snapshot.session_scope is not None
            and snapshot.session_scope
            == getattr(self._owner.client, "access_session_scope", None)
            and snapshot.device_id == self._owner.parameters.device_id
            and self.identity == self.device_identity()
        )

    @property
    def role(self):
        p = self.proof
        if (
            not self.valid()
            or not all(self.identity[1:])
            or not isinstance(p, ControllerRoleProof)
            or p.contract != "verified_controller_role_adapter"
            or p.session_scope != self.snapshot.session_scope
            or p.device_identity != self.identity
            or not 0 <= time.monotonic() - p.verified_monotonic <= MENU_MAX_AGE
        ):
            return MenuRole.UNKNOWN
        return (
            p.role
            if p.role in {MenuRole.USER, MenuRole.INSTALLER}
            else MenuRole.UNKNOWN
        )

    async def async_refresh(self):
        owner = self._owner
        owner.access.stationary_guard()
        owner.lifecycle.require_idle()
        self._last_attempt = time.monotonic()
        generation = owner.observations.generation
        identity = self.device_identity()
        self.invalidate()  # partial/failed replacement never retains old grants
        try:
            snapshot = await owner.hass.async_add_executor_job(
                partial(owner.client.read_controller_menu, preemptible=True)
            )
            if (
                owner.observations.closed
                or generation != owner.observations.generation
                or snapshot.session_scope is None
                or snapshot.session_scope
                != getattr(owner.client, "access_session_scope", None)
                or snapshot.device_id != owner.parameters.device_id
                or not snapshot.complete
                or identity != self.device_identity()
            ):
                raise NiceParameterError(
                    "Menu belongs to an obsolete target or session"
                )
            owner.access.stationary_guard()
            owner.lifecycle.require_idle()
            # Duplicate bindings with contradictory flags cannot become an ACL.
            rows = {}
            for row in snapshot.records:
                previous = rows.get(row.binding)
                if previous is not None and any(
                    getattr(previous, key) != getattr(row, key)
                    for key in (
                        "user_set",
                        "user_get",
                        "installer_set",
                        "installer_get",
                        "is_group",
                    )
                ):
                    raise NiceParameterError(
                        "Controller menu contains conflicting records"
                    )
                rows[row.binding] = row
            self.snapshot, self.identity = snapshot, identity
            self.captured = time.monotonic()
            self.received_at = datetime.now(UTC).isoformat()
            if self.role_adapter is not None:
                self.proof = await owner.hass.async_add_executor_job(
                    self.role_adapter.read_verified_role,
                    snapshot.session_scope,
                    identity,
                )
            if not self.valid():
                raise NiceParameterError("Menu scope changed during role verification")
            if self.role is not MenuRole.UNKNOWN:
                for row in rows.values():
                    definition = PARAMETERS_BY_REGISTER.get(row.binding)
                    if definition is None or row.is_group:
                        continue
                    entry = MenuEntry(
                        row.user_set,
                        row.user_get,
                        row.installer_set,
                        row.installer_get,
                        row.is_group,
                        row.indentation,
                        definition.app_id,
                        row.group | row.parameter << 8,
                    )
                    owner.parameters.record_menu_permission(
                        owner.parameters.address(*row.binding),
                        MenuPermission(
                            snapshot.device_id,
                            row.binding,
                            self.role,
                            entry,
                            owner.parameters.menu_generation,
                        ),
                    )
            self.last_error = None
            owner.async_update_listeners()
            return snapshot
        except Exception as error:
            self.invalidate()
            self.last_error = type(error).__name__
            raise

    def requires_proof(self, setting):
        return setting.requires_menu_permission or self.role_adapter is not None

    def require_permission(self, setting):
        if not self.valid():
            raise NiceParameterError("Fresh controller menu permissions are required")
        if self.role is MenuRole.UNKNOWN:
            raise NiceParameterError(
                "Authenticated controller role is unresolved: controller.role_binding"
            )
        row = next(
            (
                r
                for r in self.snapshot.records
                if r.binding == (setting.group, setting.parameter)
            ),
            None,
        )
        if row is None or row.is_group or not getattr(row, f"{self.role}_set"):
            raise NiceParameterError(
                "Controller menu denies this write for the verified role"
            )

    async def async_prepare_write(self, setting):
        if self.role_adapter is None:
            raise NiceParameterError(
                "Authenticated controller role is unresolved: controller.role_binding"
            )
        await self.async_refresh()
        self.require_permission(setting)
        self._owner.access.stationary_guard()
        info = await self._owner.hass.async_add_executor_job(
            partial(
                self._owner.client.read_dmp_info,
                setting.group,
                setting.parameter,
                preemptible=True,
            )
        )
        self.require_permission(setting)
        if info is None:
            raise NiceParameterError("Fresh controller INFO is required")
        self._owner.parameters.record_info(
            self._owner.parameters.address(setting.group, setting.parameter), info
        )

    def report(self, *, include_records=False):
        valid = self.valid()
        snapshot = self.snapshot
        result = {
            "state": "unavailable"
            if snapshot is None
            else "stale"
            if not valid
            else "role_unverified"
            if self.role is MenuRole.UNKNOWN
            else "verified",
            "controller_role": str(self.role),
            "role_gap": "controller.role_binding"
            if self.role is MenuRole.UNKNOWN
            else None,
            "module_admin_grants_installer": False,
            "complete": bool(snapshot and snapshot.complete),
            "pages": snapshot.pages if snapshot else None,
            "received_at": self.received_at,
            "age_seconds": round(max(0, time.monotonic() - self.captured), 3)
            if self.captured is not None
            else None,
            "last_error": self.last_error,
            "reported_record_count": len(snapshot.records) if snapshot else 0,
            "binding_count": len({row.binding for row in snapshot.records})
            if snapshot
            else 0,
        }
        if snapshot is not None:
            for column in ("user_get", "user_set", "installer_get", "installer_set"):
                result["reported_" + column + "_rows"] = sum(
                    bool(getattr(row, column))
                    for row in snapshot.records
                    if not row.is_group
                )
        if include_records:
            result["records"] = [
                {
                    "binding": f"{r.group:02X}/{r.parameter:02X}",
                    "user_get": r.user_get,
                    "user_set": r.user_set,
                    "installer_get": r.installer_get,
                    "installer_set": r.installer_set,
                    "group": r.is_group,
                    "indentation": r.indentation,
                }
                for r in (snapshot.records if snapshot else ())
            ]
        return result
