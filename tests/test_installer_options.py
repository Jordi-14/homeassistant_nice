"""Installer UI intent, unavailable workflows and observation-only screens."""

from unittest.mock import AsyncMock

from homeassistant.data_entry_flow import FlowResultType

from custom_components.nice_bidiwifi.entities.lifecycle import (
    AdvertisedDevicesSensor,
    InstallerStateSensor,
)
from custom_components.nice_bidiwifi.options_flow import NiceAccessOptionsFlow
from tests.conftest import config_entry
from tests.test_installer_lifecycle import owner_for


def flow_for(hass):
    owner = owner_for(hass)
    entry = config_entry()
    flow = NiceAccessOptionsFlow(entry)
    flow.hass = hass
    flow._owner = lambda: owner
    return flow, owner


async def test_unavailable_installation_step_has_no_io_even_when_submitted(hass):
    flow, owner = flow_for(hass)
    owner.lifecycle.async_run = AsyncMock()
    result = await flow.async_step_installer({"procedure": "learn_positions"})
    assert result["step_id"] == "installer_action"
    assert result["errors"]["base"] == "installer_binding_unavailable"
    result = await flow.async_step_installer_action({"confirm": True})
    assert result["step_id"] == "installer"
    owner.lifecycle.async_run.assert_not_awaited()


async def test_restart_requires_distinct_confirmation_before_call(hass):
    flow, owner = flow_for(hass)
    owner.lifecycle.async_run = AsyncMock()
    result = await flow.async_step_installer({"procedure": "restart_interface"})
    assert result["step_id"] == "installer_action"
    owner.lifecycle.async_run.assert_not_awaited()
    result = await flow.async_step_installer_action({"confirm": False})
    assert result["errors"]["base"] == "installer_confirmation_required"
    owner.lifecycle.async_run.assert_not_awaited()
    result = await flow.async_step_installer_action({"confirm": True})
    assert result["step_id"] == "installer_result"
    owner.lifecycle.async_run.assert_awaited_once_with(
        "restart_interface", confirmed=True
    )


async def test_read_only_inventory_screen_and_optional_entities(hass):
    flow, owner = flow_for(hass)
    await flow.async_step_installer({"procedure": "inspect_devices"})
    await flow.async_step_installer_action({"confirm": True})
    state = InstallerStateSensor(owner, config_entry())
    devices = AdvertisedDevicesSensor(owner, config_entry())
    assert state.native_value == "completed"
    assert devices.native_value == 2
    assert (
        not state.entity_registry_enabled_default
        and not devices.entity_registry_enabled_default
    )
    assert not owner.client.interface_reboots
    result = await flow.async_step_installer_result({})
    assert result["type"] is FlowResultType.MENU


async def test_cancellation_screen_does_not_send_guessed_abort(hass):
    flow, owner = flow_for(hass)
    result = await flow.async_step_installer_cancel()
    assert result["step_id"] == "installer_cancel"
    result = await flow.async_step_installer_cancel({})
    assert result["type"] is FlowResultType.MENU
    assert owner.client.interface_reboots == 0
