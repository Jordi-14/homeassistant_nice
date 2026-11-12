"""Coordinator-owned unsolicited event handling."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
import time

from ..errors import NiceProtocolError
from ..models.events import NiceEvent, NiceEventCategory, NiceEventKind
from ..models.status import STATE_CLOSING, STATE_OPENING, NiceStatus
from ..protocol.nhk.events import parse_nhk_event_frame
from ..protocol.nhk.status import status_value
from .base import OwnerBoundController

_LOGGER = logging.getLogger(__name__)

EVENT_STREAM_IDLE = "idle"
EVENT_STREAM_ACTIVE = "active"
EVENT_STREAM_FALLBACK_POLLING = "fallback_polling"
EVENT_STREAM_STOPPED = "stopped"
EVENT_HISTORY_LIMIT = 32
ABNORMAL_STOP_EVENT = "nice_bidiwifi_abnormal_stop"
COMMAND_MOVE_WINDOW_SECONDS = 5.0
RECENT_COMMAND_SECONDS = 3.0
ABNORMAL_SCAN_INTERVAL_SECONDS = 30.0
IMPLAUSIBLE_TRAVEL_POSITION_DELTA = 1000
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


class NiceEventController(OwnerBoundController):
    """Bridge the protocol reader thread into the Home Assistant event loop."""

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.event_stream_state = EVENT_STREAM_IDLE
        self.event_stream_error: str | None = None
        self.event_history: deque[NiceEvent] = deque(maxlen=EVENT_HISTORY_LIMIT)
        self.latest_event: NiceEvent | None = None
        self.event_sequence = 0
        self.protocol_event_count = 0
        self.malformed_protocol_event_count = 0
        self.last_event_at: datetime | None = None
        self.last_event_cause: str | None = None
        self._t4_command_move: tuple[float, str, int | None] | None = None
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
        self._suppress_initial_obstruction = True

    def _sync_obstruction_connection(self) -> None:
        """Notice a client reconnect even when its retry succeeded internally."""
        reconnect_count = getattr(self.client, "reconnect_count", None)
        if self._obstruction_reconnect_count is None:
            self._obstruction_reconnect_count = reconnect_count
        elif reconnect_count != self._obstruction_reconnect_count:
            self._obstruction_reconnect_count = reconnect_count
            self._reset_obstruction_observation()

    def reconcile_polled_obstacle(self, status: NiceStatus) -> NiceStatus:
        """Keep a live obstacle value when a poll has no current observation."""
        self._sync_obstruction_connection()
        if status.obstacle is not None and "NHK/Obstruct" in status.registers:
            self._obstruction_state = status.obstacle
            self._suppress_initial_obstruction = False
            return status
        if self._obstruction_state is not None:
            return replace(status, obstacle=self._obstruction_state)
        return status

    def _check_abnormal_stop(self, event: NiceEvent) -> NiceEvent:
        """Detect stops that no command explains and travel that cannot be real."""
        change_state = event.t4_change_state
        if change_state is None:
            return event
        now = time.monotonic()
        reason = None
        if change_state in {"closing_from_command", "opening_from_command"}:
            direction = "closing" if change_state.startswith("closing") else "opening"
            start = self.data.current_position if self.data else None
            self._t4_command_move = (now, direction, start)
        elif change_state in {"stopped_in_closed", "stopped_in_opened"}:
            move = self._t4_command_move
            if move is not None and now - move[0] < COMMAND_MOVE_WINDOW_SECONDS:
                end = None
                if self.data is not None:
                    end = (
                        self.data.closed_position
                        if change_state == "stopped_in_closed"
                        else self.data.open_position
                    )
                if (
                    move[2] is not None
                    and end is not None
                    and abs(move[2] - end) > IMPLAUSIBLE_TRAVEL_POSITION_DELTA
                ):
                    # the 04/02 state is not applied; the NHK XML state still is
                    event = replace(
                        event,
                        t4_implausible=True,
                        state=status_value(event.raw_state) if event.raw_state else None,
                    )
                    reason = "implausible_travel"
        if (
            reason is None
            and change_state.startswith("stopped_")
            and change_state not in NORMAL_STOPS
            and self._command_age(now) is None
            # a stop caused by a remote, key or input is explained
            and event.t4_cause in {None, "none", "undefined"}
        ):
            reason = "unexpected_stop"
        if change_state.startswith("stopped"):
            self._t4_command_move = None
        if reason is not None:
            self._fire_abnormal_stop(event, reason, now)
        return event

    def _command_age(self, now: float) -> float | None:
        """Return seconds since a recent command, or None when none is recent."""
        started = self.last_command_monotonic
        if started is None or now - started >= RECENT_COMMAND_SECONDS:
            return None
        return now - started

    def _fire_abnormal_stop(self, event: NiceEvent, reason: str, now: float) -> None:
        """Fire the abnormal-stop bus event and request a diagnostics scan."""
        since_command = (
            now - self.last_command_monotonic
            if self.last_command_monotonic is not None
            else None
        )
        data = {
            "entry_id": self.config_entry.entry_id,
            "reason": reason,
            "change_state": event.t4_change_state,
            "cause": event.t4_cause,
            "position": self.data.position if self.data else None,
            "seconds_since_command": (
                round(since_command, 1) if since_command is not None else None
            ),
        }
        self.hass.bus.async_fire(ABNORMAL_STOP_EVENT, data)
        self.abnormal_stop_count += 1
        self.last_abnormal_stop = {**data, "at": event.received_at.isoformat()}
        # no scan while a command runs, and at most one per 30 s
        if self._command_age(now) is not None:
            return
        last_scan = self._abnormal_scan_monotonic
        if last_scan is None or now - last_scan >= ABNORMAL_SCAN_INTERVAL_SECONDS:
            self._abnormal_scan_monotonic = now
            self._extended_status_next_refresh_monotonic = 0.0
            self.config_entry.async_create_background_task(
                self.hass, self.async_refresh(), "nice-abnormal-stop-scan"
            )

    def ensure_registered(self) -> None:
        """Install persistent callbacks on the current client exactly once."""
        if self._event_client is self.client:
            self._sync_obstruction_connection()
            return
        if self._event_client is not None:
            self._reset_obstruction_observation()
        self._remove_callbacks()
        self._event_client = self.client
        self._obstruction_reconnect_count = getattr(self.client, "reconnect_count", None)
        self._accept_events = True
        self._remove_event_callback = self.client.add_event_callback(
            self._receive_raw_frame
        )
        self._remove_failure_callback = self.client.add_event_failure_callback(
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
            self.hass.loop.call_soon_threadsafe(self._handle_raw_frame, frame)
        except RuntimeError:
            _LOGGER.debug("Dropping Nice event while Home Assistant is stopping")

    def _receive_reader_failure(self, error: Exception) -> None:
        """Transfer a reader-thread failure into the HA loop."""
        try:
            self.hass.loop.call_soon_threadsafe(
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
            events = parse_nhk_event_frame(frame, self.entry_config.device_id)
        except NiceProtocolError as err:
            self.malformed_protocol_event_count += 1
            _LOGGER.debug(
                "Discarding malformed Nice protocol event: %s",
                err.__class__.__name__,
            )
            if self.data is not None:
                self.async_set_updated_data(self.data)
            return

        self.event_stream_state = EVENT_STREAM_ACTIVE
        self.event_stream_error = None
        for event in events:
            self._apply_event(event)

    def _apply_event(self, event: NiceEvent) -> None:
        """Update normalized state and event diagnostics."""
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

        status = self.data or _unknown_status()
        updates: dict[str, object] = {}
        registers = dict(status.registers)
        if event.state is not None:
            updates["state"] = event.state
        if event.raw_state is not None:
            registers["NHK/DoorStatus"] = event.raw_state
        if event.position is not None:
            updates["position"] = event.position
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
            status = replace(status, **updates)
            status = self._normalize_status_for_display(
                self._apply_recent_stop_status_hint(status)
            )
            self._store_successful_status(status)

        if self.capabilities is not None:
            self.capabilities = replace(
                self.capabilities,
                local_events=True,
                diagnostic_events=(
                    True
                    if event.kind is NiceEventKind.DIAGNOSTIC
                    else self.capabilities.diagnostic_events
                ),
            )
        self.async_set_updated_data(status)

    def _handle_reader_failure(self, error_name: str) -> None:
        """Expose event loss while leaving adaptive polling operational."""
        if not self._accept_events:
            return
        self.event_stream_state = EVENT_STREAM_FALLBACK_POLLING
        self.event_stream_error = error_name[:64]
        self._reset_obstruction_observation()
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, obstacle=None))
        self.hass.async_create_task(
            self.async_request_refresh(),
            "nice-event-stream-fallback-refresh",
        )

    def mark_reconnecting(self) -> None:
        """Expose that polling is carrying state during reconnection."""
        self.event_stream_state = EVENT_STREAM_FALLBACK_POLLING
        self.event_stream_error = None
        self._reset_obstruction_observation()

    def mark_connected(self) -> None:
        """Expose a healthy reader after a successful polled exchange."""
        if self.client.event_stream_active:
            self.event_stream_state = EVENT_STREAM_ACTIVE
            self.event_stream_error = None

    def shutdown(self) -> None:
        """Unsubscribe callbacks before the client reader is stopped."""
        self._accept_events = False
        self._remove_callbacks()
        self.event_stream_state = EVENT_STREAM_STOPPED
        self.event_stream_error = None
        self._reset_obstruction_observation()
