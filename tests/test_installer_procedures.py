"""Procedure ownership, dispatch durability, recovery and cancellation boundaries."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from custom_components.nice_bidiwifi.feature_catalog import FeatureBlocked
from custom_components.nice_bidiwifi.installer_catalog import (
    PROCEDURES,
    InstallerProcedure,
    InstallerStep,
)
from custom_components.nice_bidiwifi.procedures import (
    ProcedureRecord,
    ProcedureRunner,
    ProcedureTimeout,
)


def runner(handlers, *, prepare=None, guard=None, persist=None):
    return ProcedureRunner(
        handlers,
        prepare=prepare or AsyncMock(),
        guard=guard or (lambda spec: None),
        persist=persist or AsyncMock(),
    )


def spec(*steps):
    return InstallerProcedure(
        "test",
        "Test",
        steps,
        (),
        "Observed result",
        "Explicit inspection without replay",
        state_machine_verified=True,
    )


@pytest.mark.parametrize(
    "procedure", [p for p in PROCEDURES if p.gaps], ids=lambda p: p.key
)
async def test_unresolved_procedure_does_no_preparation_storage_or_io(procedure):
    prepare, persist, handler = AsyncMock(), AsyncMock(), AsyncMock()
    instance = runner({"anything": handler}, prepare=prepare, persist=persist)
    with pytest.raises(FeatureBlocked):
        await instance.run(procedure, confirmed=True)
    prepare.assert_not_awaited()
    persist.assert_not_awaited()
    handler.assert_not_awaited()
    assert not instance.busy and instance.record is None


async def test_mutation_intent_is_saved_before_dispatch_and_success_is_only_acknowledged():
    saved, writes = [], []

    async def persist(record):
        saved.append(record.copy())

    async def mutate():
        assert saved[-1]["state"] == "running" and saved[-1]["dispatched"]
        writes.append(1)
        return "ack"

    instance = runner({"write": mutate}, persist=persist)
    procedure = spec(InstallerStep("submit", "write", True))
    with pytest.raises(FeatureBlocked, match="confirmation"):
        await instance.run(procedure)
    assert not saved and not writes
    assert await instance.run(procedure, confirmed=True) == "ack"
    assert writes == [1]
    assert instance.record.state == "acknowledged"
    assert instance.record.completion_basis == "Observed result"


async def test_failed_durable_intent_prevents_wire_dispatch():
    mutate = AsyncMock()
    instance = runner(
        {"write": mutate}, persist=AsyncMock(side_effect=OSError("storage unavailable"))
    )
    with pytest.raises(OSError):
        await instance.run(spec(InstallerStep("submit", "write", True)), confirmed=True)
    mutate.assert_not_awaited()
    assert not instance.record.dispatched and not instance.busy


async def test_uncertain_delivery_persists_recovery_and_never_retries():
    mutate = AsyncMock(side_effect=OSError("socket closed"))
    instance = runner({"write": mutate})
    procedure = spec(InstallerStep("submit", "write", True))
    with pytest.raises(OSError):
        await instance.run(procedure, confirmed=True)
    assert (
        instance.record.recovery_required and instance.record.state == "needs_recovery"
    )
    with pytest.raises(FeatureBlocked, match="interrupted"):
        await instance.run(procedure, confirmed=True)
    assert mutate.await_count == 1


async def test_known_failure_before_dispatch_does_not_claim_a_mutation():
    error = ValueError("Guard rejected")
    error.delivery = "not_sent"
    instance = runner({"write": AsyncMock(side_effect=error)})
    with pytest.raises(ValueError):
        await instance.run(spec(InstallerStep("submit", "write", True)), confirmed=True)
    assert not instance.record.dispatched and not instance.record.recovery_required


async def test_worker_timeout_drains_before_releasing_procedure_ownership():
    entered, release = asyncio.Event(), asyncio.Event()

    async def mutate():
        entered.set()
        await release.wait()

    instance = runner({"write": mutate})
    task = asyncio.create_task(
        instance.run(
            spec(InstallerStep("submit", "write", True, timeout=0.01)), confirmed=True
        )
    )
    await entered.wait()
    await asyncio.sleep(0.03)
    assert instance.busy and not task.done()
    with pytest.raises(FeatureBlocked):
        await instance.run(spec(InstallerStep("read", "write")))
    release.set()
    with pytest.raises(ProcedureTimeout):
        await task
    assert not instance.busy and instance.record.recovery_required


async def test_cancelling_await_does_not_detach_live_worker():
    entered, release = asyncio.Event(), asyncio.Event()

    async def mutate():
        entered.set()
        await release.wait()

    instance = runner({"write": mutate})
    task = asyncio.create_task(
        instance.run(spec(InstallerStep("submit", "write", True)), confirmed=True)
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert instance.busy
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert instance.record.recovery_required


async def test_irreversible_step_rejects_cancel_without_sending_abort():
    entered, release = asyncio.Event(), asyncio.Event()

    async def mutate():
        entered.set()
        await release.wait()

    instance = runner({"write": mutate})
    task = asyncio.create_task(
        instance.run(
            spec(InstallerStep("submit", "write", True, cancellable_before=False)),
            confirmed=True,
        )
    )
    await entered.wait()
    with pytest.raises(FeatureBlocked, match="cancelled"):
        instance.request_cancel()
    release.set()
    await task
    assert instance.record.state == "acknowledged"


async def test_read_cancellation_stops_at_boundary_and_runs_no_later_step():
    entered, release = asyncio.Event(), asyncio.Event()

    async def read():
        entered.set()
        await release.wait()

    next_step = AsyncMock()
    instance = runner({"read": read, "next": next_step})
    task = asyncio.create_task(
        instance.run(spec(InstallerStep("read", "read"), InstallerStep("next", "next")))
    )
    await entered.wait()
    assert instance.request_cancel()
    release.set()
    await task
    assert instance.record.state == "cancelled"
    next_step.assert_not_awaited()


@pytest.mark.parametrize("dispatched", [True, False])
def test_crash_recovery_never_replays_saved_steps(dispatched):
    record = ProcedureRecord(
        "restart_interface",
        state="running",
        step="send_restart_once",
        dispatched=dispatched,
    )
    instance = runner({})
    instance.restore(record.export())
    assert instance.record.recovery_required is dispatched
    assert instance.record.state == ("needs_recovery" if dispatched else "interrupted")


@pytest.mark.parametrize(
    "raw", [{}, [], {"key": "secret"}, ProcedureRecord("unknown").export()]
)
def test_corrupt_journal_is_not_silently_discarded(raw):
    instance = runner({})
    instance.restore(raw)
    assert instance.record.recovery_required
    assert instance.record.error_type == "InvalidJournal"
    assert "secret" not in str(instance.record.export())


async def test_inspection_does_not_erase_unresolved_mutation_record():
    instance = runner({"read": AsyncMock(return_value="new info")})
    old = instance.record = ProcedureRecord(
        "restart_interface",
        state="needs_recovery",
        dispatched=True,
        recovery_required=True,
    )
    assert await instance.run(spec(InstallerStep("read", "read"))) == "new info"
    assert instance.record is old and instance.record.recovery_required


@pytest.mark.parametrize(
    "change",
    [
        {"completion": None},
        {"recovery": None},
        {"state_machine_verified": False},
        {"steps": (InstallerStep("unknown", None),)},
    ],
)
async def test_mandatory_bindings_block_before_preparation(change):
    prepare = AsyncMock()
    instance = runner({"read": AsyncMock()}, prepare=prepare)
    with pytest.raises(FeatureBlocked):
        await instance.run(replace(spec(InstallerStep("read", "read")), **change))
    prepare.assert_not_awaited()


def test_reloaded_recovery_acknowledgement_does_not_become_a_device_ack():
    instance = runner({})
    raw = ProcedureRecord(
        "restart_interface",
        state="recovery_acknowledged",
        dispatched=True,
        completion_basis="untrusted stored prose",
    ).export()
    instance.restore(raw)
    assert "outcome remains unverified" in instance.record.completion_basis
    assert "untrusted" not in instance.record.completion_basis


async def test_cancellation_during_intent_storage_drains_writer_before_releasing_owner():
    entered, release = asyncio.Event(), asyncio.Event()
    saves = []

    async def persist(record):
        saves.append(record)
        if len(saves) == 1:
            entered.set()
            await release.wait()

    mutate = AsyncMock()
    instance = runner({"write": mutate}, persist=persist)
    task = asyncio.create_task(
        instance.run(spec(InstallerStep("submit", "write", True)), confirmed=True)
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert instance.busy and not task.done()
    mutate.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    mutate.assert_not_awaited()
    assert not instance.busy and not instance.record.dispatched


async def test_missing_callable_binding_blocks_before_persist():
    persist = AsyncMock()
    instance = runner({"write": None}, persist=persist)
    with pytest.raises(FeatureBlocked):
        await instance.run(spec(InstallerStep("submit", "write", True)), confirmed=True)
    persist.assert_not_awaited()
