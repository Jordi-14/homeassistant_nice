"""Diagnostics tests for Nice."""

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
    assert diagnostics["status"]["state"] == "opening"
    assert diagnostics["status"]["position"] == 42.4
    assert diagnostics["status"]["current_position"] == 424
    assert diagnostics["status"]["state_source"] == "dmp_04_01"
    assert diagnostics["status"]["position_source"] == "dmp_encoder"
    assert diagnostics["status"]["position_confidence"] == "measured"
    assert diagnostics["status"]["position_reporting_observed"] is True
    assert diagnostics["status"]["is_moving"] is True
    assert diagnostics["status"]["bus_t4"]["opening_speed"] == 60
    assert diagnostics["status"]["bus_t4"]["maintenance_count"] == 12
    assert diagnostics["status"]["bus_t4"]["limit_open"] is True
    assert diagnostics["status"]["bus_t4"]["obstacle"] is True
    assert diagnostics["status"]["bus_t4"]["oxi_product"] == "OXI"
    assert diagnostics["calibration"]["cancel_reason"] is None
    assert "dmp_registers" not in diagnostics["status"]
