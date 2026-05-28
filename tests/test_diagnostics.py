"""Diagnostics tests for Nice BiDi-WiFi."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.nice_bidiwifi.const import CONF_SOURCE_ID, CONF_TARGET_MAC
from custom_components.nice_bidiwifi.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import FakeCoordinator, config_entry


async def test_diagnostics_redacts_sensitive_data(hass) -> None:
    """Test diagnostics redacts credentials and local identifiers."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"][CONF_HOST] == "**REDACTED**"
    assert diagnostics["entry"][CONF_USERNAME] == "**REDACTED**"
    assert diagnostics["entry"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["entry"][CONF_TARGET_MAC] == "**REDACTED**"
    assert diagnostics["entry"][CONF_SOURCE_ID] == "**REDACTED**"
    assert diagnostics["device_info"]["interface_serial"] == "**REDACTED**"
    assert diagnostics["device_info"]["device_serial"] == "**REDACTED**"
    assert diagnostics["status"] == {
        "state": "opening",
        "position": 42.4,
        "is_moving": True,
    }
