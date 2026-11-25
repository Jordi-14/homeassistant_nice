"""Explicit, local administration UI; identities and lists are never saved as options."""

from __future__ import annotations

import voluptuous as vol
from functools import partial
from homeassistant.config_entries import OptionsFlow
from homeassistant.helpers import selector

from .protocol.nhk.access import UserRole
from .installer_catalog import PROCEDURES, PROCEDURES_BY_KEY
from .runtime import get_coordinator


class NiceAccessOptionsFlow(OptionsFlow):
    def __init__(self, entry):
        self._entry = entry
        self._snapshot = None
        self._target = None
        self._operation = None
        self._role = None
        self._targets = []
        self._installer_key = None

    def _owner(self):
        if getattr(self._entry, "runtime_data", None) is None:
            return None
        return get_coordinator(self._entry)

    async def async_step_init(self, user_input=None):
        if self._owner() is None:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "inspect_users",
                "ecosystem",
                "installer",
                "installer_recovery",
                "installer_cancel",
                "approve_user",
                "edit_user",
                "identify",
                "network_setup",
                "ownership",
            ],
        )

    async def async_step_inspect_users(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return await self.async_step_init()
        try:
            self._snapshot = await owner.access.async_refresh()
        except Exception:
            return self.async_show_form(
                step_id="inspect_users",
                data_schema=vol.Schema({}),
                errors={"base": "access_inspection_failed"},
                description_placeholders={
                    "role": "unverified",
                    "users": "?",
                    "pending": "?",
                    "admins": "?",
                    "records": "",
                },
            )
        summary = self._snapshot.summary()
        return self.async_show_form(
            step_id="inspect_users",
            data_schema=vol.Schema({}),
            description_placeholders={
                "role": str(owner.access.role),
                "users": str(summary["users"]),
                "pending": str(summary["pending"]),
                "admins": str(summary["roles"]["admin"]),
                "records": "\n".join(
                    f"* {user.description or user.username} / {user.username} ({user.role}) #{user.id}"
                    for user in self._snapshot.users
                )
                or "(0)",
            },
        )

    async def _select_user(self, pending, user_input, step):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        errors = {}
        if user_input is not None and self._targets:
            try:
                index = int(user_input["target"])
                if not 0 <= index < len(self._targets):
                    raise ValueError()
                self._target = self._targets[index]
                self._operation = "approve" if pending else "edit"
                self._role = UserRole.USER if pending else UserRole(user_input["role"])
            except (ValueError, KeyError):
                errors["base"] = "invalid_access_target"
            else:
                return await self.async_step_confirm_access()
        try:
            self._snapshot = await owner.access.async_refresh()
            owner.access.require_admin()
        except Exception:
            return self.async_show_form(
                step_id=step,
                data_schema=vol.Schema({}),
                errors={"base": "access_inspection_failed"},
            )
        principal = (
            owner.entry_config.credentials.username,
            owner.entry_config.credentials.source,
        )
        self._targets = [
            u
            for u in self._snapshot.users
            if u.pending == pending and u.identity != principal
        ]
        if not self._targets:
            return self.async_abort(reason="no_access_targets")
        options = [
            selector.SelectOptionDict(
                value=str(i),
                label=f"{u.description or u.username} / {u.username} ({u.role}) #{u.id}",
            )
            for i, u in enumerate(self._targets)
        ]
        schema = {
            vol.Required("target"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options, mode=selector.SelectSelectorMode.DROPDOWN
                )
            )
        }
        if not pending:
            schema[vol.Required("role", default="user")] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["admin", "user", "disable", "wait"],
                    translation_key="module_role",
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        return self.async_show_form(
            step_id=step, data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_approve_user(self, user_input=None):
        return await self._select_user(True, user_input, "approve_user")

    async def async_step_edit_user(self, user_input=None):
        return await self._select_user(False, user_input, "edit_user")

    async def async_step_confirm_access(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        if self._target is None:
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "access_confirmation_required"
            else:
                try:
                    await owner.access.async_change(
                        self._target, self._operation, self._role, confirmed=True
                    )
                except Exception:
                    errors["base"] = "access_operation_failed"
                else:
                    self._snapshot = None
                    self._target = None
                    self._targets = []
                    return self.async_create_entry(
                        title="", data=dict(self._entry.options)
                    )
        return self.async_show_form(
            step_id="confirm_access",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            errors=errors,
            description_placeholders={
                "target": self._target.description or self._target.username,
                "role": str(self._role),
            },
        )

    async def async_step_identify(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            try:
                owner.access.stationary_guard()
                await owner.hass.async_add_executor_job(
                    partial(
                        owner.client.identify_interface,
                        before_dispatch=owner.access.stationary_guard,
                    )
                )
            except Exception:
                return self.async_show_form(
                    step_id="identify",
                    data_schema=vol.Schema({}),
                    errors={"base": "identify_failed"},
                )
            return self.async_create_entry(title="", data=dict(self._entry.options))
        return self.async_show_form(step_id="identify", data_schema=vol.Schema({}))

    async def async_step_network_setup(self, user_input=None):
        return self.async_abort(reason="network_workflow_unverified")

    async def async_step_ownership(self, user_input=None):
        return self.async_abort(reason="ownership_workflow_unverified")

    async def async_step_installer(self, user_input=None):
        if self._owner() is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            key = user_input.get("procedure")
            if key not in PROCEDURES_BY_KEY:
                return self.async_abort(reason="invalid_installer_procedure")
            self._installer_key = key
            return await self.async_step_installer_action()
        return self.async_show_form(
            step_id="installer",
            data_schema=vol.Schema({vol.Required("procedure"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[selector.SelectOptionDict(value=p.key, label=p.title) for p in PROCEDURES], mode=selector.SelectSelectorMode.DROPDOWN)
            )}),
        )

    async def async_step_installer_action(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        spec = PROCEDURES_BY_KEY.get(self._installer_key)
        if spec is None:
            return await self.async_step_installer()
        if spec.gaps:
            from .feature_catalog import GAPS_BY_KEY
            if user_input is not None:
                return await self.async_step_installer()
            return self.async_show_form(
                step_id="installer_action", data_schema=vol.Schema({}),
                description_placeholders={"operation": spec.title, "details": "\n\n".join(GAPS_BY_KEY[g].missing + " " + GAPS_BY_KEY[g].completion for g in spec.gaps)},
                errors={"base": "installer_binding_unavailable"},
            )
        errors = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "installer_confirmation_required"
            else:
                try:
                    await owner.lifecycle.async_run(spec.key, confirmed=True)
                except Exception:
                    errors["base"] = "installer_operation_failed"
                else:
                    return self.async_show_form(
                        step_id="installer_result", data_schema=vol.Schema({}),
                        description_placeholders={"operation": spec.title, "summary": owner.lifecycle.summary()},
                    )
        return self.async_show_form(
            step_id="installer_action",
            data_schema=vol.Schema({vol.Required("confirm", default=False): selector.BooleanSelector()}),
            description_placeholders={"operation": spec.title, "details": f"Prerequisites: {', '.join(spec.prerequisites) or 'configured access'}. {spec.completion}. Recovery: {spec.recovery}."},
            errors=errors,
        )

    async def async_step_installer_result(self, user_input=None):
        return await self.async_step_init()

    async def async_step_installer_recovery(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        errors = {}
        if user_input is not None:
            try:
                await owner.lifecycle.async_acknowledge_recovery(confirmed=user_input.get("confirm", False))
            except Exception:
                errors["base"] = "installer_recovery_failed"
            else:
                return await self.async_step_init()
        return self.async_show_form(
            step_id="installer_recovery",
            data_schema=vol.Schema({vol.Required("confirm", default=False): selector.BooleanSelector()}),
            errors=errors,
        )

    async def async_step_installer_cancel(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            try:
                owner.lifecycle.runner.request_cancel()
            except Exception:
                return self.async_abort(reason="installer_cancel_unavailable")
            return await self.async_step_init()
        return self.async_show_form(step_id="installer_cancel", data_schema=vol.Schema({}))

    async def async_step_ecosystem(self, user_input=None):
        if self._owner() is None:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(step_id="ecosystem", menu_options=["ecosystem_controller", "ecosystem_cloud", "ecosystem_report", "ecosystem_core", "ecosystem_ha"])

    async def async_step_ecosystem_cloud(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        errors = {}
        if user_input is not None:
            login = None
            try:
                if user_input.get("confirm") is not True:
                    errors["base"] = "ecosystem_consent_required"
                else:
                    from .cloud.models import NiceCloudLogin
                    login = NiceCloudLogin(str(user_input.get("account", "")).strip(), str(user_input.get("account_password", "")))
                    await owner.ecosystem.async_inspect(login, confirmed=True)
                    return await self.async_step_ecosystem_report()
            except ValueError:
                errors["base"] = "ecosystem_invalid_login"
            except Exception:
                errors["base"] = "ecosystem_inspection_failed"
            finally:
                if login is not None:
                    login.clear()
                for key in ("account", "account_password"):
                    user_input[key] = ""
        return self.async_show_form(
            step_id="ecosystem_cloud",
            data_schema=vol.Schema({
                vol.Required("account"): selector.TextSelector(),
                vol.Required("account_password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
                vol.Required("confirm", default=False): selector.BooleanSelector(),
            }), errors=errors,
        )

    async def async_step_ecosystem_report(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return await self.async_step_ecosystem()
        snapshot = owner.ecosystem.snapshot
        return self.async_show_form(
            step_id="ecosystem_report", data_schema=vol.Schema({}),
            description_placeholders={
                "captured_at": snapshot.captured_at if snapshot else "not inspected",
                "state": snapshot.state if snapshot else "not_inspected",
                "records": snapshot.private_lines() if snapshot else "No cloud inspection has been requested.",
                "last_error": owner.ecosystem.last_error_type or "none",
            },
        )

    async def async_step_ecosystem_ha(self, user_input=None):
        if self._owner() is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return await self.async_step_ecosystem()
        return self.async_show_form(step_id="ecosystem_ha", data_schema=vol.Schema({}))

    async def async_step_ecosystem_controller(self, user_input=None):
        owner = self._owner()
        if owner is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return await self.async_step_ecosystem()
        records = owner.ecosystem.controller_behavior()["settings"]
        text = "\n\n".join(
            f"{name.replace('_', ' ')}: {str(row['enabled_reported']) if row['enabled_reported'] is not None else 'unknown'}; observed: {row['observed_at'] or 'not observed'}"
            for name, row in records.items()
        )
        return self.async_show_form(step_id="ecosystem_controller",data_schema=vol.Schema({}),description_placeholders={"records":text})

    async def async_step_ecosystem_core(self, user_input=None):
        if self._owner() is None:
            return self.async_abort(reason="not_loaded")
        return self.async_abort(reason="ecosystem_core_unverified")
