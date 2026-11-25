"""Exactly-once procedure steps, durable mutation intent and honest cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Awaitable, Callable

from .feature_catalog import FeatureBlocked
from .installer_catalog import InstallerProcedure, PROCEDURES_BY_KEY


class ProcedureTimeout(TimeoutError):
    """The deadline expired; the in-flight worker was drained, never replayed."""


def utc_now():
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ProcedureRecord:
    key: str
    state: str = "preparing"
    step: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    dispatched: bool = False
    recovery_required: bool = False
    cancellation_requested: bool = False
    error_type: str | None = None
    completion_basis: str | None = None

    def export(self):
        return asdict(self)


async def drain(task):
    """Cancelling an await does not cancel executor I/O or free its ownership."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    try:
        return task.result()
    except (Exception, asyncio.CancelledError):
        return None


class ProcedureRunner:
    def __init__(self, handlers, *, prepare, guard, persist, notify=lambda: None):
        self.handlers: dict[str, Callable[[], Awaitable]] = handlers
        self.prepare = prepare
        self.guard = guard
        self.persist = persist
        self.notify = notify
        self.record: ProcedureRecord | None = None
        self.busy = False
        self.closing = False
        self._cancel_requested = False
        self._finished = asyncio.Event()
        self._finished.set()
        self._spec: InstallerProcedure | None = None
        self.result = None

    async def _save(self, durable):
        self.record.updated_at = utc_now()
        if durable:
            # Do not let a cancelled Store await leave an old journal writer
            # running after a new procedure or config-entry owner has started.
            writer = asyncio.create_task(self.persist(self.record.export()))
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                await drain(writer)
                raise
        self.notify()

    def request_cancel(self):
        if not self.busy or self._spec is None:
            return False
        # A submitted irreversible request cannot be undone by cancelling HA.
        if (
            self.record.dispatched
            or self._spec.mutates
            and any(
                not s.cancellable_before
                for s in self._spec.steps
                if s.key == self.record.step
            )
        ):
            raise FeatureBlocked(
                "The current step cannot be safely cancelled; no abort opcode is verified"
            )
        self._cancel_requested = True
        self.record.cancellation_requested = True
        self.notify()
        return True

    async def run(self, spec, *, confirmed=False):
        # Fail before storage, queues, cancellation or network activity.
        spec.preflight(self.handlers)
        if spec.mutates and confirmed is not True:
            raise FeatureBlocked("Explicit procedure confirmation is required")
        if self.closing or self.busy:
            raise FeatureBlocked(
                "Another procedure is active or the integration is closing"
            )
        if self.record is not None and self.record.recovery_required and spec.mutates:
            raise FeatureBlocked(
                "Inspect and acknowledge the previous interrupted procedure first"
            )
        previous = self.record
        self.busy = True
        self._finished.clear()
        self._spec = spec
        self._cancel_requested = False
        record = self.record = ProcedureRecord(spec.key)
        self.result = None
        submitted = False
        completed_mutations = False
        try:
            await self.prepare(spec)
            await self._save(spec.mutates)
            for step in spec.steps:
                if self.closing or self._cancel_requested:
                    record.recovery_required = completed_mutations
                    record.state = (
                        "needs_recovery" if completed_mutations else "cancelled"
                    )
                    await self._save(spec.mutates)
                    return None
                self.guard(spec)
                record.state, record.step = "running", step.key
                if step.mutates:
                    record.dispatched = True
                await self._save(spec.mutates)
                submitted = step.mutates
                worker = asyncio.create_task(self.handlers[step.handler]())
                try:
                    value = await asyncio.wait_for(asyncio.shield(worker), step.timeout)
                except TimeoutError as error:
                    await drain(worker)
                    raise ProcedureTimeout(
                        "Procedure step exceeded its deadline"
                    ) from error
                except asyncio.CancelledError:
                    await drain(worker)
                    raise
                if not step.mutates:
                    self.guard(spec)
                else:
                    completed_mutations = True
                self.result = value
                record.completed_steps.append(step.key)
                await self._save(spec.mutates)
            record.state = "acknowledged" if spec.mutates else "completed"
            record.completion_basis = spec.completion
            await self._save(spec.mutates)
            return self.result
        except BaseException as error:
            # A failed Store write before dispatch cannot itself move hardware.
            not_sent = getattr(error, "delivery", None) == "not_sent"
            record.dispatched = completed_mutations or submitted and not not_sent
            record.recovery_required = record.dispatched
            record.state = (
                "needs_recovery"
                if record.recovery_required
                else "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "failed"
            )
            record.error_type = type(error).__name__
            try:
                await self._save(spec.mutates)
            except Exception:
                # The pre-dispatch durable marker remains authoritative on reload.
                record.recovery_required = record.recovery_required or record.dispatched
            raise
        finally:
            # Inspection must not erase an unresolved mutation journal.
            if previous is not None and previous.recovery_required and not spec.mutates:
                self.record = previous
            self.busy = False
            self._spec = None
            self._finished.set()
            self.notify()

    async def reconcile(self, spec, operation):
        """Own inspection and journal reconciliation as one serialized operation."""
        spec.preflight(self.handlers)
        if spec.mutates or self.busy or self.closing:
            raise FeatureBlocked(
                "Recovery inspection cannot start in the current state"
            )
        self.busy = True
        self._spec = spec
        self._finished.clear()
        worker = asyncio.create_task(operation())
        try:
            return await asyncio.wait_for(
                asyncio.shield(worker), sum(s.timeout for s in spec.steps)
            )
        except TimeoutError as error:
            await drain(worker)
            raise ProcedureTimeout(
                "Recovery inspection exceeded its deadline"
            ) from error
        except asyncio.CancelledError:
            await drain(worker)
            raise
        finally:
            self.busy = False
            self._spec = None
            self._finished.set()
            self.notify()

    def restore(self, raw):
        if raw is None:
            return
        fields = set(ProcedureRecord.__dataclass_fields__)
        if not isinstance(raw, dict) or set(raw) != fields:
            self.record = ProcedureRecord(
                "unrecognized",
                state="needs_recovery",
                recovery_required=True,
                error_type="InvalidJournal",
            )
            return
        try:
            spec = PROCEDURES_BY_KEY.get(raw["key"])
            if spec is None or raw["state"] not in {
                "preparing",
                "running",
                "completed",
                "acknowledged",
                "cancelled",
                "failed",
                "interrupted",
                "needs_recovery",
                "recovery_acknowledged",
            }:
                raise ValueError()
            valid_steps = {step.key for step in spec.steps}
            if (
                raw["step"] is not None
                and raw["step"] not in valid_steps
                or not set(raw["completed_steps"]) <= valid_steps
            ):
                raise ValueError()
            for key in ["started_at", "updated_at"]:
                datetime.fromisoformat(raw[key])
            if raw["error_type"] is not None and (
                not isinstance(raw["error_type"], str)
                or not raw["error_type"].isidentifier()
            ):
                raise ValueError()
            if (
                not isinstance(raw["completed_steps"], list)
                or len(raw["completed_steps"]) > 32
                or any(
                    not isinstance(x, str) or len(x) > 96
                    for x in raw["completed_steps"]
                )
            ):
                raise ValueError()
            if any(
                not isinstance(raw[k], bool)
                for k in ["dispatched", "recovery_required", "cancellation_requested"]
            ):
                raise ValueError()
            if any(
                raw[k] is not None
                and (not isinstance(raw[k], str) or len(raw[k]) > 512)
                for k in fields
                - {
                    "completed_steps",
                    "dispatched",
                    "recovery_required",
                    "cancellation_requested",
                }
            ):
                raise ValueError()
            record = ProcedureRecord(**raw)
            record.completion_basis = (
                "Operator checked the module; previous physical restart outcome remains unverified"
                if record.state == "recovery_acknowledged"
                else spec.completion
                if record.state in {"completed", "acknowledged"}
                else None
            )
        except (ValueError, TypeError):
            self.record = ProcedureRecord(
                "unrecognized",
                state="needs_recovery",
                recovery_required=True,
                error_type="InvalidJournal",
            )
            return
        if record.state in {"preparing", "running"}:
            record.state = "needs_recovery" if record.dispatched else "interrupted"
            record.recovery_required = record.dispatched
            record.error_type = "InterruptedProcedure"
        self.record = record

    async def close(self):
        self.closing = True
        self._cancel_requested = True
        if self.busy:
            await self._finished.wait()
