"""Tests for CU_WIFI closes that are reported only by a live endpoint position."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nice_bidiwifi import position as position_module
from custom_components.nice_bidiwifi.const import (
    DOMAIN,
    IDLE_UPDATE_INTERVAL,
    MOVING_UPDATE_INTERVAL,
)
from custom_components.nice_bidiwifi.coordinator import NiceBidiDataUpdateCoordinator
from custom_components.nice_bidiwifi.models.events import (
    NiceEvent,
    NiceEventCategory,
    NiceEventKind,
)
from tests.conftest import FakeClient, config_entry_data, make_device_info, make_status

SETTLE = position_module.CLOSE_ENDPOINT_SETTLE_SECONDS


def _coordinator(
    hass: HomeAssistant,
    *,
    interface_product: str = "CU_WIFI",
) -> NiceBidiDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="entry-1")
    entry.add_to_hass(hass)
    instance = NiceBidiDataUpdateCoordinator(hass, entry)
    instance.client = FakeClient()
    instance._store_device_info(
        replace(make_device_info(nhk_status=True), interface_product=interface_product)
    )
    return instance


def _settled(instance: NiceBidiDataUpdateCoordinator) -> None:
    # The display animation runs on real timers and is not under test here.
    instance._clear_position_simulation(notify=False)


def _change(
    instance: NiceBidiDataUpdateCoordinator,
    state: str,
    *,
    obstruction: bool = False,
) -> None:
    """Apply a CHANGE event as the reader thread would deliver it."""
    instance.event_controller._apply_event(
        NiceEvent(
            kind=NiceEventKind.CHANGE,
            category=NiceEventCategory.STATE_CHANGE,
            received_at=dt_util.utcnow(),
            state=state,
            raw_state=state,
            obstruction=obstruction,
        )
    )
    _settled(instance)


def _live_position(
    instance: NiceBidiDataUpdateCoordinator,
    position: float,
    *,
    scale: str = "percent",
) -> None:
    """Apply a CU_WIFI 04/40 live position frame."""
    instance.event_controller._apply_event(
        NiceEvent(
            kind=NiceEventKind.LIVE_STATUS,
            category=NiceEventCategory.STATE_CHANGE,
            received_at=dt_util.utcnow(),
            position=position,
            t4_payload_kind="04/40",
            t4_state="stopped",
            t4_raw_position=round(position),
            t4_position_scale=scale,
        )
    )
    _settled(instance)


def _poll(instance: NiceBidiDataUpdateCoordinator, state: str) -> None:
    """Apply an NHK STATUS poll result through the coordinator pipeline."""
    status = replace(
        make_status(state=state, position=None, current_position=None),
        registers={"NHK/DoorStatus": state},
        obstacle=False,
    )
    status = instance._normalize_status_for_display(
        instance._apply_recent_stop_status_hint(status)
    )
    instance._store_successful_status(status)
    instance.async_set_updated_data(status)
    _settled(instance)


async def _wait(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def _observed_close(instance: NiceBidiDataUpdateCoordinator) -> None:
    """The captured RBS600HS close: closing, about 50%, then 1%."""
    _change(instance, "closing")
    _live_position(instance, 53.0)
    _live_position(instance, 1.0)


async def test_live_endpoint_position_settles_to_closed(hass: HomeAssistant) -> None:
    instance = _coordinator(hass)
    _observed_close(instance)
    assert instance.data.state == "closing"

    await _wait(hass, SETTLE - 1)
    assert instance.data.state == "closing"

    await _wait(hass, SETTLE + 1)
    assert instance.data.state == "closed"
    assert instance.update_interval == IDLE_UPDATE_INTERVAL

    _poll(instance, "closing")
    assert instance.data.state == "closed"
    assert instance.data.position == 0.0
    assert instance.data.registers["NHK/InferredClosedEndpoint"] == "live_endpoint_position"


async def test_new_movement_clears_the_inferred_close(hass: HomeAssistant) -> None:
    instance = _coordinator(hass)
    _observed_close(instance)
    await _wait(hass, SETTLE + 1)
    assert instance.data.state == "closed"

    _change(instance, "opening")
    assert instance.data.state == "opening"
    _poll(instance, "closing")
    assert instance.data.state == "closing"
    assert instance.update_interval == MOVING_UPDATE_INTERVAL


async def test_command_clears_the_inferred_close(hass: HomeAssistant) -> None:
    instance = _coordinator(hass)
    _observed_close(instance)
    await _wait(hass, SETTLE + 1)
    assert instance.data.state == "closed"

    await instance.async_send_action("close")
    await instance._async_cancel_post_command_refresh()
    _poll(instance, "closing")

    assert instance.data.state == "closing"


async def test_reversal_before_settling_cancels_the_inference(
    hass: HomeAssistant,
) -> None:
    instance = _coordinator(hass)
    _observed_close(instance)
    _change(instance, "opening", obstruction=True)
    await _wait(hass, SETTLE + 1)

    assert instance.data.state == "opening"


async def test_gate_stopped_midway_is_never_inferred_closed(hass: HomeAssistant) -> None:
    """Without an endpoint frame, a quiet closing state stays unconfirmed."""
    instance = _coordinator(hass)
    _change(instance, "closing")
    _live_position(instance, 53.0)
    for _ in range(3):
        await _wait(hass, 600)
        _poll(instance, "closing")

    assert instance.data.state == "closing"


@pytest.mark.parametrize("obstruction_after_endpoint", [False, True])
async def test_obstruction_blocks_the_inference(
    hass: HomeAssistant,
    obstruction_after_endpoint: bool,
) -> None:
    instance = _coordinator(hass)
    _change(instance, "closing")
    if not obstruction_after_endpoint:
        _change(instance, "closing", obstruction=True)
    _live_position(instance, 1.0)
    if obstruction_after_endpoint:
        _change(instance, "closing", obstruction=True)
    await _wait(hass, SETTLE + 1)

    assert instance.data.state == "closing"
    assert instance.data.obstacle is True


async def test_stale_closing_after_restart_stays_unconfirmed(hass: HomeAssistant) -> None:
    """A restart misses the endpoint frame; only stale polls arrive."""
    instance = _coordinator(hass)
    for _ in range(3):
        _poll(instance, "closing")
        await _wait(hass, 600)

    assert instance.data.state == "closing"


@pytest.mark.parametrize("interface_product", ["BiDi-WiFi", "IT4WIFI"])
async def test_other_interface_families_are_never_inferred(
    hass: HomeAssistant,
    interface_product: str,
) -> None:
    instance = _coordinator(hass, interface_product=interface_product)
    _observed_close(instance)
    await _wait(hass, SETTLE + 1)

    assert instance.data.state == "closing"


async def test_raw_scale_live_position_is_not_an_endpoint(hass: HomeAssistant) -> None:
    instance = _coordinator(hass)
    _change(instance, "closing")
    _live_position(instance, 1.0, scale="raw")
    await _wait(hass, SETTLE + 1)

    assert instance.data.state == "closing"


async def test_inference_is_logged_at_info_once(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    instance = _coordinator(hass)
    caplog.set_level(logging.DEBUG, logger=position_module.__name__)
    for _ in range(2):
        _change(instance, "opening")
        _observed_close(instance)
        await _wait(hass, SETTLE + 1)
        assert instance.data.state == "closed"

    messages = [r for r in caplog.records if r.name == position_module.__name__]
    assert [r.levelno for r in messages if "did not report the end" in r.getMessage()] == [
        logging.INFO
    ]
    assert sum("close inferred" in r.getMessage() for r in messages) == 2


class _FakeScheduler:
    """Deterministic stand-in for async_call_later with its own clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.pending: list[list] = []

    def call_later(self, _hass, delay, action):
        entry = [self.now + delay, action, True]
        self.pending.append(entry)

        def cancel() -> None:
            entry[2] = False

        return cancel

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for entry in [e for e in self.pending if e[2] and e[0] <= self.now]:
            entry[2] = False
            entry[1](None)


