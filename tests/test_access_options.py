"""Explicit module-access forms and mutation consent boundaries."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nice_bidiwifi.entities.access import ModuleRoleSensor
from custom_components.nice_bidiwifi.options_flow import NiceAccessOptionsFlow
from custom_components.nice_bidiwifi.protocol.nhk.access import (
    PairedUser,
    PairingsSnapshot,
    UserRole,
)
from tests.conftest import FakeClient, config_entry, make_status
from tests.test_coordinator import _coordinator


async def flow_for(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.data = make_status(state="closed")
    entry = config_entry()
    entry.options = {"retained": True}
    flow = NiceAccessOptionsFlow(entry)
    flow.hass = hass
    flow._owner = lambda: owner
    return flow, owner


async def test_options_inspection_and_private_sensor(hass):
    flow, owner = await flow_for(hass)
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.MENU
    result = await flow.async_step_inspect_users()
    assert result["description_placeholders"]["role"] == "admin"
    assert result["description_placeholders"]["admins"] == "1"
    sensor = ModuleRoleSensor(owner, config_entry())
    assert sensor.native_value == "admin"
    assert not sensor.entity_registry_enabled_default
    assert sensor.unique_id.endswith("_module_access_role")
    assert "source" not in str(sensor.extra_state_attributes)
    owner.client.reconnect_count += 1
    assert sensor.native_value == "unverified"


async def test_approval_requires_second_explicit_confirmation(hass):
    flow, owner = await flow_for(hass)
    actor = PairedUser("1", "user", "source", UserRole.ADMIN)
    target = PairedUser("2", "pending", "phone", UserRole.WAIT, "Visitor", pending=True)
    owner.client.read_pairings = lambda: PairingsSnapshot((actor, target))
    owner.access.async_change = AsyncMock()
    result = await flow.async_step_approve_user()
    assert result["step_id"] == "approve_user"
    result = await flow.async_step_approve_user({"target": "0"})
    assert result["step_id"] == "confirm_access"
    owner.access.async_change.assert_not_awaited()
    result = await flow.async_step_confirm_access({"confirm": False})
    assert result["errors"]["base"] == "access_confirmation_required"
    owner.access.async_change.assert_not_awaited()
    result = await flow.async_step_confirm_access({"confirm": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == dict(flow._entry.options)
    owner.access.async_change.assert_awaited_once_with(
        target, "approve", UserRole.USER, confirmed=True
    )
    assert not flow._targets and flow._target is None


async def test_unknown_actor_cannot_get_user_edit_screen(hass):
    flow, owner = await flow_for(hass)
    owner.client.read_pairings = lambda: PairingsSnapshot(())
    result = await flow.async_step_edit_user()
    assert result["errors"]["base"] == "access_inspection_failed"


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        ("network_setup", "network_workflow_unverified"),
        ("ownership", "ownership_workflow_unverified"),
    ],
)
async def test_unverified_workflows_abort_before_any_network(hass, step, reason):
    flow, owner = await flow_for(hass)
    owner.access.async_refresh = AsyncMock()
    result = await getattr(flow, "async_step_" + step)()
    assert result["reason"] == reason
    owner.access.async_refresh.assert_not_awaited()
