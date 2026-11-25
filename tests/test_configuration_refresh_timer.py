"""Watched settings load independently of the gate's idle status interval."""

from unittest.mock import Mock

from homeassistant.util import dt as dt_util

from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo
from tests.native_menu_fixtures import install_menu_fixture
from tests.test_controller_menu import instance


async def test_watched_value_batches_continue_without_a_gate_poll(hass):
    owner = await instance(hass)
    install_menu_fixture(owner, bytes.fromhex("70 5b 70 5a 70 5c 70 8d"))
    info = DmpInfo(1, 0, 0x11, 0, minimum=0, maximum=250, step=1)
    fields = [
        ("ctrl_time_courtesy_light", 0x5B, 60),
        ("ctrl_time_electric_lock", 0x5A, 2),
        ("ctrl_time_suction_cup", 0x5C, 2),
        ("ctrl_standby_activation_time", 0x8D, 60),
    ]
    for _key, parameter, value in fields:
        owner.parameters.record_info(owner.parameters.address(4, parameter), info)
        owner.client.dmp_values[(4, parameter)] = bytes([value])
    await owner.controller_menu.async_refresh()
    removers = [owner.configuration.watch(key) for key, _, _ in fields]
    await owner.configuration._task
    assert (
        sum(owner.configuration.view(key).raw_value is not None for key, _, _ in fields)
        == 3
    )
    assert owner.configuration._remove_tick is not None
    owner.async_request_refresh = Mock(
        side_effect=AssertionError("No gate poll needed")
    )
    owner.configuration._tick(dt_util.utcnow())
    await owner.configuration._task
    assert [owner.configuration.view(key).raw_value for key, _, _ in fields] == [
        60,
        2,
        2,
        60,
    ]
    assert not owner.client.dmp_writes
    read_count = len(owner.client.value_reads)
    owner.configuration._tick(dt_util.utcnow())
    assert owner.configuration._task is None
    assert len(owner.client.value_reads) == read_count
    for remove in removers:
        remove()
    assert owner.configuration._remove_tick is None
    await owner.async_shutdown()


async def test_configuration_timer_stops_at_shutdown_and_yields_to_installer_work(hass):
    owner = await instance(hass)
    # A watched safe field starts the timer even before metadata is available.
    remove = owner.configuration.watch("ctrl_time_courtesy_light")
    await owner.configuration._task
    assert owner.configuration._remove_tick is not None
    owner.lifecycle.block_reason = Mock(return_value="Installer transaction active")
    owner.configuration._last_attempt.clear()
    reads = len(owner.client.dmp_info_reads)
    owner.configuration._tick(dt_util.utcnow())
    assert owner.configuration._task is None
    assert len(owner.client.dmp_info_reads) == reads
    await owner.async_shutdown()
    assert owner.configuration._remove_tick is None
    remove()


async def test_value_timer_waits_for_movement_to_finish(hass):
    from tests.conftest import make_status

    owner = await instance(hass)
    info = DmpInfo(1, 0, 0x11, 0, minimum=0, maximum=250, step=1)
    owner.parameters.record_info(owner.parameters.address(4, 0x5B), info)
    owner.client.dmp_values[(4, 0x5B)] = b"\x3c"
    owner.async_set_updated_data(make_status(state="opening"))
    remove = owner.configuration.watch("ctrl_time_courtesy_light")
    owner.configuration._tick(dt_util.utcnow())
    assert owner.configuration._task is None and not owner.client.value_reads
    owner.async_set_updated_data(make_status(state="closed"))
    owner.configuration._tick(dt_util.utcnow())
    await owner.configuration._task
    assert owner.configuration.view("ctrl_time_courtesy_light").raw_value == 60
    assert not owner.client.dmp_writes
    remove()
    await owner.async_shutdown()


async def test_disabled_polling_allows_initial_demand_read_but_no_periodic_reads(hass):
    import time

    owner = await instance(hass)
    hass.config_entries.async_update_entry(
        owner.config_entry, pref_disable_polling=True
    )
    info = DmpInfo(1, 0, 0x11, 0, minimum=0, maximum=250, step=1)
    owner.parameters.record_info(owner.parameters.address(4, 0x5B), info)
    owner.client.dmp_values[(4, 0x5B)] = b"\x3c"
    key = "ctrl_time_courtesy_light"
    remove = owner.configuration.watch(key)
    await owner.configuration._task
    assert owner.configuration.view(key).raw_value == 60
    count = len(owner.client.value_reads)
    owner.configuration._last_attempt[key] = time.monotonic() - 400
    owner.configuration._tick(dt_util.utcnow())
    assert owner.configuration._task is None and len(owner.client.value_reads) == count
    remove()
    await owner.async_shutdown()
