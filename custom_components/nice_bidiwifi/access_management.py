"""Fresh principal/role evidence and guarded module-user operations."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
import time

from homeassistant.exceptions import HomeAssistantError

from .errors import NiceBidiError, NicePermissionError
from .protocol.nhk.access import UserRole, validate_user_change


class AccessManagement:
    def __init__(self, owner):
        self._owner = owner
        self.snapshot = None
        self.scope = None
        self.received_at = None
        self.last_error = None
        self._task = None
        self._last_attempt_scope = object()
        self._last_attempt_time = 0
        self._verified_at = 0

    def invalidate(self):
        self.snapshot = None
        self.scope = None
        self.received_at = None

    @property
    def role(self):
        owner = self._owner
        if (
            self.snapshot is None
            or self.scope is None
            or self.scope != getattr(owner.client, "access_session_scope", None)
        ):
            return UserRole.UNKNOWN
        actor = self.snapshot.principal(
            owner.entry_config.credentials.username,
            owner.entry_config.credentials.source,
        )
        return actor.role if actor else UserRole.UNKNOWN

    def schedule(self):
        scope = getattr(self._owner.client, "access_session_scope", None)
        if self._task is not None or self._owner.observations.closed:
            return
        if (
            self.snapshot is not None
            and self.scope == scope
            and time.monotonic() - self._verified_at < 300
        ):
            return
        if (
            self._last_attempt_scope == scope
            and time.monotonic() - self._last_attempt_time < 300
        ):
            return
        self._last_attempt_scope = scope
        self._last_attempt_time = time.monotonic()
        self._task = self._owner.tasks.create(
            self._background_refresh(), "nice-access-inspection"
        )

    async def _background_refresh(self):
        try:
            await self.async_refresh()
        except (NiceBidiError, OSError, HomeAssistantError) as error:
            self.last_error = type(error).__name__
        finally:
            self._task = None

    async def async_refresh(self):
        owner = self._owner
        if owner.observations.closed:
            raise HomeAssistantError("Integration is shutting down")
        generation = owner.observations.generation

        def inspect():
            snapshot = owner.client.read_pairings()
            return snapshot, getattr(owner.client, "access_session_scope", None)

        snapshot, scope = await owner.hass.async_add_executor_job(inspect)
        if scope != getattr(owner.client, "access_session_scope", None):
            raise HomeAssistantError("Access session changed during inspection")
        if generation != owner.observations.generation or owner.observations.closed:
            raise HomeAssistantError("Access inspection belongs to an obsolete session")
        self.snapshot = snapshot
        self.scope = scope
        self.received_at = datetime.now(UTC).isoformat()
        self.last_error = None
        self._verified_at = time.monotonic()
        owner.async_update_listeners()
        return snapshot

    def stationary_guard(self):
        owner = self._owner
        if owner.observations.closed:
            raise NicePermissionError("Integration is shutting down")
        if (
            owner.reported_moving
            or owner.data is None
            or owner.data.state not in {"open", "closed", "stopped", "partially_open"}
        ):
            raise NicePermissionError("A confirmed stationary gate is required")
        if owner.calibration_controller.calibration_state == "running":
            raise NicePermissionError("Access changes are blocked during calibration")

    def require_admin(self):
        if self.role is not UserRole.ADMIN or time.monotonic() - self._verified_at > 30:
            raise NicePermissionError(
                "Verified administrator permission is required; refresh paired users"
            )

    async def async_prepare_admin(self):
        self.stationary_guard()
        await self.async_refresh()
        self.require_admin()
        self.stationary_guard()

    async def async_ensure_admin(self):
        if self.role is not UserRole.ADMIN or time.monotonic() - self._verified_at > 5:
            await self.async_prepare_admin()
        else:
            self.stationary_guard()

    def mutation_guard(self):
        generation = self._owner.observations.generation
        scope = self.scope

        def guard():
            self._owner.lifecycle.require_idle_at_dispatch()
            self.stationary_guard()
            if generation != self._owner.observations.generation or scope != getattr(
                self._owner.client, "access_session_scope", None
            ):
                raise NicePermissionError("Access session changed; inspect users again")
            self.require_admin()

        return guard

    async def async_change(self, target, operation, role, *, confirmed=False):
        # Missing intent/unsupported workflows fail before inspection or queueing.
        if not confirmed:
            raise HomeAssistantError("Explicit confirmation is required")
        if operation not in {"approve", "edit"}:
            raise HomeAssistantError(
                "Ownership transfer and record deletion are unresolved"
            )
        self._owner.lifecycle.require_idle()
        self.stationary_guard()
        await self.async_refresh()
        credentials = self._owner.entry_config.credentials
        validate_user_change(
            self.snapshot,
            (credentials.username, credentials.source),
            target,
            operation,
            role,
        )
        guard = self.mutation_guard()
        snapshot = await self._owner.hass.async_add_executor_job(
            partial(
                self._owner.client.change_user,
                target,
                operation,
                role,
                before_dispatch=guard,
            )
        )
        guard()
        self.snapshot = snapshot
        self.received_at = datetime.now(UTC).isoformat()
        self._owner.async_update_listeners()

    def control_guard(self):
        if self.role in {UserRole.DISABLED, UserRole.WAIT}:
            raise HomeAssistantError(
                "The module has not granted this principal control permission"
            )

    def diagnostics(self):
        return {
            "role": str(self.role),
            "received_at": self.received_at,
            "role_age_seconds": round(max(0, time.monotonic() - self._verified_at), 1)
            if self.received_at
            else None,
            "last_error_type": self.last_error,
            "pairings": self.snapshot.summary() if self.snapshot else None,
            "identity_basis": "exact authenticated username/controllerID; unknown stays unknown",
            "user_identifiers_exported": False,
            "self_access_changes_allowed": False,
            "module_admin_is_cloud_owner": False,
        }
