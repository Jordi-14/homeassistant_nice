"""Owned tasks must not finish before their callers can store the task handle."""

from unittest.mock import Mock

from custom_components.nice_bidiwifi.errors import NicePermissionError
from custom_components.nice_bidiwifi.runtime_tasks import RuntimeTasks
from tests.native_menu_fixtures import install_menu_fixture
from tests.test_controller_menu import instance


async def test_owned_task_runs_after_its_handle_is_stored(hass):
    tasks = RuntimeTasks(hass)
    handle = {"task": None}
    observed = []

    async def worker():
        observed.append(handle["task"])
        handle["task"] = None

    handle["task"] = tasks.create(worker(), "immediate-completion")
    task = handle["task"]
    await task
    assert observed == [task]
    assert handle["task"] is None
    await tasks.cancel(close=True)


async def test_early_menu_permission_failure_does_not_poison_future_renewals(
    hass, monkeypatch
):
    owner = await instance(hass)
    install_menu_fixture(owner)
    guard = owner.access.stationary_guard
    monkeypatch.setattr(
        owner.access,
        "stationary_guard",
        Mock(side_effect=NicePermissionError("No stationary state yet")),
    )
    menu = owner.controller_menu
    menu.schedule()
    first = menu._task
    assert first is not None
    await first
    assert menu._task is None
    monkeypatch.setattr(owner.access, "stationary_guard", guard)
    menu.schedule()
    second = menu._task
    assert second is not None
    await second
    assert menu.valid()
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()


async def test_pro_number_registers_and_accepts_unchanged_value_through_ha_service(
    hass,
    monkeypatch,
):
    from homeassistant.helpers.entity import entity_sources
    from homeassistant.setup import async_setup_component

    from custom_components.nice_bidiwifi.entities.configuration import (
        setup_configuration_controls,
    )
    from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo

    owner = await instance(hass)
    install_menu_fixture(owner, bytes.fromhex("70 5b"))
    info = DmpInfo(1, 0, 0x11, 0, minimum=0, maximum=250, step=1)
    owner.client.dmp_info = {(4, 0x5B): info}
    owner.client.dmp_values[(4, 0x5B)] = b"\x3c"
    owner.parameters.record_info(owner.parameters.address(4, 0x5B), info)
    owner.parameters.observe(owner.parameters.address(4, 0x5B), b"\x3c")
    assert await async_setup_component(hass, "number", {})
    component = hass.data["number"]
    jobs = []
    unload_callbacks = []
    monkeypatch.setattr(owner.config_entry, "async_on_unload", unload_callbacks.append)

    def add_entities(rows):
        jobs.append(hass.async_create_task(component.async_add_entities(rows)))

    setup_configuration_controls(owner, owner.config_entry, "number", add_entities)
    entity_id = "number.parking_gate_time_courtesy_light_parameter_04_5b"
    try:
        assert entity_id not in entity_sources(hass)
        guard = owner.access.stationary_guard
        monkeypatch.setattr(
            owner.access,
            "stationary_guard",
            Mock(side_effect=NicePermissionError("Startup state missing")),
        )
        owner.controller_menu.schedule()
        await owner.controller_menu._task
        assert entity_id not in entity_sources(hass)
        monkeypatch.setattr(owner.access, "stationary_guard", guard)
        owner.controller_menu.schedule()
        await owner.controller_menu._task
        for task in jobs:
            await task
        await hass.async_block_till_done()
        assert entity_id in entity_sources(hass)
        assert hass.states.get(entity_id).state == "60"
        await hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 60}, blocking=True
        )
        assert len(owner.client.dmp_writes) == 1
        result = owner.command_executor.last_result
        assert str(result.acknowledgement) == "accepted"
        assert str(result.verification) == "verified"
        assert hass.states.get(entity_id).state == "60"
    finally:
        if entity_id in entity_sources(hass):
            await component.async_remove_entity(entity_id)
        for remove in unload_callbacks:
            remove()
        await owner.async_shutdown()
