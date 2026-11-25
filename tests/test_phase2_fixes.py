"""Cold diagnostics must not block HA; stale snapshots must not look current."""

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.nice_bidiwifi import diagnostic_runtime
from custom_components.nice_bidiwifi.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.nice_bidiwifi.parity import parity_summary, validation_matrix
from tests.conftest import FakeClient, FakeCoordinator, config_entry, make_status
from tests.test_coordinator import _coordinator


async def test_cold_diagnostics_reads_both_files_off_event_loop(hass, monkeypatch):
    entry = config_entry()
    entry.runtime_data = FakeCoordinator()
    parity_summary.cache_clear()
    validation_matrix.cache_clear()
    loop_thread = threading.get_ident()
    reads = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.name in {"parity_summary.json", "validation_matrix.json"}:
            reads.append((path.name, threading.get_ident()))
            assert threading.get_ident() != loop_thread
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["parity_closure"]["all_sources_dispositioned"]
    assert result["validation_matrix"]["stable_release_authorized"] is False
    assert {name for name, _ in reads} == {
        "parity_summary.json",
        "validation_matrix.json",
    }
    await async_get_config_entry_diagnostics(hass, entry)
    assert len(reads) == 2


async def test_feature_parity_snapshot_also_loads_in_worker(hass, monkeypatch):
    parity_summary.cache_clear()
    loop_thread = threading.get_ident()
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "parity_summary.json":
            assert threading.get_ident() != loop_thread
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    owner = _coordinator(hass)
    result = await owner.features.execute("parity.report")
    assert result["all_sources_dispositioned"]
    await owner.async_shutdown()


async def test_old_opening_snapshot_is_stale_when_cover_is_closed(hass, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(
        diagnostic_runtime, "time", SimpleNamespace(monotonic=lambda: now[0])
    )
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state="closed", position=0))
    owner.diagnostic_runtime._samples["automation_state"] = {
        "state": "available",
        "values": {"status_code": 2},
        "observed_monotonic": 100.0,
        "source": "selective_get",
    }
    view = owner.diagnostic_runtime.view("automation_state")
    assert view["state"] == "stale"
    assert view["reason"] == "snapshot_differs_from_current_gate_state"
    assert view["values"]["status_code"] == 2  # preserve original evidence
    assert view["current_gate_state"] == "closed"
    assert owner.data.position == 0
    owner.diagnostic_runtime._samples["automation_state"]["values"]["status_code"] = 5
    assert owner.diagnostic_runtime.view("automation_state")["state"] == "available"
    now[0] = 111
    assert owner.diagnostic_runtime.view("automation_state")["state"] == "stale"
    assert (
        owner.diagnostic_runtime.view("automation_state")["reason"]
        == "snapshot_expired"
    )
    await owner.async_shutdown()


async def test_enabled_automation_refreshes_after_five_seconds_without_waiting_for_metadata(
    hass, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr(
        diagnostic_runtime, "time", SimpleNamespace(monotonic=lambda: now[0])
    )
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state="closed"))
    runtime = owner.diagnostic_runtime
    runtime._watched = {"io": 1, "hardware": 1, "automation_state": 1}
    runtime._attempts = {"io": -300, "hardware": -300, "automation_state": 94.0}
    runtime.async_read = AsyncMock()
    await runtime._refresh()
    assert runtime.async_read.await_args_list[0].args == ("automation_state",)
    assert len(runtime.async_read.await_args_list) == 2  # bounded batch retained
    assert runtime.extra_registers() == ()
    owner.async_set_updated_data(make_status(state="opening"))
    assert (4, 1) in runtime.extra_registers()
    await owner.async_shutdown()


async def test_fresh_poll_replaces_snapshot_instead_of_claiming_old_frame_is_current(
    hass,
):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state="closed", position=0))
    old = replace(
        make_status(state="opening"),
        registers={"04/01": "02 ff 00 00"},
        fresh_registers=frozenset({"04/01"}),
    )
    owner.diagnostic_runtime.record_poll(old)
    assert owner.diagnostic_runtime.view("automation_state")["state"] == "stale"
    new = replace(
        make_status(state="closed"),
        registers={"04/01": "05 ff 00 00"},
        fresh_registers=frozenset({"04/01"}),
    )
    owner.diagnostic_runtime.record_poll(new)
    view = owner.diagnostic_runtime.view("automation_state")
    assert view["state"] == "available"
    assert view["source"] == "poll"
    assert view["values"]["status_code"] == 5
    await owner.async_shutdown()


