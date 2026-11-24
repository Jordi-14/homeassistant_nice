"""Regressions from the b5 installation health check."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.nice_bidiwifi as nice_init
from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.nice_bidiwifi.parameters import ParameterRepository
from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.settings import AUTO_CLOSE
from custom_components.nice_bidiwifi.text import TEXTS, NiceBidiText
from tests.conftest import FakeCoordinator, config_entry, config_entry_data
from tests.test_coordinator import _coordinator
from tests.test_init import FakeCoordinator as SetupCoordinator


async def test_cancelled_first_refresh_closes_resources_before_another_setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data())
    entry.add_to_hass(hass)
    SetupCoordinator.instances = []
    with (
        patch.object(nice_init, "NiceBidiDataUpdateCoordinator", SetupCoordinator),
        patch.object(
            SetupCoordinator,
            "async_config_entry_first_refresh",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await nice_init.async_setup_entry(hass, entry)
    assert SetupCoordinator.instances[0].shutdown
    assert getattr(entry, "runtime_data", None) is None


async def test_startup_core_read_does_not_wait_for_broad_diagnostics(hass):
    instance = _coordinator(hass)
    instance.startup_refresh = True
    assert instance._should_read_extended_status() is False
    instance.startup_refresh = False
    assert instance._should_read_extended_status() is True
    from tests.conftest import FakeClient

    instance.client = FakeClient()
    await instance.async_shutdown()


async def test_diagnostics_work_when_setup_has_not_created_runtime_data(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data())
    entry.add_to_hass(hass)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["setup"]["runtime_available"] is False
    assert entry.data["password"] not in str(result)
    assert result["entry"]["password"] == "**REDACTED**"


def test_unnamed_interface_has_unknown_text_state_instead_of_invalid_empty_string():
    coordinator = FakeCoordinator()
    coordinator.device_info = replace(coordinator.device_info, interface_name=None)
    entity = NiceBidiText(coordinator, config_entry(), TEXTS[0])
    assert entity.native_value is None
    assert entity.state is None


def test_captured_boolean_info_keeps_on_off_settings_readable_and_writable():
    repository = ParameterRepository(1)
    address = repository.address(4, 0x80)
    info = decode_dmp_info(bytes.fromhex("01000141020001"))
    repository.record_info(address, info)
    assert repository.observe(address, b"\x00").value is False
    assert repository.block_reason(AUTO_CLOSE) is None
    assert repository.prepare_write(AUTO_CLOSE, 1).payload == b"\x01"
