"""Commands must cross the actual coordinator/router boundary with guards intact."""

from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from tests.conftest import config_entry_data, make_status
from tests.test_connection_router import _router
from tests.test_coordinator import _coordinator


@pytest.mark.parametrize("mode", ["local_only", "cloud_only", "local_with_cloud_fallback"])
@pytest.mark.parametrize("method,action", [
    ("send_action", "open"), ("send_action", "close"), ("send_action", "stop"),
    ("send_dep_action", "partial_open_1"), ("send_t4_action", "partial_open_1"),
])
@pytest.mark.parametrize("revoked", [False, True])
async def test_coordinator_router_dispatch_guard(hass, monkeypatch, mode, method, action, revoked):
    owner = _coordinator(hass, data=config_entry_data(connection_mode=mode, relay_host="relay.example", relay_port=7890))
    router, _, clients = _router(mode)
    owner.client = router
    owner.async_set_updated_data(make_status())
    monkeypatch.setattr(owner, "t4_action_supported", lambda _: True)
    monkeypatch.setattr(type(owner), "t4_actions_via_nhk", property(lambda _: method == "send_t4_action"))
    calls = []
    for client in clients:
        def send(value, *, before_dispatch=None, client=client):
            assert before_dispatch is not None
            before_dispatch()
            calls.append((client.route, method, value))
        monkeypatch.setattr(client, method, send, raising=False)
    guard = Mock(wraps=owner._control_dispatch_guard)
    monkeypatch.setattr(owner, "_control_dispatch_guard", guard)
    acquire = router._acquire_for_command
    def acquire_then_revoke(**kwargs):
        acquire(**kwargs)
        if revoked:
            owner.access.control_guard = Mock(side_effect=HomeAssistantError("Permission revoked while queued"))
    monkeypatch.setattr(router, "_acquire_for_command", acquire_then_revoke)
    try:
        if method == "send_action":
            operation = owner._async_send_action(action, refresh=False, simulate=False)
        else:
            operation = owner._async_send_dep_action(action, refresh=False)
            monkeypatch.setattr(owner.position_controller, "_start_position_simulation", lambda *a, **k: None)
        if revoked:
            with pytest.raises(HomeAssistantError, match="Permission revoked"):
                await operation
            assert calls == []
            assert owner.command_executor.last_result.acknowledgement == "rejected"
        else:
            await operation
            assert calls == [("cloud" if mode == "cloud_only" else "local", method, action)]
        guard.assert_called_once_with(action)
    finally:
        await owner.async_shutdown()
