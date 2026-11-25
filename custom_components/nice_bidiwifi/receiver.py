"""Bounded passive receiver inspection with separate authentication/layout gaps."""

from datetime import UTC, datetime
import asyncio
from functools import partial
import time

from .errors import NiceParameterError
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

    def invalidate(self):
        self.generation += 1
        self.snapshot = None
        self.captured = None
        self.scope = None
        self.memory_page = None
        self.protocols = None

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
            or result.get("session_scope")
            != getattr(self.owner.client, "access_session_scope", None)
        ):
            raise NiceParameterError("Receiver result belongs to an obsolete session")
        value = dict(result)
        value.pop("session_scope", None)
        value["received_at"] = datetime.now(UTC).isoformat()
        return value

    async def async_memory_page(self, start=0, count=8):
        async with self._lock:
            self.memory_page = None
            await self._prepare_operator_read()
            generation = self.generation
            observation_generation = self.owner.observations.generation
            result = await self.owner.hass.async_add_executor_job(
                self.owner.client.read_receiver_memory_page, start, count
            )
            self.memory_page = self._accept_operator_result(
                result, generation, observation_generation
            )
            return dict(self.memory_page)

    async def async_protocols(self, *, confirmed=False):
        if not confirmed:
            raise NiceParameterError("Confirm the receiver authentication inspection")
        async with self._lock:
            self.protocols = None
            await self._prepare_operator_read()
            generation = self.generation
            observation_generation = self.owner.observations.generation
            guard = self.owner.access.mutation_guard()

            def before_dispatch():
                if not self.owner.controller_menu.enabled:
                    raise NiceParameterError("Pro controls were disabled")
                guard()

            result = await self.owner.hass.async_add_executor_job(
                partial(
                    self.owner.client.inspect_receiver_protocols,
                    before_dispatch=before_dispatch,
                )
            )
            self.protocols = self._accept_operator_result(
                result, generation, observation_generation
            )
            return dict(self.protocols)

    def report(self, *, include_memory=False):
        if self.snapshot is None:
            result = {
                "state": "unavailable",
                "reason": "No receiver inspection completed",
                "last_error": self.last_error,
            }
            if include_memory:
                result.update(memory_page=self.memory_page, protocols=self.protocols)
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
            result.update(memory_page=self.memory_page, protocols=self.protocols)
        return result
