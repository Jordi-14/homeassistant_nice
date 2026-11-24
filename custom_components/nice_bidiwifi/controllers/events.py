"""Coordinator-owned unsolicited event handling."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
import time

from homeassistant.util import dt as dt_util

from ..errors import NiceProtocolError
from ..models.events import NiceEvent, NiceEventCategory, NiceEventKind
from ..observations import declared_fields
from ..models.status import STATE_CLOSING, STATE_OPENING, NiceStatus
from ..protocol.nhk.events import parse_nhk_event_frame
from ..protocol.nhk.status import status_value
from .base import RuntimeController

_LOGGER = logging.getLogger(__name__)

EVENT_STREAM_IDLE = "idle"
EVENT_STREAM_ACTIVE = "active"
EVENT_STREAM_FALLBACK_POLLING = "fallback_polling"
EVENT_STREAM_STOPPED = "stopped"
EVENT_HISTORY_LIMIT = 32
# LOCAL-DIAGNOSTIC(pr55): abnormal-stop constants, windows and thresholds.
# Built to diagnose one controller fault (ROBUSR10 + BiDi-WiFi 2.6.4, PR #55
# hardware tests). Thresholds and rules come from that one gate only.
# Agents: do not extend, generalize or build on this; keep it opt-in for
# diagnostics, or remove it before a stable release.
ABNORMAL_STOP_EVENT = "nice_bidiwifi_abnormal_stop"
COMMAND_MOVE_WINDOW_SECONDS = 5.0
NHK_MOVE_EXPIRY_SECONDS = 120.0  # longer than the slowest move seen (83 s)
RECENT_COMMAND_SECONDS = 5.0  # same as COMMAND_MOVE_WINDOW_SECONDS
ABNORMAL_SCAN_INTERVAL_SECONDS = 30.0
IMPLAUSIBLE_TRAVEL_POSITION_DELTA = 1000
IMPLAUSIBLE_TRAVEL_SPEED = 2000  # position units per second
ABNORMAL_DEDUPE_SECONDS = 2.0  # NHK and 04/02 often report the same stop
# EvtChangeState values that report a controller fault, not a normal stop
FAULT_CHANGE_STATES = frozenset(
    {
        "force_limiter_intervent",
        "fototest_failed",
        "memory_err",
        "hw_err",
        "double_cmd_err",
        "lockedmotor_err",
    }
)
# Partial and pause stops are normal travel ends.
NORMAL_STOPS = frozenset(
    {
        "stopped_in_opened",
        "stopped_in_closed",
        "stopped_in_pause_time",
        "stopped_in_partial_1",
        "stopped_in_partial_2",
        "stopped_in_partial_3",
    }
)


def _unknown_status() -> NiceStatus:
    return NiceStatus(
        state=None,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
        registers={},
    )


class NiceEventController(RuntimeController):
    """Bridge the protocol reader thread into the Home Assistant event loop."""

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.abnormal_stop_count = 0
        self.last_abnormal_stop = None
        self._abnormal_scan_monotonic = None
        self.event_stream_state = EVENT_STREAM_IDLE
        self.event_stream_error: str | None = None
        self.event_history: deque[NiceEvent] = deque(maxlen=EVENT_HISTORY_LIMIT)
        self.latest_event: NiceEvent | None = None
        self.event_sequence = 0
        self.protocol_event_count = 0
        self.malformed_protocol_event_count = 0
        self.last_event_at: datetime | None = None
        self.last_event_cause: str | None = None
        # LOCAL-DIAGNOSTIC(pr55): move tracking for abnormal-stop checks; see controllers/events.py:_check_abnormal_stop.
        self._t4_command_move: tuple[float, str, int | None] | None = None
        self._nhk_move: tuple[float, str, int | None] | None = None
        self._last_abnormal_fire: tuple[float, str] | None = None
        self.last_t4_cause: str | None = None
        self.last_t4_change_state: str | None = None
        self.last_t4_basic_diagnostic: str | None = None
        self.last_t4_advanced_diagnostic: str | None = None
        self.last_t4_command: str | None = None
        self.basic_diagnostic_code: str | None = None
        self.advanced_diagnostic_code: str | None = None
        self.bluebus_error_status: str | None = None
        self.manoeuvre_average_current: float | None = None
        self.last_reset_cause: str | None = None
        self.last_reset_device_class: str | None = None
        self.event_battery_level: str | None = None
        self.event_battery_device_type: str | None = None
        self._event_client = None
        self._accept_events = False
        self._remove_event_callback: Callable[[], None] | None = None
        self._remove_failure_callback: Callable[[], None] | None = None
        self._obstruction_state: bool | None = None
        self._obstruction_reconnect_count: int | None = None
        self._suppress_initial_obstruction = False

    def _reset_obstruction_observation(self) -> None:
        """Require a fresh observation after the event stream reconnects."""
        self._obstruction_state = None
        self._coordinator.observations.apply_update(
            {"obstacle": None}, {}, source="connection_reset",
            removed_registers={"NHK/Obstruct", "NHK/T4Obstruct"},
        )
        self._suppress_initial_obstruction = True

    def _sync_obstruction_connection(self) -> None:
        """Notice a client reconnect even when its retry succeeded internally."""
        reconnect_count = getattr(self._coordinator.client, "reconnect_count", None)
        if self._obstruction_reconnect_count is None:
            self._obstruction_reconnect_count = reconnect_count
        elif reconnect_count != self._obstruction_reconnect_count:
            self._obstruction_reconnect_count = reconnect_count
            self._reset_obstruction_observation()

    def reconcile_polled_obstacle(self, status: NiceStatus) -> NiceStatus:
        """Keep a live obstacle value when a poll has no current observation."""
        self._sync_obstruction_connection()
        if status.obstacle is not None and "NHK/Obstruct" in (status.fresh_registers if status.fresh_registers is not None else status.registers):
            self._obstruction_state = status.obstacle
            self._suppress_initial_obstruction = False
            return status
        if self._obstruction_state is not None:
            return replace(status, obstacle=self._obstruction_state, observed_fields=declared_fields(status) - {"obstacle"})
        return status

    # LOCAL-DIAGNOSTIC(pr55): speed check for implausible travel.
    @staticmethod
    def _implausible_travel(start: int | None, end: int | None, elapsed: float) -> bool:
        """Return true when the position change is too large and too fast to be real."""
        if start is None or end is None:
            return False
        delta = abs(start - end)
        return (
            delta > IMPLAUSIBLE_TRAVEL_POSITION_DELTA
            and delta / max(elapsed, 0.1) > IMPLAUSIBLE_TRAVEL_SPEED
        )

    # LOCAL-DIAGNOSTIC(pr55): abnormal-stop and implausible-travel detection.
    def _check_abnormal_stop(self, event: NiceEvent) -> NiceEvent:
        """Detect stops that no command explains and travel that cannot be real."""
        change_state = event.t4_change_state
        nhk = status_value(event.raw_state) if event.raw_state else None
        if change_state is None and nhk is None:
            return event
        now = time.monotonic()
        reason = None
        start = self._coordinator.data.current_position if self._coordinator.data else None
        # arm once per move: 04/40 frames repeat DoorStatus every second
        # re-arm when no terminal state closed a move that is long over
        if nhk in {"opening", "closing"} and (
            self._nhk_move is None
            or self._nhk_move[1] != nhk
            or now - self._nhk_move[0] >= NHK_MOVE_EXPIRY_SECONDS
        ):
            self._nhk_move = (now, nhk, start)
        if change_state in {"closing_from_command", "opening_from_command"}:
            direction = "closing" if change_state.startswith("closing") else "opening"
            self._t4_command_move = (now, direction, start)
        elif change_state in {"stopped_in_closed", "stopped_in_opened"}:
            move = self._t4_command_move
            end_name = "closed" if change_state == "stopped_in_closed" else "open"
            if (
                move is not None
                and move[1] == ("closing" if end_name == "closed" else "opening")
                and now - move[0] < COMMAND_MOVE_WINDOW_SECONDS
                and self._coordinator.data is not None
                and self._implausible_travel(
                    move[2], getattr(self._coordinator.data, f"{end_name}_position"), now - move[0]
                )
            ):
                # observational only: the endpoint state is still applied
                event = replace(event, t4_implausible=True)
                reason = "implausible_travel"
        if reason is None and change_state in FAULT_CHANGE_STATES:
            reason = "controller_fault"
        if (
            reason is None
            and change_state is not None
            and change_state.startswith("stopped")
            and change_state not in NORMAL_STOPS
            and (
                # a stop caused by a remote, key, input or HA command is explained;
                # on ROBUSR10 a photocell reversal also reports cause none, so
                # a photocell set to "stop" would fire here
                event.t4_cause in {None, "none", "undefined"}
                or self._open_close_read_as_stop(change_state, event.t4_cause, now)
            )
        ):
            reason = "unexpected_stop"
        if (
            reason is None
            and nhk == "stopped"
            and self._nhk_move is not None
            and now - self._nhk_move[0] < COMMAND_MOVE_WINDOW_SECONDS
            # open/close reverse a move, they do not stop it (2026-11-14 cycle 3:
            # close, then stopped after 2 s, was the fault)
            and self._recent_command(now) not in {"stop", "step_step"}
        ):
            # shortcut: a remote stop in the first 5 s of a move is reported too
            reason = "unexpected_stop"
        if reason is None and nhk in {"open", "closed"} and self._nhk_move is not None:
            started, direction, start = self._nhk_move
            if (
                direction == ("opening" if nhk == "open" else "closing")
                and now - started < COMMAND_MOVE_WINDOW_SECONDS
                and self._coordinator.data is not None
                and self._implausible_travel(
                    start,
                    self._coordinator.data.open_position if nhk == "open" else self._coordinator.data.closed_position,
                    now - started,
                )
            ):
                reason = "implausible_travel"
        if reason is not None:
            self._fire_abnormal_stop(event, reason, now)
        if reason == "implausible_travel":
            self._t4_command_move = self._nhk_move = None
        if change_state is not None and change_state.startswith("stopped"):
            self._t4_command_move = None
        if nhk in {"open", "closed", "stopped"}:
            self._nhk_move = None
        return event

    def _open_close_read_as_stop(
        self, change_state: str, cause: str | None, now: float
    ) -> bool:
        """Return true when the controller stopped on a recent open or close command.

        On hardware an HA close sent to an open, idle gate came back as
        stopped_for_command_arrival with cause oview and no motion (2.5 s later).
        """
        return (
            change_state == "stopped_for_command_arrival"
            and cause == "oview"
            and self._recent_command(now) in {"open", "close"}
            and self._coordinator.data is not None
            and not self._coordinator.data.is_moving
        )

    # LOCAL-DIAGNOSTIC(pr55): recent-command age for abnormal-stop checks.
    def _command_age(self, now: float) -> float | None:
        """Return seconds since a recent command, or None when none is recent."""
        started = self._coordinator.command_executor.state.last_command_monotonic
        if started is None or now - started >= RECENT_COMMAND_SECONDS:
            return None
        return now - started

    def _recent_command(self, now: float) -> str | None:
        """Return the last HA command only while it is recent enough to explain an event."""
        return self._coordinator.command_executor.state.last_command if self._command_age(now) is not None else None

    # LOCAL-DIAGNOSTIC(pr55): abnormal-stop bus event data.
    def _fire_abnormal_stop(self, event: NiceEvent, reason: str, now: float) -> None:
        """Report an abnormal stop that came with a NiceEvent."""
        last = self._last_abnormal_fire
        # one physical stop can come as NHK and 04/02 with different reasons
        if last is not None and now - last[0] < ABNORMAL_DEDUPE_SECONDS:
            return
        self._last_abnormal_fire = (now, reason)
        since_command = (
            now - self._coordinator.command_executor.state.last_command_monotonic
            if self._coordinator.command_executor.state.last_command_monotonic is not None
            else None
        )
        move = self._t4_command_move or self._nhk_move
        self.report_abnormal(
            reason,
            at=event.received_at.isoformat(),
            change_state=event.t4_change_state,
            cause=event.t4_cause,
            position=self._coordinator.data.position if self._coordinator.data else None,
            seconds_since_command=(
                round(since_command, 1) if since_command is not None else None
            ),
            travel_seconds=round(now - move[0], 1) if move is not None else None,
        )

    # LOCAL-DIAGNOSTIC(pr55): abnormal-stop bus event, counter and scan request.
    def report_abnormal(self, reason: str, *, at: str | None = None, **fields) -> None:
        """Fire the abnormal-stop bus event and request a diagnostics scan."""
        data = {"entry_id": self._coordinator.config_entry.entry_id, "reason": reason, **fields}
        self._coordinator.hass.bus.async_fire(ABNORMAL_STOP_EVENT, data)
        self.abnormal_stop_count += 1
        self.last_abnormal_stop = {**data, "at": at or dt_util.utcnow().isoformat()}
        # at most one scan per 30 s
        now = time.monotonic()
        last_scan = self._abnormal_scan_monotonic
        if last_scan is None or now - last_scan >= ABNORMAL_SCAN_INTERVAL_SECONDS:
            self._abnormal_scan_monotonic = now
            self._coordinator._extended_scan_requests += 1
            self._coordinator._extended_status_next_refresh_monotonic = 0.0
            # while a command runs, the fast post-command polls run the scan
            if self._command_age(now) is None:
                self._coordinator.tasks.create(self._coordinator.async_refresh(), "nice-abnormal-stop-scan")

    def ensure_registered(self) -> None:
        """Install persistent callbacks on the current client exactly once."""
        if self._event_client is self._coordinator.client:
            self._sync_obstruction_connection()
            return
        if self._event_client is not None:
            self._reset_obstruction_observation()
        self._remove_callbacks()
        self._event_client = self._coordinator.client
        self._obstruction_reconnect_count = getattr(self._coordinator.client, "reconnect_count", None)
        self._accept_events = True
        self._remove_event_callback = self._coordinator.client.add_event_callback(
            self._receive_raw_frame
        )
        self._remove_failure_callback = self._coordinator.client.add_event_failure_callback(
            self._receive_reader_failure
        )

    def _remove_callbacks(self) -> None:
        for remove in (
            self._remove_event_callback,
            self._remove_failure_callback,
        ):
            if remove is not None:
                remove()
        self._remove_event_callback = None
        self._remove_failure_callback = None
        self._event_client = None

    def _receive_raw_frame(self, frame: bytes) -> None:
        """Transfer a reader-thread frame into the HA loop."""
        try:
            self._coordinator.hass.loop.call_soon_threadsafe(self._handle_raw_frame, frame)
        except RuntimeError:
            _LOGGER.debug("Dropping Nice event while Home Assistant is stopping")

    def _receive_reader_failure(self, error: Exception) -> None:
        """Transfer a reader-thread failure into the HA loop."""
        try:
            self._coordinator.hass.loop.call_soon_threadsafe(
                self._handle_reader_failure,
                error.__class__.__name__,
            )
        except RuntimeError:
            _LOGGER.debug(
                "Ignoring Nice reader failure while Home Assistant is stopping"
            )

    def _handle_raw_frame(self, frame: bytes) -> None:
        """Normalize and apply one unsolicited frame on the HA loop."""
        if not self._accept_events:
            return
        self._sync_obstruction_connection()
        try:
            events = parse_nhk_event_frame(frame, self._coordinator.entry_config.device_id)
        except NiceProtocolError as err:
            self.malformed_protocol_event_count += 1
            _LOGGER.debug(
                "Discarding malformed Nice protocol event: %s",
                err.__class__.__name__,
            )
            if self._coordinator.data is not None:
                self._coordinator.async_set_updated_data(self._coordinator.data)
            return

        self.event_stream_state = EVENT_STREAM_ACTIVE
        self.event_stream_error = None
        for event in events:
            self._apply_event(event)

    def _apply_event(self, event: NiceEvent) -> None:
        """Update normalized state and event diagnostics."""
        event = self._coordinator.position_controller._normalize_live_position_event(event)
        # LOCAL-DIAGNOSTIC(pr55): abnormal-stop detection; see controllers/events.py:_check_abnormal_stop.
        event = self._check_abnormal_stop(event)
        if event.obstruction is not None:
            if (
                event.obstruction is True
                and event.category is not NiceEventCategory.BLUEBUS_ERROR
                and (
                    self._obstruction_state is False
                    or (
                        self._obstruction_state is None
                        and not self._suppress_initial_obstruction
                    )
                )
            ):
                event = replace(event, category=NiceEventCategory.OBSTRUCTION)
            self._obstruction_state = event.obstruction
            self._suppress_initial_obstruction = False
        self.latest_event = event
        self.event_history.append(event)
        self.event_sequence += 1
        self.protocol_event_count += 1
        self.last_event_at = event.received_at
        if event.cause_code is not None:
            self.last_event_cause = event.cause_code
        if event.t4_change_state is not None:
            # a new state change must not keep the old diagnostics
            self.last_t4_change_state = event.t4_change_state
            self.last_t4_basic_diagnostic = event.t4_basic_diagnostic
            self.last_t4_advanced_diagnostic = event.t4_advanced_diagnostic
        if event.t4_cause is not None:
            self.last_t4_cause = event.t4_cause
        if event.t4_command is not None:
            self.last_t4_command = event.t4_command
        if event.basic_diagnostic_code is not None:
            self.basic_diagnostic_code = event.basic_diagnostic_code
        if event.advanced_diagnostic_code is not None:
            self.advanced_diagnostic_code = event.advanced_diagnostic_code
        if event.bluebus_error_status is not None:
            self.bluebus_error_status = event.bluebus_error_status
        if event.manoeuvre_average_current is not None:
            self.manoeuvre_average_current = event.manoeuvre_average_current
        if event.reset_cause is not None:
            self.last_reset_cause = event.reset_cause
            self.last_reset_device_class = event.reset_device_class
        if event.battery_level_code is not None:
            self.event_battery_level = event.battery_level_code
            self.event_battery_device_type = event.battery_device_type

        status = self._coordinator.data or _unknown_status()
        updates: dict[str, object] = {}
        registers = dict(status.registers)
        if event.state is not None:
            updates["state"] = event.state
        elif event.raw_state is not None and event.raw_state.casefold() in {"unknown", "unknow"}:
            updates["state"] = None
        if event.raw_state is not None:
            registers["NHK/DoorStatus"] = event.raw_state
        if event.position is not None:
            updates["position"] = event.position
            registers.pop("NHK/T4CalibratedPosition", None)
            registers.pop("NHK/T4EncoderPosition", None)
            registers.pop("NHK/T4InstantPositionRaw", None)
            registers.pop("NHK/T4InstantPositionScale", None)
            registers["NHK/T4InstantPosition"] = str(round(event.position))
        if event.t4_payload_kind is not None and (
            event.t4_state is not None or event.position is not None
        ):
            registers["NHK/T4PayloadKind"] = event.t4_payload_kind
        if event.t4_state is not None:
            registers["NHK/T4Status"] = event.t4_state
        if event.t4_raw_position is not None:
            registers["NHK/T4InstantPositionRaw"] = str(event.t4_raw_position)
        if event.t4_position_scale is not None:
            registers["NHK/T4InstantPositionScale"] = event.t4_position_scale
        if event.t4_payload_kind == "04/40":
            registers.pop("NHK/T4StatusIgnored", None)
            if event.t4_state not in {None, STATE_OPENING, STATE_CLOSING}:
                registers["NHK/T4StatusIgnored"] = "04/40_position_only"
        if event.obstruction is not None:
            updates["obstacle"] = event.obstruction
            key = "NHK/T4Obstruct" if event.obstruction_source == "t4" else "NHK/Obstruct"
            registers[key] = str(event.obstruction).lower()
        if event.manoeuvre_count is not None:
            updates["maintenance_count"] = event.manoeuvre_count
            updates["total_maneuver_count"] = event.manoeuvre_count
        if event.manoeuvre_threshold is not None:
            updates["maintenance_threshold"] = event.manoeuvre_threshold
        if registers != dict(status.registers):
            updates["registers"] = registers
        if updates:
            changes = {k: v for k,v in registers.items() if status.registers.get(k) != v}
            sources = {}
            if "state" in updates:
                sources["state"] = ("t4_04_02" if event.t4_payload_kind == "04/02" else
                                    "t4_04_40_motion" if event.t4_payload_kind == "04/40" else "nhk_door_status")
            if "position" in updates:
                sources["position"] = "t4_04_40"
            status = self._coordinator.observations.apply_update(
                {k: v for k,v in updates.items() if k != "registers"}, changes,
                source="live_event", sources=sources, baseline=status,
                removed_registers=set(status.registers) - set(registers),
            )
            status = self._coordinator.position_controller._normalize_status_for_display(
                self._coordinator.position_controller._apply_recent_stop_status_hint(status)
            )
            self._coordinator._store_successful_status(status)

        if self._coordinator.capabilities is not None:
            self._coordinator.capabilities = replace(
                self._coordinator.capabilities,
                local_events=True,
                diagnostic_events=(
                    True
                    if event.kind is NiceEventKind.DIAGNOSTIC
                    else self._coordinator.capabilities.diagnostic_events
                ),
            )
        self._coordinator.async_set_updated_data(status)

    def _handle_reader_failure(self, error_name: str) -> None:
        """Expose event loss while leaving adaptive polling operational."""
        if not self._accept_events:
            return
        self.event_stream_state = EVENT_STREAM_FALLBACK_POLLING
        self.event_stream_error = error_name[:64]
        self._reset_obstruction_observation()
        if self._coordinator.data is not None:
            self._coordinator.async_set_updated_data(replace(self._coordinator.data, obstacle=None))
        self._coordinator.tasks.create(self._coordinator.async_request_refresh(), "nice-event-stream-fallback-refresh")

    def mark_reconnecting(self) -> None:
        """Expose that polling is carrying state during reconnection."""
        self.event_stream_state = EVENT_STREAM_FALLBACK_POLLING
        self.event_stream_error = None
        self._reset_obstruction_observation()

    def mark_connected(self) -> None:
        """Expose a healthy reader after a successful polled exchange."""
        if self._coordinator.client.event_stream_active:
            self.event_stream_state = EVENT_STREAM_ACTIVE
            self.event_stream_error = None

    def shutdown(self) -> None:
        """Unsubscribe callbacks before the client reader is stopped."""
        self._accept_events = False
        self._remove_callbacks()
        self.event_stream_state = EVENT_STREAM_STOPPED
        self.event_stream_error = None
        self._reset_obstruction_observation()
