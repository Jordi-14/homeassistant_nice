"""Pro-style menu controls, scoped to the authenticated module and controller.

The selected installer column is an interface profile, not a device-role token.
Actual device denials, fresh module-admin access and all write guards still apply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
import time
import asyncio
from datetime import timedelta
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .controller_catalog import PARAMETERS_BY_REGISTER
from homeassistant.exceptions import HomeAssistantError

from .errors import NiceBidiError, NiceParameterError
from .protocol.t4.menu import MenuEntry, MenuPermission, MenuRole

MENU_MAX_AGE = 30
MENU_REFRESH_AGE = 10
MENU_RETRY_INTERVAL = 10


CONF_PRO_CONTROLS = "pro_controls"


class ControllerMenu:
    def __init__(self, owner):
        self._owner = owner
        self.snapshot = None
        self.received_at = None
        self.captured = None
        self.identity = None
        self._lock = asyncio.Lock()
        self._remove_tick = None
        self.last_error = None
        self._task = None
        self._last_attempt = float("-inf")

    @property
    def enabled(self):
        return self._owner.config_entry.options.get(CONF_PRO_CONTROLS, False) is True

    def start(self):
        self.close()
        if self.enabled and not self._owner.observations.closed:
            self._remove_tick = async_track_time_interval(
                self._owner.hass, self._tick, timedelta(seconds=5)
            )
            self.schedule()

    def close(self):
        if self._remove_tick is not None:
            self._remove_tick()
            self._remove_tick = None

    @callback
    def _tick(self, now):
        if self._owner.observations.closed or not self.enabled:
            self.close()
            return
        self._owner.async_update_listeners()
        self.schedule()
        self._owner.configuration.schedule_pro_metadata()

    def schedule(self):
        owner = self._owner
        if (
            not self.enabled
            or owner.observations.closed
            or owner.reported_moving
            or owner.calibration_controller.calibration_state == "running"
            or self._task is not None
            or time.monotonic() - self._last_attempt < MENU_RETRY_INTERVAL
            or self.valid()
            and time.monotonic() - self.captured < MENU_REFRESH_AGE
        ):
            return
        self._task = owner.tasks.create(
            self._background_refresh(), "nice-controller-menu"
        )

    async def _background_refresh(self):
        try:
            # Permission renewal must not sit behind optional diagnostic sweeps.
            # READBACK remains below STOP and CONTROL, and paging is preemptible.
            await self.async_refresh(background=False)
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
    def profile(self):
        if (
            not self.enabled
            or not self.valid()
            or not all(self.identity[1:])
            or not 0
            <= time.monotonic() - self._owner.access._verified_at
            <= MENU_MAX_AGE
        ):
            return MenuRole.UNKNOWN
        try:
            self._owner.access.require_admin()
        except NiceBidiError:
            return MenuRole.UNKNOWN
        return MenuRole.INSTALLER

    async def async_refresh(self, *, background=False):
        async with self._lock:
            return await self._async_refresh_locked(background=background)

    async def _async_refresh_locked(self, *, background):
        owner = self._owner
        if self.enabled:
            await owner.access.async_prepare_admin()
        owner.access.stationary_guard()
        owner.lifecycle.require_idle()
        self._last_attempt = time.monotonic()
        generation = owner.observations.generation
        identity = self.device_identity()
        # Keep a still-valid snapshot visible while its replacement is read.
        # Public writes await this serialized refresh; failures still revoke it.
        if not self.valid():
            self.invalidate()
        try:
            snapshot = await owner.hass.async_add_executor_job(
                partial(
                    owner.client.read_controller_menu,
                    preemptible=True,
                    background=background,
                )
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
            owner.configuration.sync_session()
            self.apply_profile()
            owner.configuration.schedule_pro_metadata()
            self.last_error = None
            owner.async_update_listeners()
            return snapshot
        except Exception as error:
            self.invalidate()
            self.last_error = type(error).__name__
            raise

    def apply_profile(self):
        self._owner.parameters.invalidate_menu_permissions()
        profile = self.profile
        if profile is MenuRole.UNKNOWN:
            return
        for row in {row.binding: row for row in self.snapshot.records}.values():
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
            self._owner.parameters.record_menu_permission(
                self._owner.parameters.address(*row.binding),
                MenuPermission(
                    self.snapshot.device_id,
                    row.binding,
                    profile,
                    entry,
                    self._owner.parameters.menu_generation,
                ),
            )

    def requires_menu(self, setting):
        return setting.requires_menu_permission or self.enabled

    def require_permission(self, setting):
        if not self.enabled:
            raise NiceParameterError(
                "Pro controls are disabled; enable controller menu permissions in integration options"
            )
        if not self.valid():
            raise NiceParameterError("Fresh controller menu permissions are required")
        if self.profile is MenuRole.UNKNOWN:
            raise NiceParameterError(
                "Fresh module administrator permission and controller identity are required"
            )
        row = next(
            (
                r
                for r in self.snapshot.records
                if r.binding == (setting.group, setting.parameter)
            ),
            None,
        )
        if row is None or row.is_group or not getattr(row, f"{self.profile}_set"):
            raise NiceParameterError(
                "Controller menu does not advertise this write for the selected Pro profile"
            )

    async def async_prepare_write(self, setting):
        if not self.enabled:
            raise NiceParameterError(
                "Enable Pro controls in integration options to use these settings"
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
            else "controls_disabled"
            if not self.enabled
            else "admin_required"
            if self.profile is MenuRole.UNKNOWN
            else "available",
            "pro_controls_enabled": self.enabled,
            "menu_profile": str(self.profile),
            "profile_basis": "explicit_Pro_menu_selection",
            "device_authorization": "device_response_required",
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
