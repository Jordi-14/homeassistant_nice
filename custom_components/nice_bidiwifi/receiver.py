"""Bounded passive receiver inspection with separate authentication/layout gaps."""

from datetime import UTC, datetime
import asyncio
from functools import partial
import time

from .errors import (
    NiceParameterError,
    NiceResponseError,
    NicePermissionError,
    NiceUnsupportedError,
    NiceReceiverFormatError,
)
from .models.capabilities import ProductFamily


class ReceiverRuntime:
    def __init__(self, owner):
        self.owner = owner
        self.snapshot = None
        self.captured = None
        self.received_at = None
        self.scope = None
        self.generation = 0
        self.last_error = None
        self._lock = asyncio.Lock()
        self.memory_page = None
        self.protocols = None
        self.operator_errors = {}
        self._operator_scopes = {}

    def invalidate(self):
        self.generation += 1
        self.snapshot = None
        self.captured = None
        self.scope = None
        self.memory_page = None
        self.protocols = None
        self.operator_errors = {}
        self._operator_scopes = {}

    async def async_inspect(self, *, background=True):
        async with self._lock:
            return await self._async_inspect_locked(background=background)

    async def _async_inspect_locked(self, *, background):
        owner = self.owner
        if owner.capabilities is not None and owner.capabilities.family in {
            ProductFamily.CORE,
            ProductFamily.PROVIEW,
        }:
            raise NiceParameterError(
                "Receiver transport adapter is not implemented for this product family"
            )
        owner.access.stationary_guard()
        owner.lifecycle.require_idle()
        generation = self.generation
        observation_generation = owner.observations.generation
        try:
            result = await owner.hass.async_add_executor_job(
                partial(owner.client.read_receiver_diagnostics, background=background)
            )
            if (
                owner.observations.closed
                or generation != self.generation
                or observation_generation != owner.observations.generation
                or result.get("session_scope") is None
                or result["session_scope"]
                != getattr(owner.client, "access_session_scope", None)
            ):
                raise NiceParameterError(
                    "Receiver inspection belongs to an obsolete session"
                )
            self.scope = result.pop("session_scope")
            self.snapshot = result
            self.captured = time.monotonic()
            self.received_at = datetime.now(UTC).isoformat()
            self.last_error = None
            owner.async_update_listeners()
            return self.report()
        except Exception as error:
            self.invalidate()
            self.last_error = type(error).__name__
            raise

    async def _prepare_operator_read(self):
        if not self.owner.controller_menu.enabled:
            raise NiceParameterError("Enable Pro controls before receiver management")
        self.owner.lifecycle.require_idle()
        await self.owner.access.async_prepare_admin()

    def _accept_operator_result(self, result, generation, observation_generation):
        if (
            self.owner.observations.closed
            or generation != self.generation
            or observation_generation != self.owner.observations.generation
            or result.get("session_scope") is None
            or result.get("session_scope")
            != getattr(self.owner.client, "access_session_scope", None)
        ):
            raise NiceParameterError("Receiver result belongs to an obsolete session")
        value = dict(result)
        value.pop("session_scope", None)
        value["received_at"] = datetime.now(UTC).isoformat()
        return value

    def _operator_context(self):
        return (
            self.generation,
            self.owner.observations.generation,
            getattr(self.owner.client, "access_session_scope", None),
        )

    def _store_operator_result(self, key, result, generation, observation_generation):
        value = self._accept_operator_result(result, generation, observation_generation)
        setattr(self, key, value)
        self._operator_scopes[key] = self._operator_context()
        self.operator_errors.pop(key, None)
        return dict(value)

    def _record_operator_error(self, key, error):
        # Device exception text can contain request details. Keep only safe fields.
        top_error_type = type(error).__name__
        for _ in range(4):
            if getattr(error, "inspection_stage", None) or getattr(error, "code", None):
                break
            cause = error.__cause__
            if cause is None:
                break
            error = cause
        register = error.register if isinstance(error, NiceResponseError) else None
        self.operator_errors[key] = {
            "state": "unverified_format"
            if isinstance(error, NiceReceiverFormatError)
            else "unsupported"
            if isinstance(error, NiceUnsupportedError)
            or isinstance(error, NiceResponseError)
            and error.unsupported
            else "denied"
            if isinstance(error, NicePermissionError)
            else "unavailable",
            "error_type": type(error).__name__,
            "outer_error_type": top_error_type,
            "code": getattr(error, "code", None),
            "stage": getattr(error, "inspection_stage", None),
            "shape": getattr(error, "inspection_evidence", None),
            "layer": getattr(error, "layer", None),
            "register": "/".join(f"{part:02X}" for part in register)
            if register is not None
            else None,
            "received_at": datetime.now(UTC).isoformat(),
        }

    async def _operator_read(self, key, operation):
        async with self._lock:
            setattr(self, key, None)
            self._operator_scopes.pop(key, None)
            try:
                await self._prepare_operator_read()
                generation = self.generation
                observation_generation = self.owner.observations.generation
                guard = self.owner.access.mutation_guard()

                def before_dispatch():
                    if not self.owner.controller_menu.enabled:
                        raise NiceParameterError("Pro controls were disabled")
                    guard()

                result = await self.owner.hass.async_add_executor_job(
                    operation(before_dispatch)
                )
                return self._store_operator_result(
                    key, result, generation, observation_generation
                )
            except Exception as error:
                self._record_operator_error(key, error)
                raise
            finally:
                self.owner.async_update_listeners()

    async def async_memory_page(self, start=0, count=8):
        return await self._operator_read(
            "memory_page",
            lambda guard: partial(
                self.owner.client.read_receiver_memory_page, start, count
            ),
        )

    async def async_protocols(self, *, confirmed=False):
        if not confirmed:
            raise NiceParameterError("Confirm the receiver authentication inspection")
        return await self._operator_read(
            "protocols",
            lambda guard: partial(
                self.owner.client.inspect_receiver_protocols, before_dispatch=guard
            ),
        )

    def _operator_report(self):
        current = self._operator_context()
        return {
            key: getattr(self, key)
            if self._operator_scopes.get(key) == current
            and not self.owner.observations.closed
            else None
            for key in ("memory_page", "protocols")
        } | {"operator_errors": dict(self.operator_errors)}

    def report(self, *, include_memory=False):
        if self.snapshot is None:
            result = {
                "state": "unavailable",
                "reason": "No receiver inspection completed",
                "last_error": self.last_error,
            }
            if include_memory:
                result.update(self._operator_report())
            return result
        result = dict(self.snapshot)
        stale = (
            self.scope != getattr(self.owner.client, "access_session_scope", None)
            or self.owner.observations.closed
            or time.monotonic() - self.captured > 300
        )
        result.update(
            received_at=self.received_at,
            age_seconds=round(max(0, time.monotonic() - self.captured), 3),
            source="addressed_receiver_GET",
            identity_basis="bus_profile_and_model_not_serial_identity",
            physical_cause="unknown",
            configuration_changed=False,
        )
        if stale:
            result["state"] = "stale"
        if include_memory:
            result.update(self._operator_report())
        return result
