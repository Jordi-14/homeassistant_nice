"""Regressions for ordering, lifecycle and the fail-closed extension boundary."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.feature_catalog import (
    FEATURES,
    FEATURES_BY_KEY,
    GAPS_BY_KEY,
    FeatureBinding,
    FeatureBlocked,
    Implementation,
    app_inventory,
)
from custom_components.nice_bidiwifi.feature_runtime import (
    CORE_ACCESS,
    FORCE_MAP_PROCEDURE,
    PROVIEW_ACCESS,
    BlockedAccessAdapter,
    ProcedureDefinition,
)
from custom_components.nice_bidiwifi.models.capabilities import NiceCapabilities
from custom_components.nice_bidiwifi.observations import ObservationStore
from custom_components.nice_bidiwifi.protocol.nhk.status import parse_nhk_status_xml
from custom_components.nice_bidiwifi.runtime_tasks import RuntimeTasks
from custom_components.nice_bidiwifi.scheduler import (
    OperationExpired,
    OperationPriority,
    SessionScheduler,
)
from scripts.render_capabilities import ROOT, documents
from tests.conftest import FakeClient, make_device_info, make_status
from tests.test_coordinator import _coordinator


def test_inventory_has_destination_and_provenance_for_every_item():
    entries = app_inventory()["entries"]
    assert len(entries) > 3000
    assert len({entry["id"] for entry in entries}) == len(entries)
    for entry in entries:
        assert entry["destination"] in FEATURES_BY_KEY
        assert entry["phase"] == FEATURES_BY_KEY[entry["destination"]].phase
        assert set(entry["gaps"]) <= GAPS_BY_KEY.keys()
        assert len(entry["sha256"]) == 64
        assert not entry["source"].startswith("/")
    commands = [entry for entry in entries if entry["kind"] == "controller_command"]
    assert len(commands) == 357
    assert any(entry["symbol"] == "CTRL_STOP" for entry in commands)
    assert any(entry["app_command_id"] == -1 for entry in commands)
    assert any(entry["native_methods"] for entry in entries)


def test_generated_docs_are_current():
    for path, content in documents().items():
        assert (ROOT / path).read_text() == content


@pytest.mark.parametrize("feature", FEATURES, ids=lambda feature: feature.key)
def test_only_complete_bindings_can_be_implemented(feature):
    if feature.implementation is Implementation.IMPLEMENTED:
        feature.require_implemented()
    else:
        with pytest.raises(FeatureBlocked):
            feature.require_implemented()


def test_zero_is_known_but_missing_slot_is_not():
    FeatureBinding("future", "future", (("opcode", 0),), ("opcode",)).require_resolved()
    with pytest.raises(FeatureBlocked, match="opcode"):
        FeatureBinding(
            "future", "future", (("opcode", None),), ("opcode",)
        ).require_resolved()


@pytest.mark.parametrize("contract", [CORE_ACCESS, PROVIEW_ACCESS])
def test_unresolved_access_has_no_transport(contract):
    adapter = BlockedAccessAdapter(contract)
    with pytest.raises(FeatureBlocked, match="access adapter"):
        adapter.dispatch(FeatureBinding("future", "future"), {})
    adapter.close()
    assert vars(adapter) == {"contract": contract}


async def test_every_blocked_feature_has_zero_side_effects(hass):
    instance = _coordinator(hass)
    instance.client = FakeClient()
    instance.position_controller._async_cancel_position_target = AsyncMock()
    instance.calibration_controller._async_cancel_calibration = AsyncMock()
    instance.client.send_action = AsyncMock()
    before = instance.observations.revision
    for feature in FEATURES:
        if feature.implementation is not Implementation.IMPLEMENTED:
            with pytest.raises(FeatureBlocked):
                await instance.async_execute_feature(feature.key)
    instance.position_controller._async_cancel_position_target.assert_not_called()
    instance.calibration_controller._async_cancel_calibration.assert_not_called()
    instance.client.send_action.assert_not_called()
    assert instance.tasks.pending == 0
    assert instance.observations.revision == before
    assert instance.client.info_reads == 0
    await instance.async_shutdown()


async def test_feature_dispatch_uses_existing_reviewed_path(hass):
    instance = _coordinator(hass)
    instance.async_send_action = AsyncMock()
    await instance.async_execute_feature("automation.stop")
    instance.async_send_action.assert_awaited_once_with("stop")
    with pytest.raises(FeatureBlocked, match="Unexpected"):
        await instance.async_execute_feature("automation.open", opcode=123)
    instance.async_send_action.assert_awaited_once()
    instance.client = FakeClient()
    await instance.async_shutdown()


async def test_capability_report_distinguishes_offline_gap_and_denial(hass):
    instance = _coordinator(hass)
    instance.capabilities = NiceCapabilities.from_device_info(make_device_info(), 1)
    instance.connection_state = "failed"
    report = instance.capability_report
    assert report["automation.stop"]["implementation"] == "implemented"
    assert report["automation.stop"]["runtime_available"] is False
    assert (
        "Connection is not currently established"
        in report["automation.stop"]["reasons"]
    )
    assert report["installer.fg01h.force_map"]["gaps"] == ("fg01h.force_map_learning",)
    instance.capabilities = replace(instance.capabilities, high_level_actions=False)
    with pytest.raises(FeatureBlocked, match="does not advertise"):
        await instance.async_execute_feature("automation.open")
    instance.client = FakeClient()
    await instance.async_shutdown()


async def test_procedure_preflights_all_recovery_before_runner():
    runner = AsyncMock()
    calls = []

    def require(key):
        calls.append(key)
        if key == "missing_recovery":
            raise FeatureBlocked("missing recovery")
        return FeatureBinding("future", key)

    procedure = ProcedureDefinition(
        "example",
        ("start", "finish"),
        ("missing_recovery",),
        state_machine_verified=True,
    )
    with pytest.raises(FeatureBlocked):
        await procedure.run(require, runner)
    assert calls == ["start", "finish", "missing_recovery"]
    runner.assert_not_called()
    with pytest.raises(FeatureBlocked, match="force-map"):
        await FORCE_MAP_PROCEDURE.run(require, runner)
    runner.assert_not_called()


def test_poll_reduces_each_field_without_overwriting_live_update():
    now = [10.0]
    store = ObservationStore(clock=lambda: now[0])
    old_poll = store.begin_poll(make_status(position=10, state="opening"))
    now[0] = 11
    store.apply_update({"position": 40}, {}, source="live")
    result = store.apply_poll(
        replace(
            make_status(position=20, state="closing", opening_force=33),
            observed_fields=frozenset({"position", "state", "opening_force"}),
        ),
        old_poll,
    )
    assert result.position == 40
    assert result.state == "closing"
    assert result.opening_force == 33
    assert "position" not in result.observed_fields
    assert store.observation("position").monotonic == 11
    assert store.observation("position").source == "live"


def test_cached_and_explicit_unknown_fields_are_distinct():
    store = ObservationStore()
    token = store.begin_poll(make_status(position=40, state="opening"))
    result = store.apply_poll(
        replace(
            make_status(state=None, position=None), observed_fields=frozenset({"state"})
        ),
        token,
    )
    assert result.state is None
    assert result.position == 40
    assert result.observed_fields == {"state"}
    assert store.observation("position").revision == 0


def test_generation_and_shutdown_reject_late_poll_and_event():
    store = ObservationStore()
    token = store.begin_poll(make_status(position=20))
    store.invalidate()
    assert store.apply_poll(make_status(position=80), token).position == 20
    store.invalidate(close=True)
    assert store.apply_update({"position": 99}, {}, source="late_event").position == 20


def test_delayed_capture_cannot_replace_newer_measurement():
    store = ObservationStore(clock=lambda: 20)
    store.apply_update({"position": 40}, {}, source="live")
    token = store.begin_poll()
    status = replace(
        make_status(position=10),
        observed_fields=frozenset({"position"}),
        observation_times={"position": 10},
    )
    assert store.apply_poll(status, token).position == 40


def test_register_removal_and_fresh_dmp_position_clear_old_live_provenance():
    store = ObservationStore()
    store.apply_update({"position": 20}, {"NHK/T4InstantPosition": "20"}, source="live")
    token = store.begin_poll()
    result = store.apply_poll(
        replace(
            make_status(position=40),
            observed_fields=frozenset({"position"}),
            fresh_registers=frozenset(),
        ),
        token,
    )
    assert "NHK/T4InstantPosition" not in result.registers
    assert store.observation("position").source == "dmp_encoder"
    result = store.apply_update({}, {"test": "x"}, source="test")
    result = store.apply_update({}, {}, source="test", removed_registers={"test"})
    assert "test" not in result.registers


def test_nhk_never_uses_another_identified_device():
    xml = '<Response><Devices><Device id="2"><Properties><DoorStatus>opening</DoorStatus><Obstruct>true</Obstruct></Properties></Device></Devices></Response>'
    assert parse_nhk_status_xml(xml, 1) is None
    assert parse_nhk_status_xml(xml, 2).state == "opening"


async def test_cached_position_is_not_a_learning_sample(hass):
    instance = _coordinator(hass)
    status = replace(
        make_status(position=50, state="opening"), observed_fields=frozenset({"state"})
    )
    instance.position_controller._learn_movement_speed(status)
    instance.position_controller._observe_position_speed(status)
    assert instance.position_controller.estimator.observation is None
    assert all(band["samples"] == 0 for band in instance.movement_speed_profile["open"])
    instance.client = FakeClient()
    await instance.async_shutdown()


async def test_write_guard_uses_reported_state_not_display_hint(hass):
    instance = _coordinator(hass)
    instance.async_set_updated_data(make_status(state="stopped"))
    instance.observations.apply_update({"state": "opening"}, {}, source="live")
    with pytest.raises(HomeAssistantError, match="moving"):
        await instance.async_write_dmp_register(4, 0x80, 1)
    instance.client = FakeClient()
    await instance.async_shutdown()


def _wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_stop_then_user_command_then_background_fifo():
    scheduler = SessionScheduler()
    order = []
    scheduler.acquire()
    threads = []
    for name, priority in [
        ("metadata", OperationPriority.METADATA),
        ("open", OperationPriority.CONTROL),
        ("close", OperationPriority.CONTROL),
        ("stop", OperationPriority.STOP),
    ]:

        def work(name=name, priority=priority):
            with scheduler.hold(priority):
                order.append(name)

        thread = threading.Thread(target=work)
        thread.start()
        threads.append(thread)
        _wait_until(lambda: scheduler.diagnostics()["pending"] == len(threads))
    assert scheduler.should_yield()
    scheduler.release()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert order == ["stop", "open", "close", "metadata"]


def test_queued_command_expires_and_can_never_execute_later():
    scheduler = SessionScheduler()
    scheduler.acquire()
    sent = []
    errors = []

    def worker():
        try:
            with scheduler.hold(OperationPriority.CONTROL, timeout=0.03):
                sent.append(True)
        except OperationExpired as err:
            errors.append(err.delivery)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(1)
    scheduler.release()
    assert not thread.is_alive()
    assert not sent
    assert errors == ["not_sent"]
    assert scheduler.diagnostics()["pending"] == 0


def test_shutdown_fences_queue_and_finalizes_after_current_exchange():
    scheduler = SessionScheduler()
    scheduler.acquire()
    calls = []

    def worker():
        with pytest.raises(OperationExpired):
            with scheduler.hold(OperationPriority.CONTROL):
                calls.append("sent")

    thread = threading.Thread(target=worker)
    thread.start()
    _wait_until(lambda: scheduler.diagnostics()["pending"] == 1)
    scheduler.cancel_pending(on_idle=lambda: calls.append("closed"))
    thread.join(1)
    assert not calls
    scheduler.release()
    assert calls == ["closed"]
    with pytest.raises(OperationExpired):
        scheduler.acquire()


async def test_task_owner_cancels_child_refresh_and_rejects_late_work(hass):
    owner = RuntimeTasks(hass)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def child():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    owner.create(child(), "test-refresh")
    await started.wait()
    await owner.cancel(close=True)
    assert cleaned.is_set()
    assert owner.pending == 0
    coroutine = child()
    assert owner.create(coroutine) is None
    assert coroutine.cr_frame is None


def test_router_deadline_is_checked_after_connect_before_write():
    from tests.test_connection_router import _router

    router, health, clients = _router("local_only")
    now = [0.0]
    router._deadline_clock = lambda: now[0]
    before = health.active
    sent = []

    def slow_connect(action, *, before_dispatch):
        now[0] = 16
        before_dispatch()
        sent.append(action)

    clients[0].send_action = slow_connect
    with pytest.raises(OperationExpired) as error:
        router.send_action("open")
    assert error.value.delivery == "not_sent"
    assert not sent
    assert health.active == before
    router.shutdown()


def test_client_guard_runs_before_connect_and_again_before_dispatch(monkeypatch):
    from tests.test_client import _client

    client = _client()
    calls = []
    monkeypatch.setattr(
        client, "_ensure_connected_locked", lambda: calls.append("connect")
    )
    client._run_command_once(
        lambda: calls.append("send"), before_dispatch=lambda: calls.append("guard")
    )
    assert calls == ["guard", "connect", "guard", "send"]

    calls.clear()

    def blocked():
        raise OperationExpired()

    with pytest.raises(OperationExpired):
        client._run_command_once(lambda: calls.append("send"), before_dispatch=blocked)
    assert not calls


def test_router_shutdown_closes_running_session_without_admitting_new_work():
    from tests.test_connection_router import _router

    router, _, clients = _router("local_only")
    started = threading.Event()
    finish = threading.Event()

    def read(**kwargs):
        started.set()
        assert finish.wait(2)
        return make_status()

    clients[0].read_status = read
    thread = threading.Thread(target=router.read_status)
    thread.start()
    assert started.wait(1)
    router.shutdown()
    assert clients[0].close_count == 0
    with pytest.raises(OperationExpired):
        router.send_action("open")
    finish.set()
    thread.join(2)
    assert not thread.is_alive()
    assert clients[0].close_count == 1
    assert not clients[0].commands


async def test_owner_state_is_explicit_and_legacy_facade_still_addresses_it(hass):
    instance = _coordinator(hass)
    instance._position_simulation_anchor_position = 20
    assert instance.position_controller.animation.anchor_position == 20
    assert "_position_simulation_anchor_position" not in vars(instance)
    assert "__getattr__" not in type(instance).__dict__
    instance.last_command = "stop"
    assert instance.command_executor.state.last_command == "stop"
    instance.client = FakeClient()
    await instance.async_shutdown()
    assert instance.tasks.pending == 0


async def test_observation_provenance_wins_over_cached_state_markers(hass):
    instance = _coordinator(hass)
    instance.async_set_updated_data(
        replace(make_status(state="opening"), registers={"NHK/DoorStatus": "opening"})
    )
    instance.observations.apply_update({"state": "opening"}, {}, source="dmp_04_01")
    assert instance.state_source == "dmp_04_01"
    instance.client = FakeClient()
    await instance.async_shutdown()


@pytest.mark.parametrize(
    "permission, expected", [(None, None), ("r", False), ("rwe", True)]
)
async def test_account_permission_is_separate_from_support(hass, permission, expected):
    from custom_components.nice_bidiwifi.models.device import NiceServiceCapability

    instance = _coordinator(hass)
    info = make_device_info()
    caps = NiceCapabilities.from_device_info(info, 1)
    service = NiceServiceCapability(
        "Device", "1", "DoorAction", "", None, permission, None, ()
    )
    instance.capabilities = replace(caps, high_level_actions=True, services=(service,))
    report = instance.features.availability("automation.open")
    assert report.hardware_supported is True
    assert report.permission is expected
    if expected is False:
        instance.async_send_action = AsyncMock()
        with pytest.raises(FeatureBlocked, match="permission"):
            await instance.async_execute_feature("automation.open")
        instance.async_send_action.assert_not_called()
    instance.client = FakeClient()
    await instance.async_shutdown()


def test_closed_empty_store_cannot_be_seeded_by_late_event():
    store = ObservationStore()
    store.invalidate(close=True)
    result = store.apply_update(
        {"position": 80}, {}, source="late", baseline=make_status(position=20)
    )
    assert result.position is None
    assert store.diagnostics() == {}


async def test_read_only_feature_uses_measurements_without_network(hass):
    instance = _coordinator(hass)
    instance.observations.apply_update({"position": 30}, {}, source="live")
    result = await instance.async_execute_feature("automation.status")
    assert result.position == 30
    assert instance.tasks.pending == 0
    instance.client = FakeClient()
    await instance.async_shutdown()