async def test_fast_diagnostic_timer_is_opt_in_and_cancelled_on_shutdown(hass, monkeypatch):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state='closed'))
    hooks = []
    cancel = Mock()
    def track(hass, action, interval):
        hooks.append((action, interval.total_seconds()))
        return cancel
    monkeypatch.setattr(diagnostic_runtime, 'async_track_time_interval', track)
    owner.diagnostic_runtime.schedule = Mock()
    remove = owner.diagnostic_runtime.watch('automation_state')
    assert hooks[0][1] == 5
    hooks[0][0](None)
    assert owner.diagnostic_runtime.schedule.call_count == 2
    assert owner.client.value_reads == []
    await owner.async_shutdown()
    cancel.assert_called_once()
    remove()
    cancel.assert_called_once()


async def test_unsupported_get_is_not_probed_every_five_seconds(hass):
    from custom_components.nice_bidiwifi.parameters import SupportState
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state='closed'))
    address = owner.parameters.address(4, 1)
    owner.parameters.record_support(address, 'get', SupportState.UNSUPPORTED)
    owner.client.read_dmp_value = Mock(side_effect=AssertionError('Unsupported GET retried'))
    for _ in range(2):
        await owner.diagnostic_runtime.async_read('automation_state')
    assert owner.diagnostic_runtime.view('automation_state')['state'] == 'unsupported'
    owner.client.read_dmp_value.assert_not_called()
    owner.diagnostic_runtime._samples['automation_state']['last_read_error'] = 'TransportError'
    assert owner.diagnostic_runtime._refresh_interval('automation_state') == 30
    await owner.async_shutdown()


async def test_generic_stopped_state_is_not_a_false_contradiction(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state='partially_open'))
    owner.client.dmp_values[(4, 1)] = bytes.fromhex('01 ff 00 00')
    await owner.diagnostic_runtime.async_read('automation_state')
    assert owner.diagnostic_runtime.view('automation_state')['state'] == 'available'
    await owner.async_shutdown()


async def test_poll_snapshot_is_published_on_loop_after_generation_fence(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.async_set_updated_data(make_status(state='closed'))
    loop_thread = threading.get_ident()
    calls = []
    record = owner.diagnostic_runtime.record_poll
    def record_on_loop(status):
        assert threading.get_ident() == loop_thread
        calls.append(status)
        record(status)
    owner.diagnostic_runtime.record_poll = record_on_loop
    status = replace(make_status(state='closed'), registers={'04/01':'05 ff 00 00'}, fresh_registers=frozenset({'04/01'}))
    owner._read_status_and_maybe_info = lambda: status
    await owner._async_update_data()
    assert len(calls) == 1
    assert owner.diagnostic_runtime.view('automation_state')['values']['status_code'] == 5
    owner.diagnostic_runtime.invalidate()
    def old_session():
        owner.observations.invalidate()
        return status
    owner._read_status_and_maybe_info = old_session
    await owner._async_update_data()
    assert len(calls) == 1
    assert owner.diagnostic_runtime.view('automation_state')['state'] == 'unavailable'
    await owner.async_shutdown()


def test_extra_diagnostic_register_does_not_duplicate_primary_status_read(monkeypatch):
    from tests.test_client import _client
    client = _client()
    read = Mock()
    monkeypatch.setattr(client, '_read_dmp_register_locked', read)
    client._read_status_locked(extra_registers=((4, 1), (4, 0xD2), (4, 0xD2)))
    addresses = [tuple(call.args[1:5]) for call in read.call_args_list]
    assert addresses.count((0, 3, 4, 1)) == 1
    assert addresses.count((0, 3, 4, 0xD2)) == 1
