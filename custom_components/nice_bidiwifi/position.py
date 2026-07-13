"""Position tracking and set-position support for Nice."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
import logging
import time

from homeassistant.exceptions import HomeAssistantError

from .calibration_constants import CALIBRATION_STATE_RUNNING
from .client import (
    NiceBidiAuthError,
    NiceBidiConnectionError,
    NiceBidiStatus,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_STOPPED,
)
from .connection import CONNECTION_STATE_AUTH_FAILED, CONNECTION_STATE_FAILED
from .const import DOMAIN, ERROR_UPDATE_INTERVAL, IDLE_UPDATE_INTERVAL, MOVING_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

POSITION_TARGET_POLL_SECONDS = 0.5
POSITION_TARGET_TOLERANCE = 1.0
POST_COMMAND_REFRESH_DELAY_SECONDS = 2.0
POST_COMMAND_FAST_POLL_SECONDS = 60.0
RECENT_STOP_STATUS_OVERRIDE_SECONDS = 20.0
POSITION_SIMULATION_TICK_SECONDS = 1.0
POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND = 1.0
POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR = 0.8
POSITION_SIMULATION_START_GRACE_SECONDS = 8.0
POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS = 30.0
POSITION_TARGET_LIVE_POSITION_TOLERANCE = 0.5
POSITION_TARGET_LIVE_SPEED_MARGIN = 3.0


class NiceBidiPositionMixin:
    """Position display, simulation, and target-position behavior."""

    def _init_position_state(self) -> None:
        """Initialize position-related runtime state."""
        self._recent_stop_command_monotonic: float | None = None
        self._recent_stop_started_from_motion = False
        self._post_command_fast_poll_until_monotonic: float | None = None
        self._last_known_position: float | None = None
        self._position_target_task: asyncio.Task[None] | None = None
        self._post_command_refresh_task: asyncio.Task[None] | None = None
        self._position_simulation_task: asyncio.Task[None] | None = None
        self._position_simulation_action: str | None = None
        self._position_simulation_anchor_position: float | None = None
        self._position_simulation_anchor_monotonic: float | None = None
        self._position_simulation_started_monotonic: float | None = None
        self._position_simulation_deadline_monotonic: float | None = None
        self._position_simulation_confirmed_moving = False
        self._position_simulation_target_position: float | None = None
        self._position_simulation_speed_percent_per_second: float | None = None

    async def _async_cancel_position_target(self) -> None:
        """Cancel a pending target-position watcher."""
        task = self._position_target_task
        if task is None:
            return
        self._position_target_task = None
        if task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_cancel_post_command_refresh(self) -> None:
        """Cancel a pending delayed post-command refresh."""
        task = self._post_command_refresh_task
        if task is None:
            return
        self._post_command_refresh_task = None
        if task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_cancel_position_simulation(self) -> None:
        """Cancel the optimistic position animation."""
        task = self._position_simulation_task
        self._clear_position_simulation(notify=False)
        if task is None or task.done() or task is asyncio.current_task():
            return
        with suppress(asyncio.CancelledError):
            await task

    def _extend_post_command_fast_poll_window(self) -> None:
        """Poll quickly for a short window after any command."""
        self._post_command_fast_poll_until_monotonic = time.monotonic() + POST_COMMAND_FAST_POLL_SECONDS

    def _post_command_fast_poll_active(self) -> bool:
        """Return true while the post-command fast polling window is active."""
        until = self._post_command_fast_poll_until_monotonic
        if until is None:
            return False
        if time.monotonic() <= until:
            return True
        self._post_command_fast_poll_until_monotonic = None
        return False

    def _update_interval_for_status(self, status: NiceBidiStatus) -> timedelta:
        """Return the coordinator interval for the latest status."""
        post_command_fast_poll_active = self._post_command_fast_poll_active()
        if status.is_moving or post_command_fast_poll_active:
            return MOVING_UPDATE_INTERVAL
        return IDLE_UPDATE_INTERVAL

    def _schedule_post_command_refresh(self) -> None:
        """Schedule a delayed refresh so motor state is not missed after commands."""
        task = self._post_command_refresh_task
        if task is not None and not task.done():
            task.cancel()
        self._post_command_refresh_task = self.hass.async_create_task(
            self._async_post_command_refresh(),
            name=f"{DOMAIN} post-command refresh",
        )

    async def _async_post_command_refresh(self) -> None:
        """Refresh after the gate controller has had time to enter its new state."""
        task = asyncio.current_task()
        try:
            await asyncio.sleep(POST_COMMAND_REFRESH_DELAY_SECONDS)
            await self.async_request_refresh()
        except asyncio.CancelledError:
            raise
        finally:
            if self._post_command_refresh_task is task:
                self._post_command_refresh_task = None

    def _apply_recent_stop_status_hint(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Mask short-lived stale CU_WIFI states after a local stop command."""
        stopped_at = self._recent_stop_command_monotonic
        if stopped_at is None:
            return status
        if time.monotonic() - stopped_at > RECENT_STOP_STATUS_OVERRIDE_SECONDS:
            self._recent_stop_command_monotonic = None
            self._recent_stop_started_from_motion = False
            return status
        if not self._recent_stop_started_from_motion or status.state == STATE_STOPPED:
            return status
        if status.state not in {STATE_OPEN, STATE_CLOSED, STATE_OPENING, STATE_CLOSING}:
            return status

        registers = dict(status.registers)
        registers["NHK/RecentStopOverride"] = status.state
        return replace(status, state=STATE_STOPPED, position=self._last_known_position, registers=registers)

    def _normalize_status_for_display(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Align sparse status updates with the cover behavior users expect."""
        if (
            status.state == STATE_STOPPED
            and status.position is None
            and self._last_known_position is not None
        ):
            registers = dict(status.registers)
            registers["NHK/LastKnownPositionFallback"] = str(round(self._last_known_position, 1))
            return replace(status, position=self._last_known_position, registers=registers)
        return status

    @property
    def display_position(self) -> float | None:
        """Return the position HA should display, using simulation while active."""
        simulated = self._current_simulated_position()
        if simulated is not None:
            return round(simulated, 1)
        status = self.data
        if status is None:
            return None
        return status.position if status.position is not None else self._last_known_position

    @property
    def display_position_estimated(self) -> bool:
        """Return true when the displayed position is currently estimated."""
        if self._current_simulated_position() is not None:
            return True
        status = self.data
        return bool(status is not None and status.position is None and self._last_known_position is not None)

    @property
    def position_simulation_action(self) -> str | None:
        """Return the active simulated direction."""
        return self._position_simulation_action if self.display_position_estimated else None

    @property
    def position_simulation_speed_percent_per_second(self) -> float | None:
        """Return the active simulated display speed."""
        if not self.display_position_estimated:
            return None
        speed = self._position_simulation_speed_percent_per_second
        return round(speed, 2) if speed is not None else None

    def _start_position_simulation(self, action: str, *, target_position: float | None = None) -> None:
        """Start or restart optimistic position animation after a movement command."""
        anchor = self._current_simulated_position()
        if anchor is None:
            anchor = self.display_position
        if anchor is None:
            return

        now = time.monotonic()
        speed = self._position_simulation_speed(action)
        target = None if target_position is None else max(0.0, min(100.0, float(target_position)))
        limit = 100.0 if action == "open" and target is None else 0.0 if target is None else target
        distance = abs(limit - anchor)
        expected_seconds = distance / speed if speed > 0 else 0
        deadline = now + max(
            POSITION_SIMULATION_START_GRACE_SECONDS,
            expected_seconds + POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
        )

        self._position_simulation_action = action
        self._position_simulation_anchor_position = anchor
        self._position_simulation_anchor_monotonic = now
        self._position_simulation_started_monotonic = now
        self._position_simulation_deadline_monotonic = deadline
        self._position_simulation_confirmed_moving = False
        self._position_simulation_target_position = target
        self._position_simulation_speed_percent_per_second = speed
        self._ensure_position_simulation_task()
        self.async_update_listeners()

    def _ensure_position_simulation_task(self) -> None:
        """Ensure the simulation tick task is running."""
        task = self._position_simulation_task
        if task is not None and not task.done():
            return
        self._position_simulation_task = self.hass.async_create_task(
            self._async_run_position_simulation(),
            name=f"{DOMAIN} position simulation",
        )

    async def _async_run_position_simulation(self) -> None:
        """Tick the optimistic display position until real status stops it."""
        task = asyncio.current_task()
        try:
            while self._position_simulation_action is not None:
                await asyncio.sleep(POSITION_SIMULATION_TICK_SECONDS)
                deadline = self._position_simulation_deadline_monotonic
                if deadline is not None and time.monotonic() > deadline:
                    self._clear_position_simulation()
                    return
                self.async_update_listeners()
        except asyncio.CancelledError:
            raise
        finally:
            if self._position_simulation_task is task:
                self._position_simulation_task = None

    def _current_simulated_position(self) -> float | None:
        """Return the current synthetic display position."""
        action = self._position_simulation_action
        anchor = self._position_simulation_anchor_position
        anchor_time = self._position_simulation_anchor_monotonic
        speed = self._position_simulation_speed_percent_per_second
        if action is None or anchor is None or anchor_time is None or speed is None:
            return None

        elapsed = max(0.0, time.monotonic() - anchor_time)
        if action == "open":
            calculated = min(100.0, anchor + (speed * elapsed))
            target = self._position_simulation_target_position
            if target is None:
                cap = 100.0 if anchor >= 100.0 else max(99.0, anchor)
            else:
                cap = target if anchor < target else anchor
            return max(0.0, min(calculated, cap))

        calculated = max(0.0, anchor - (speed * elapsed))
        target = self._position_simulation_target_position
        if target is None:
            floor = 0.0 if anchor <= 0.0 else min(1.0, anchor)
        else:
            floor = target if anchor > target else anchor
        return min(100.0, max(calculated, floor))

    def _sync_position_simulation_from_status(self, status: NiceBidiStatus) -> None:
        """Rebase or stop simulated display movement from a real status update."""
        if self.calibration_state == CALIBRATION_STATE_RUNNING:
            self._clear_position_simulation(notify=False)
            return
        if status.position is None:
            return

        real_action = self._motion_action_from_state(status.state)
        if real_action is not None:
            self._rebase_position_simulation(
                real_action,
                status.position,
                confirmed_moving=True,
                keep_target=real_action == self._position_simulation_action,
            )
            return

        if self._position_simulation_action is None:
            return
        if not self._position_simulation_confirmed_moving:
            started = self._position_simulation_started_monotonic
            if (
                started is not None
                and time.monotonic() - started < POSITION_SIMULATION_START_GRACE_SECONDS
            ):
                return
        self._clear_position_simulation(notify=False)

    def _rebase_position_simulation(
        self,
        action: str,
        position: float,
        *,
        confirmed_moving: bool,
        keep_target: bool,
    ) -> None:
        """Anchor simulation to a fresh real position update."""
        now = time.monotonic()
        speed = self._position_simulation_speed(action)
        target = self._position_simulation_target_position if keep_target else None
        limit = 100.0 if action == "open" and target is None else 0.0 if target is None else target
        distance = abs(limit - position)
        expected_seconds = distance / speed if speed > 0 else 0
        self._position_simulation_action = action
        self._position_simulation_anchor_position = max(0.0, min(100.0, position))
        self._position_simulation_anchor_monotonic = now
        if self._position_simulation_started_monotonic is None:
            self._position_simulation_started_monotonic = now
        self._position_simulation_deadline_monotonic = now + max(
            POSITION_SIMULATION_START_GRACE_SECONDS,
            expected_seconds + POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
        )
        self._position_simulation_confirmed_moving = confirmed_moving
        self._position_simulation_target_position = target
        self._position_simulation_speed_percent_per_second = speed
        self._ensure_position_simulation_task()

    def _clear_position_simulation(self, *, notify: bool = True) -> None:
        """Clear optimistic position animation state."""
        task = self._position_simulation_task
        self._position_simulation_task = None
        self._position_simulation_action = None
        self._position_simulation_anchor_position = None
        self._position_simulation_anchor_monotonic = None
        self._position_simulation_started_monotonic = None
        self._position_simulation_deadline_monotonic = None
        self._position_simulation_confirmed_moving = False
        self._position_simulation_target_position = None
        self._position_simulation_speed_percent_per_second = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        if notify:
            self.async_update_listeners()

    @staticmethod
    def _motion_action_from_state(state: str) -> str | None:
        """Return the movement action represented by a BiDi state."""
        if state == STATE_OPENING:
            return "open"
        if state == STATE_CLOSING:
            return "close"
        return None

    @staticmethod
    def _position_reached(action: str, position: float, target: float) -> bool:
        """Return true when a percentage has crossed the requested stop point."""
        if action == "open":
            return position >= target
        return position <= target

    @staticmethod
    def _live_position_can_adjust_stop(
        action: str,
        *,
        start_position: float | None,
        position: float,
        elapsed_seconds: float,
        planned_delay_seconds: float,
        stop_position: float,
    ) -> bool:
        """Return true when a live position update is useful for target timing."""
        if start_position is None:
            return False
        tolerance = POSITION_TARGET_LIVE_POSITION_TOLERANCE
        if action == "open":
            if position <= start_position + tolerance:
                return False
        elif position >= start_position - tolerance:
            return False

        if elapsed_seconds <= 0 or planned_delay_seconds <= 0:
            return True

        planned_distance = abs(stop_position - start_position)
        if planned_distance <= 0:
            return True
        planned_speed = planned_distance / planned_delay_seconds
        observed_speed = abs(position - start_position) / elapsed_seconds
        max_reasonable_speed = max(
            planned_speed * POSITION_TARGET_LIVE_SPEED_MARGIN,
            planned_speed + 10.0,
        )
        return observed_speed <= max_reasonable_speed

    def _remaining_stop_delay_seconds(
        self,
        action: str,
        position: float,
        stop_position: float,
    ) -> float | None:
        """Return remaining calibrated time from a live position update."""
        speed = self._calibrated_travel_speed_percent_per_second(action)
        if speed is None or speed <= 0:
            return None
        if action == "open":
            remaining = stop_position - position
        else:
            remaining = position - stop_position
        return max(0.0, remaining) / speed

    async def async_set_position(self, target_position: int) -> None:
        """Move toward a target percentage and stop after the target is reached."""
        await self._async_cancel_calibration()
        await self._async_cancel_post_command_refresh()
        target = max(0, min(100, target_position))
        status = self.data
        current = self.display_position
        if status is None or current is None:
            await self.async_request_refresh()
            status = self.data
            current = self.display_position
        if status is None or current is None:
            raise HomeAssistantError("Nice position is not available")

        if target <= 0:
            await self.async_send_action("close")
            return
        if target >= 100:
            await self.async_send_action("open")
            return
        if abs(current - target) <= POSITION_TARGET_TOLERANCE:
            if status.is_moving:
                await self.async_send_action("stop")
            return

        action = "open" if target > current else "close"
        calibrated_stop_percent = self._calibrated_stop_percent(float(target), action)
        stop_position = float(target) if calibrated_stop_percent is None else calibrated_stop_percent
        stop_raw = None
        if calibrated_stop_percent is not None and self._has_encoder_calibration_data(status):
            stop_raw = self._raw_for_percent(status, stop_position)
        stop_delay_seconds = self._calibrated_stop_delay_seconds(current, stop_position, action)
        await self._async_cancel_position_target()
        await self._async_send_action(action, refresh=False, simulation_target_position=target)
        self._position_target_task = self.hass.async_create_task(
            self._async_stop_at_position(
                target,
                action,
                stop_raw,
                stop_delay_seconds=stop_delay_seconds,
                start_position=current,
                stop_position=stop_position,
            ),
            name=f"{DOMAIN} stop at {target}%",
        )

    async def _async_stop_at_position(
        self,
        target: int,
        action: str,
        stop_raw: int | None = None,
        *,
        stop_delay_seconds: float | None = None,
        start_position: float | None = None,
        stop_position: float | None = None,
    ) -> None:
        """Poll live position and stop after crossing the target."""
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        terminal_states = {STATE_OPEN, STATE_CLOSED, STATE_STOPPED}
        task = asyncio.current_task()
        started_moving = False
        movement_started_monotonic: float | None = None
        timing_started_monotonic = time.monotonic()
        movement_start_deadline = time.monotonic() + 8.0
        stop_deadline_monotonic: float | None = None
        live_adjustment_position = start_position
        target_stop_position = float(target) if stop_position is None else float(stop_position)
        try:
            while True:
                await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
                now = time.monotonic()
                status = await self._async_read_motion_status()

                if status.state == moving_state:
                    started_moving = True
                    if movement_started_monotonic is None:
                        movement_started_monotonic = now
                        if stop_delay_seconds is not None:
                            stop_deadline_monotonic = now + stop_delay_seconds
                elif (
                    stop_delay_seconds is not None
                    and stop_deadline_monotonic is None
                    and now > movement_start_deadline
                ):
                    stop_deadline_monotonic = timing_started_monotonic + stop_delay_seconds

                position = status.position
                if position is not None:
                    if stop_raw is not None and status.current_position is not None:
                        if self._raw_reached(action, status, status.current_position, stop_raw):
                            await self._async_send_action("stop")
                            return

                    if stop_delay_seconds is None:
                        if self._position_reached(action, position, target_stop_position):
                            await self._async_send_action("stop")
                            return
                    elif status.state == moving_state and self._live_position_can_adjust_stop(
                        action,
                        start_position=start_position,
                        position=position,
                        elapsed_seconds=max(now - timing_started_monotonic, 0.0),
                        planned_delay_seconds=stop_delay_seconds,
                        stop_position=target_stop_position,
                    ):
                        if self._position_reached(action, position, target_stop_position):
                            await self._async_send_action("stop")
                            return
                        if (
                            live_adjustment_position is None
                            or abs(position - live_adjustment_position)
                            >= POSITION_TARGET_LIVE_POSITION_TOLERANCE
                        ):
                            remaining_delay = self._remaining_stop_delay_seconds(
                                action,
                                position,
                                target_stop_position,
                            )
                            if remaining_delay is not None:
                                stop_deadline_monotonic = now + remaining_delay
                                live_adjustment_position = position

                if stop_deadline_monotonic is not None and now >= stop_deadline_monotonic:
                    if started_moving or now > movement_start_deadline:
                        await self._async_send_action("stop")
                        return
                if status.state == moving_state:
                    continue
                if started_moving and (status.state in terminal_states or status.state != moving_state):
                    return
                if not started_moving and now > movement_start_deadline:
                    return
        except asyncio.CancelledError:
            raise
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            _LOGGER.warning("Nice target-position authentication failed: %s", err)
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            _LOGGER.warning("Nice target-position tracking failed: %s", err)
        finally:
            if self._position_target_task is task:
                self._position_target_task = None

    @staticmethod
    def _validate_position_bounds(status: NiceBidiStatus) -> None:
        """Validate that encoder endpoints are available."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        if status.closed_position == status.open_position:
            raise HomeAssistantError("Nice encoder endpoints are identical")

    @staticmethod
    def _raw_for_percent(status: NiceBidiStatus, percent: float) -> int:
        """Convert percentage to raw encoder position."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        span = status.open_position - status.closed_position
        raw = status.closed_position + (span * (percent / 100.0))
        return round(raw)

    @staticmethod
    def _percent_for_raw(status: NiceBidiStatus, raw: int) -> float:
        """Convert raw encoder position to percentage."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        span = status.open_position - status.closed_position
        if span == 0:
            raise HomeAssistantError("Nice encoder endpoints are identical")
        return max(0.0, min(100.0, ((raw - status.closed_position) / span) * 100.0))

    @staticmethod
    def _raw_reached(action: str, status: NiceBidiStatus, current_raw: int, target_raw: int) -> bool:
        """Return true if raw position crossed target in the requested direction."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        increasing = status.open_position > status.closed_position
        if action == "open":
            return current_raw >= target_raw if increasing else current_raw <= target_raw
        return current_raw <= target_raw if increasing else current_raw >= target_raw

    @staticmethod
    def _clamp_raw(status: NiceBidiStatus, raw: int) -> int:
        """Clamp raw position to known encoder bounds."""
        if status.closed_position is None or status.open_position is None:
            raise HomeAssistantError("Nice encoder endpoints are not available")
        lower = min(status.closed_position, status.open_position)
        upper = max(status.closed_position, status.open_position)
        return max(lower, min(upper, raw))
