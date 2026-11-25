"""Explicit Pro menu selection without an invented controller-role handshake."""

import threading
import time
from unittest.mock import Mock

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nice_bidiwifi.controller_catalog import PARAMETERS_BY_KEY
from custom_components.nice_bidiwifi.controller_menu import CONF_PRO_CONTROLS
from custom_components.nice_bidiwifi.entities.configuration import (
    configuration_controls,
)
from custom_components.nice_bidiwifi.options_flow import NiceAccessOptionsFlow
from custom_components.nice_bidiwifi.protocol.nhk.access import (
    PairedUser,
    PairingsSnapshot,
    UserRole,
)
from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from custom_components.nice_bidiwifi.scheduler import OperationPriority
from tests.native_menu_fixtures import install_menu_fixture, set_pro_controls
from tests.test_connection_router import _router
from tests.test_controller_menu import instance


async def pro_flow(hass):
    owner = await instance(hass)
    install_menu_fixture(owner)
    set_pro_controls(owner, False)
    flow = NiceAccessOptionsFlow(owner.config_entry)
    flow.hass = hass
    flow._owner = lambda: owner
    return flow, owner


async def test_pro_mode_uses_admin_and_menu_without_fabricating_controller_role(hass):
    flow, owner = await pro_flow(hass)
    result = await flow.async_step_pro_controls({CONF_PRO_CONTROLS: True})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert owner.controller_menu.enabled
    assert owner.controller_menu.report()['menu_profile'] == 'installer'
    assert owner.controller_menu.report()['profile_basis'] == 'explicit_Pro_menu_selection'
    assert not owner.controller_menu.report()['module_admin_grants_installer']
    assert not hasattr(owner.controller_menu, 'role_adapter')
    assert owner.client.value_reads == []
    assert owner.client.dmp_writes == []
    assert owner.data.state == 'closed'
    await owner.async_shutdown()


async def test_pro_mode_rejects_non_admin_before_menu_or_option_change(hass):
    flow, owner = await pro_flow(hass)
    owner.client.read_pairings = lambda: PairingsSnapshot((PairedUser('1','user','source',UserRole.USER),))
    owner.client.read_controller_menu = Mock(side_effect=AssertionError('Menu should not be queried'))
    result = await flow.async_step_pro_controls({CONF_PRO_CONTROLS: True})
    assert result['type'] is FlowResultType.FORM
    assert result['errors']['base'] == 'pro_controls_unavailable'
    assert not owner.controller_menu.enabled
    owner.client.read_controller_menu.assert_not_called()
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()


async def test_new_setting_control_is_enabled_only_after_explicit_pro_mode(hass):
    flow, owner = await pro_flow(hass)
    definition = PARAMETERS_BY_KEY['ctrl_time_electric_lock']
    info = decode_dmp_info(bytes.fromhex('01001111016401000a'))
    address = owner.parameters.address(4, 0x5A)
    owner.parameters.record_info(address, info)
    owner.parameters.observe(address, b'\x14')
    assert not [e for e in configuration_controls(owner, owner.config_entry, 'number') if e.definition.key == definition.key]
    await flow.async_step_pro_controls({CONF_PRO_CONTROLS: True})
    rows = [e for e in configuration_controls(owner, owner.config_entry, 'number') if e.definition.key == definition.key]
    assert len(rows) == 1
    assert rows[0].entity_registry_enabled_default
    assert rows[0].entity_registry_visible_default
    owner.client.read_controller_menu = Mock(side_effect=AssertionError('Disabling must not read device'))
    result = await flow.async_step_pro_controls({CONF_PRO_CONTROLS: False})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert not owner.controller_menu.enabled
    assert not owner.parameters._menu_permissions
    assert not rows[0].available
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()




@pytest.mark.parametrize('method', ['read_pairings', 'read_controller_menu'])
def test_explicit_inspection_runs_before_queued_periodic_status(method):
    router, _, clients = _router('local_only')
    order = []
    setattr(clients[0], method, lambda **kwargs: order.append('inspection'))
    def status():
        with router._lock.hold(OperationPriority.STATUS):
            order.append('status')
    first = threading.Thread(target=status)
    second = threading.Thread(target=lambda: getattr(router, method)())
    def pending(count):
        until = time.monotonic() + 2
        while router._lock.diagnostics()['pending'] < count and time.monotonic() < until:
            time.sleep(0.001)
        assert router._lock.diagnostics()['pending'] >= count
    with router._lock.hold(OperationPriority.STATUS):
        first.start()
        pending(1)
        second.start()
        pending(2)
    first.join(2)
    second.join(2)
    assert order == ['inspection', 'status']
    router.close()