async def test_newer_live_position_above_endpoint_cancels_pending_close(
    hass: HomeAssistant,
) -> None:
    """1% starts the settle timer; a later 12% frame while closing cancels it."""
    instance = _coordinator(hass)
    _change(instance, "closing")
    _live_position(instance, 1.0)
    _live_position(instance, 12.0)
    await _wait(hass, SETTLE + 1)

    assert instance.data.state == "closing"
    assert instance.data.position == 12.0


async def test_new_endpoint_frame_restarts_the_quiet_period(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _FakeScheduler()
    monkeypatch.setattr(position_module, "async_call_later", scheduler.call_later)
    instance = _coordinator(hass)
    _change(instance, "closing")
    _live_position(instance, 1.0)

    scheduler.advance(SETTLE - 2)
    _live_position(instance, 0.0)
    scheduler.advance(SETTLE - 2)
    assert instance.data.state == "closing"

    scheduler.advance(3)
    assert instance.data.state == "closed"


async def test_settling_rechecks_the_latest_live_position(hass: HomeAssistant) -> None:
    instance = _coordinator(hass)
    _change(instance, "closing")
    _live_position(instance, 1.0)
    _live_position(instance, 12.0)

    instance._latch_close_endpoint(None)

    assert instance.data.state == "closing"


async def test_live_movement_after_inferred_close_shows_closing(
    hass: HomeAssistant,
) -> None:
    instance = _coordinator(hass)
    _observed_close(instance)
    await _wait(hass, SETTLE + 1)
    assert instance.data.state == "closed"

    _live_position(instance, 12.0)

    assert instance.data.state == "closing"
    assert instance.data.position == 12.0
    assert "NHK/InferredClosedEndpoint" not in instance.data.registers


async def test_reported_closed_after_a_command_stays_closed(hass: HomeAssistant) -> None:
    """Only an inherited inference is cleared; an explicit closed report wins."""
    instance = _coordinator(hass)
    _observed_close(instance)
    await _wait(hass, SETTLE + 1)
    instance._reset_close_endpoint()

    _change(instance, "closed")

    assert instance.data.state == "closed"
    assert "NHK/InferredClosedEndpoint" not in instance.data.registers
