"""Motion trace: with debug logging on, the moving status poll also reads 04/D2 and 04/D1."""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.controllers import motion_trace
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from custom_components.nice_bidiwifi.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from tests.conftest import FakeClient, config_entry_data, make_status
from tests.test_client import _client

D2_INFO = decode_dmp_info(bytes.fromhex("0281e2410e80008000800080648000806400008000000000008064000080000000"))
D1_INFO = decode_dmp_info(bytes.fromhex("0181e1410e0f0743000dffffffff3c1fff0100"))
# ROBUSR10 while opening: work 238 s left, torque_m1 45 %, speed_m1 97 %; force limiter level 2
D2_OPENING = "00 ee 00 00 00 00 00 0b 00 21 00 2d 00 00 00 19 00 00 00 00 00 61 00 00"
D1_OPENING = "01 00 40 00 08 04 00 00 00 00 00 02 01 00"
PACKAGE_LOGGER = "custom_components.nice_bidiwifi"


def _coordinator(hass) -> NiceBidiDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="trace-1")
    entry.add_to_hass(hass)
    coordinator = NiceBidiDataUpdateCoordinator(hass, entry)
    coordinator._extended_status_next_refresh_monotonic = time.monotonic() + 1000  # no extended scan now
    return coordinator


def test_extra_registers_are_read_after_the_core_status_and_are_optional(monkeypatch) -> None:
    client = _client()
    calls = []
    monkeypatch.setattr(
        client,
        "_read_dmp_register_locked",
        lambda registers, daddr, dendpoint, group, parameter, *, required: calls.append(
            (group, parameter, required)
        ),
    )

    client._read_status_locked(extra_registers=motion_trace.TRACE_REGISTERS)

    assert calls[-2:] == [(0x04, 0xD2, False), (0x04, 0xD1, False)]
    assert all(required for *_, required in calls[:-2])


async def test_no_trace_registers_without_debug_logging(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.client = client = FakeClient()
    client.read_status_result = make_status(state="opening")
    coordinator.data = client.read_status_result
    logging.getLogger(PACKAGE_LOGGER).setLevel(logging.INFO)

    coordinator.motion_trace.handle_command("open")
    coordinator._read_status_and_maybe_info()

    assert client.read_status_extra_registers == [()]
    assert not coordinator.motion_trace.traces


async def test_moving_poll_records_load_and_inputs(hass, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=PACKAGE_LOGGER)
    coordinator = _coordinator(hass)
    coordinator.register_info.update({"04/D2": D2_INFO, "04/D1": D1_INFO})
    coordinator.client = client = FakeClient()
    moving = make_status(state="opening", current_position=2080)
    client.read_status_result = replace(moving, registers={"04/D2": D2_OPENING, "04/D1": D1_OPENING})
    coordinator.data = moving  # a remote or wall-button move starts the trace too

    coordinator._read_status_and_maybe_info()
    coordinator.data = replace(moving, state="stopped")
    client.read_status_result = replace(client.read_status_result, state="stopped", current_position=2163)
    coordinator._read_status_and_maybe_info()  # within AFTER_STOP_SECONDS: still sampled

    assert client.read_status_extra_registers == [motion_trace.TRACE_REGISTERS] * 2
    (trace,) = coordinator.motion_trace.traces
    first, last = trace["samples"]
    assert trace["trigger"] == "movement"
    assert first["encoder"] == 2080 and first["state"] == "opening"
    assert first["torque_m1"] == 45 and first["speed_m1"] == 97 and first["work_time_left"] == 238
    assert first["force_limiter_state"] == "level_2"
    assert last["state"] == "stopped" and last["encoder"] == 2163
    assert "Nice motion trace" in caplog.text
    coordinator.config_entry.runtime_data = coordinator
    diagnostics = await async_get_config_entry_diagnostics(hass, coordinator.config_entry)
    assert diagnostics["motion_traces"][-1]["samples"][0]["torque_m1"] == 45


async def test_command_trigger_and_idle_gate(hass, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=PACKAGE_LOGGER)
    coordinator = _coordinator(hass)
    trace = coordinator.motion_trace
    coordinator.data = make_status(state="closed")

    assert trace.extra_registers() == ()  # idle gate, no command: normal poll only
    trace.handle_command("open")
    assert trace.extra_registers() == motion_trace.TRACE_REGISTERS
    trace.record(make_status(state="opening"))
    assert trace.traces[-1]["trigger"] == "command:open"
