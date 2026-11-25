"""Bounded passive receiver inspection with separate authentication/layout gaps."""

from datetime import UTC, datetime
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

    def invalidate(self):
        self.generation += 1
        self.snapshot = None
        self.captured = None
        self.scope = None

    async def async_inspect(self, *, background=True):
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

    def report(self):
        if self.snapshot is None:
            return {
                "state": "unavailable",
                "reason": "No receiver inspection completed",
                "last_error": self.last_error,
            }
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
        return result
