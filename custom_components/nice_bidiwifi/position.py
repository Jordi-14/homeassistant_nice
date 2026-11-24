"""Position tracking and set-position support for Nice."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
import logging
import time

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .calibration_constants import CALIBRATION_STATE_RUNNING
from .client import (
    NiceBidiAuthError,
    NiceBidiConnectionError,
    NiceBidiStatus,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_PARTIALLY_OPEN,
    STATE_STOPPED,
)
from .connection import CONNECTION_STATE_AUTH_FAILED, CONNECTION_STATE_FAILED
from .const import DOMAIN, ERROR_UPDATE_INTERVAL, IDLE_UPDATE_INTERVAL, MOVING_UPDATE_INTERVAL
from .controllers.base import RuntimeController
from .models.events import NiceEvent
from .models.position import NicePosition, resolve_position
from .models.runtime_state import AnimationState, EstimatorState, PositionControlState
from .movement_profile import MovementProfile, advance, speed_at

_LOGGER = logging.getLogger(__name__)

POSITION_TARGET_POLL_SECONDS = 0.5
POSITION_TARGET_TOLERANCE = 1.0
POST_COMMAND_REFRESH_DELAY_SECONDS = 2.0
POST_COMMAND_FAST_POLL_SECONDS = 60.0
RECENT_STOP_STATUS_OVERRIDE_SECONDS = 20.0
POSITION_SIMULATION_TICK_SECONDS = 0.25
POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND = 1.0
POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR = 0.9
POSITION_OBSERVED_SPEED_MIN_DELTA_PERCENT = 0.5
POSITION_OBSERVED_SPEED_MIN_INTERVAL_SECONDS = 0.25
POSITION_OBSERVED_SPEED_MAX_INTERVAL_SECONDS = 30.0
POSITION_OBSERVED_SPEED_MAX_PERCENT_PER_SECOND = 25.0
POSITION_OBSERVED_SPEED_SMOOTHING_FACTOR = 0.5
POSITION_SIMULATION_START_GRACE_SECONDS = 8.0
POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS = 30.0
POSITION_TARGET_LIVE_POSITION_TOLERANCE = 0.5
POSITION_TARGET_LIVE_SPEED_MARGIN = 3.0


def movement_profile_store(hass, entry_id: str) -> Store:
    """Keep learned movement speeds separate from calibrated stop thresholds."""
    return Store(hass, 1, f"{DOMAIN}.movement_profile.{entry_id}")


class NiceBidiPositionController(RuntimeController["NiceBidiDataUpdateCoordinator"]):
    """Position display, simulation, and target-position behavior."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self.animation = AnimationState()
        self.estimator = EstimatorState()
        self.control = PositionControlState()

    def _init_position_state(self, hass, entry) -> None:
        """Initialize position-related runtime state."""
        self.control.recent_stop: float | None = None
        self.control.stop_started_moving = False
        self.control.fast_poll_until: float | None = None
        self.estimator.last_known_position: float | None = None
        self.estimator.reporting_observed = False
        self.control.target_task: asyncio.Task[None] | None = None
        self.control.refresh_task: asyncio.Task[None] | None = None
        self.animation.task: asyncio.Task[None] | None = None
        self.animation.action: str | None = None
        self.animation.anchor_position: float | None = None
        self.animation.anchor_monotonic: float | None = None
        self.animation.started_monotonic: float | None = None
        self.animation.deadline_monotonic: float | None = None
        self.animation.confirmed_moving = False
        self.animation.target_position: float | None = None
        self.animation.speed_percent_per_second: float | None = None
        self.estimator.observation: tuple[str, float, float] | None = None
        self.estimator.observed_speeds: dict[str, float] = {}
        self.estimator.profile = MovementProfile()
        self.estimator.store = movement_profile_store(hass, entry.entry_id)
        self.estimator.dirty = False
        self.animation.speed_curve: tuple[float, ...] | None = None

    async def async_load_movement_profile(self) -> None:
        """Restore passive learning, or seed it from a saved calibration."""
        try:
            data = await self.estimator.store.async_load()
        except Exception:  # optional learning must not prevent setup
            _LOGGER.warning("Could not load Nice movement speed profile", exc_info=True)
            data = None
        if data is None and self._coordinator.calibration_controller.calibration_profile:
            data = self._coordinator.calibration_controller.calibration_profile.get("movement_speed_profile")
        self.estimator.profile.load(data)

    def _schedule_movement_profile_save(self) -> None:
        self.estimator.dirty = True
        snapshot = self.estimator.profile.export()
        self.estimator.store.async_delay_save(lambda: snapshot, 30)

    async def async_save_movement_profile(self) -> None:
        """Flush completed/partial band measurements when unloading."""
        self.estimator.dirty |= self.estimator.profile.finish()
        if self.estimator.dirty:
            try:
                await self.estimator.store.async_save(self.estimator.profile.export())
                self.estimator.dirty = False
            except Exception:
                _LOGGER.warning("Could not save Nice movement speed profile", exc_info=True)

    @property
    def movement_speed_profile(self) -> dict:
        """Measured speeds before the 90% animation margin, by direction and band."""
        return self.estimator.profile.summary()

    def _learn_movement_speed(self, status: NiceBidiStatus) -> None:
        changed = False
        for action, setting in (("open", status.opening_speed), ("close", status.closing_speed)):
            changed |= self.estimator.profile.setting_changed(action, setting)
        action = self._motion_action_from_state(status.state)
        fresh = status.observed_fields is None or "position" in status.observed_fields
        if action is not None and status.position is not None and fresh:
            changed |= self.estimator.profile.observe(action, status.position, (status.observation_times or {}).get("position", time.monotonic()))
        elif status.state in {STATE_STOPPED, STATE_PARTIALLY_OPEN, STATE_OPEN, STATE_CLOSED}:
            changed |= self.estimator.profile.finish()
        if changed:
            self._schedule_movement_profile_save()

    async def _async_cancel_position_target(self) -> None:
        """Cancel a pending target-position watcher."""
        task = self.control.target_task
        if task is None:
            return
        self.control.target_task = None
        if task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_cancel_post_command_refresh(self) -> None:
        """Cancel only the delay before a post-command refresh starts."""
        task = self.control.refresh_task
        if task is None:
            return
        self.control.refresh_task = None
        if task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_cancel_position_simulation(self) -> None:
        """Cancel the optimistic position animation."""
        task = self.animation.task
        self._clear_position_simulation(notify=False)
        if task is None or task.done() or task is asyncio.current_task():
            return
        with suppress(asyncio.CancelledError):
            await task

    def _extend_post_command_fast_poll_window(self) -> None:
        """Poll quickly for a short window after any command."""
        self.control.fast_poll_until = time.monotonic() + POST_COMMAND_FAST_POLL_SECONDS

    def _post_command_fast_poll_active(self) -> bool:
        """Return true while the post-command fast polling window is active."""
        until = self.control.fast_poll_until
        if until is None:
            return False
        if time.monotonic() <= until:
            return True
        self.control.fast_poll_until = None
        return False

    def _update_interval_for_status(self, status: NiceBidiStatus) -> timedelta:
        """Return the coordinator interval for the latest status."""
        post_command_fast_poll_active = self._post_command_fast_poll_active()
        if status.is_moving or post_command_fast_poll_active:
            return MOVING_UPDATE_INTERVAL
        return IDLE_UPDATE_INTERVAL

    def _schedule_post_command_refresh(self) -> None:
        """Schedule a delayed refresh so motor state is not missed after commands."""
        task = self.control.refresh_task
        if task is not None and not task.done():
            task.cancel()
        self.control.refresh_task = self._coordinator.tasks.create(self._async_start_post_command_refresh_after_delay(), name=f"{DOMAIN} post-command refresh delay")

    async def _async_start_post_command_refresh_after_delay(self) -> None:
        """Start an independently owned refresh after the command delay."""
        task = asyncio.current_task()
        try:
            await asyncio.sleep(POST_COMMAND_REFRESH_DELAY_SECONDS)
            if self.control.refresh_task is task:
                self.control.refresh_task = None
            self._coordinator.tasks.create(self._coordinator.async_request_refresh(), name=f"{DOMAIN} post-command refresh")
        except asyncio.CancelledError:
            raise
        finally:
            if self.control.refresh_task is task:
                self.control.refresh_task = None

    def _apply_recent_stop_status_hint(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Mask short-lived stale CU_WIFI states after a local stop command."""
        stopped_at = self.control.recent_stop
        if stopped_at is None:
            return status
        if time.monotonic() - stopped_at > RECENT_STOP_STATUS_OVERRIDE_SECONDS:
            self.control.recent_stop = None
            self.control.stop_started_moving = False
            return status
        if not self.control.stop_started_moving or status.state == STATE_STOPPED:
            return status
        if status.state not in {STATE_OPEN, STATE_CLOSED, STATE_OPENING, STATE_CLOSING}:
            return status

        registers = dict(status.registers)
        registers["NHK/RecentStopOverride"] = status.state
        return replace(status, state=STATE_STOPPED, position=None, registers=registers)

    def _normalize_status_for_display(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Align sparse status updates with the cover behavior users expect."""
        status = self._normalize_live_scalar_status(status)
        if (
            status.position is None
            and self.animation.action is None
            and self._coordinator.data is not None
            and self._coordinator.data.state in {STATE_OPEN, STATE_CLOSED}
            and status.state in {STATE_STOPPED, STATE_PARTIALLY_OPEN}
        ):
            self.estimator.last_known_position = None
            registers = dict(status.registers)
            registers["NHK/LastKnownPositionInvalidated"] = "unobserved_external_movement"
            status = replace(status, registers=registers)
        if (
            status.position is None
            and self.position_reporting_observed
            and status.state in {STATE_OPEN, STATE_CLOSED}
        ):
            registers = dict(status.registers)
            registers["NHK/ConfirmedEndpointPosition"] = status.state
            return replace(
                status,
                position=100.0 if status.state == STATE_OPEN else 0.0,
                registers=registers,
            )
        if (
            status.state in {STATE_STOPPED, STATE_PARTIALLY_OPEN}
            and status.position is None
            and self.estimator.last_known_position is not None
        ):
            registers = dict(status.registers)
            registers["NHK/LastKnownPositionFallback"] = str(round(self.estimator.last_known_position, 1))
            return replace(status, registers=registers)
        return status

    def _normalize_live_scalar_status(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Normalize a live raw value with calibrated or reported encoder bounds."""
        registers = status.registers
        scale = registers.get("NHK/T4InstantPositionScale")
        if scale is None or not scale.startswith("raw"):
            return status
        if status.position is None and "NHK/RecentStopOverride" in registers:
            return status
        raw_text = registers.get("NHK/T4InstantPositionRaw")
        if raw_text is None:
            return status
        try:
            raw = int(raw_text)
        except (TypeError, ValueError):
            return status
        normalized = self._normalize_live_scalar_value(raw, status)
        if normalized is None:
            return status
        percent, marker = normalized
        updated_registers = dict(registers)
        updated_registers.pop("NHK/T4CalibratedPosition", None)
        updated_registers.pop("NHK/T4EncoderPosition", None)
        updated_registers[marker] = str(percent)
        updated_registers["NHK/T4InstantPosition"] = str(round(percent))
        return replace(status, position=percent, registers=updated_registers)

    def _normalize_live_position_event(self, event: NiceEvent) -> NiceEvent:
        """Keep the public event position consistent with the displayed position."""
        if (
            event.t4_raw_position is None
            or event.t4_position_scale is None
            or not event.t4_position_scale.startswith("raw")
        ):
            return event
        normalized = self._normalize_live_scalar_value(event.t4_raw_position, self._coordinator.data)
        return replace(event, position=normalized[0]) if normalized is not None else event

    def _normalize_live_scalar_value(
        self, raw: int, status: NiceBidiStatus | None
    ) -> tuple[float, str] | None:
        """Prefer explicit scalar calibration, then usable 16-bit encoder bounds."""
        candidates = []
        profile = self._coordinator.calibration_controller.calibration_profile
        if isinstance(profile, dict) and profile.get("mode") == "live_scalar":
            bounds = profile.get("bounds")
            if isinstance(bounds, dict):
                candidates.append((
                    bounds.get("live_scalar_closed_raw"),
                    bounds.get("live_scalar_open_raw"),
                    "NHK/T4CalibratedPosition",
                ))
        if status is not None:
            candidates.append((status.closed_position, status.open_position, "NHK/T4EncoderPosition"))
        for closed_raw, open_raw, marker in candidates:
            if (
                not all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFF for value in (closed_raw, open_raw))
                or closed_raw == open_raw
            ):
                continue
            percent = max(0.0, min(100.0, ((raw - closed_raw) / (open_raw - closed_raw)) * 100.0))
            return round(percent, 1), marker
        return None

    @property
    def position_snapshot(self) -> NicePosition | None:
        """Return the normalized position with provenance and confidence."""
        return resolve_position(
            self._coordinator.data,
            simulated=self._current_simulated_position(),
            last_known=self.estimator.last_known_position,
        )

    @property
    def display_position(self) -> float | None:
        """Return the position HA should display."""
        position = self.position_snapshot
        return round(position.value, 1) if position is not None else None

    @property
    def position_reporting_observed(self) -> bool:
        """Return true only after the controller has supplied a numeric position."""
        status = self._coordinator.data
        return self.estimator.reporting_observed or (
            status is not None and status.position is not None
        )

    @property
    def display_position_estimated(self) -> bool:
        """Return true when the displayed position is currently estimated."""
        position = self.position_snapshot
        return bool(position and position.estimated)

    @property
    def position_simulation_action(self) -> str | None:
        """Return the active simulated direction."""
        return self.animation.action if self.display_position_estimated else None

    @property
    def position_simulation_speed_percent_per_second(self) -> float | None:
        """Return the active simulated display speed."""
        if not self.display_position_estimated:
            return None
        speed = self.animation.speed_percent_per_second
        curve = self.animation.speed_curve
        if curve is not None:
            position = self._current_simulated_position()
            if position is not None:
                speed = speed_at(position, curve)
        return round(speed, 2) if speed is not None else None

    @property
    def observed_position_speed_percent_per_second(self) -> dict[str, float]:
        """Return movement speeds learned from live position updates."""
        return {
            action: round(speed, 2)
            for action, speed in self.estimator.observed_speeds.items()
        }

    @property
    def state_source(self) -> str | None:
        """Return the source currently responsible for the displayed state."""
        status = self._coordinator.data
        if status is None:
            return None
        registers = status.registers
        if "NHK/RecentStopOverride" in registers:
            return "local_stop_hold"
        observation = self._coordinator.observations.observation("state")
        if observation is not None and observation.source != "baseline":
            return observation.source
        payload_kind = registers.get("NHK/T4PayloadKind")
        t4_state = registers.get("NHK/T4Status")
        if payload_kind == "04/02" and t4_state == status.state:
            return "t4_04_02"
        if payload_kind == "04/40" and t4_state == status.state:
            return "t4_04_40_motion"
        if "NHK/DoorStatus" in registers:
            return "nhk_door_status"
        if "04/01" in registers:
            return "dmp_04_01"
        return "unknown"

    @property
    def position_source(self) -> str | None:
        """Return the source currently responsible for displayed position."""
        position = self.position_snapshot
        return position.source if position is not None else None

    @property
    def position_confidence(self) -> str | None:
        """Return whether displayed position is measured, live, or estimated."""
        position = self.position_snapshot
        return position.confidence if position is not None else None

    def _start_position_simulation(self, action: str, *, target_position: float | None = None) -> None:
        """Start or restart optimistic position animation after a movement command."""
        if not self.position_reporting_observed:
            return
        anchor = None
        if action != self.animation.action and self._coordinator.data is not None:
            anchor = self._coordinator.data.position
        if anchor is None:
            anchor = self._current_simulated_position()
        if anchor is None:
            anchor = self.display_position
        if anchor is None:
            return

        now = time.monotonic()
        curve = self._coordinator.calibration_controller._position_speed_curve(action)
        speed = speed_at(anchor, curve)
        target = None if target_position is None else max(0.0, min(100.0, float(target_position)))
        limit = 100.0 if action == "open" and target is None else 0.0 if target is None else target
        distance = abs(limit - anchor)
        expected_seconds = distance / min(curve)
        deadline = now + max(
            POSITION_SIMULATION_START_GRACE_SECONDS,
            expected_seconds + POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
        )

        self.animation.action = action
        self.animation.anchor_position = anchor
        self.animation.anchor_monotonic = now
        self.animation.started_monotonic = now
        self.animation.deadline_monotonic = deadline
        self.animation.confirmed_moving = False
        self.animation.target_position = target
        self.animation.speed_percent_per_second = speed
        self.animation.speed_curve = curve
        self._ensure_position_simulation_task()
        self._coordinator.async_update_listeners()

    def _ensure_position_simulation_task(self) -> None:
        """Ensure the simulation tick task is running."""
        task = self.animation.task
        if task is not None and not task.done():
            return
        self.animation.task = self._coordinator.tasks.create(self._async_run_position_simulation(), name=f"{DOMAIN} position simulation")

    async def _async_run_position_simulation(self) -> None:
        """Tick the optimistic display position until real status stops it."""
        task = asyncio.current_task()
        try:
            while self.animation.action is not None:
                await asyncio.sleep(POSITION_SIMULATION_TICK_SECONDS)
                deadline = self.animation.deadline_monotonic
                if deadline is not None and time.monotonic() > deadline:
                    self._clear_position_simulation()
                    return
                self._coordinator.async_update_listeners()
        except asyncio.CancelledError:
            raise
        finally:
            if self.animation.task is task:
                self.animation.task = None

    def _current_simulated_position(self) -> float | None:
        """Return the current synthetic display position."""
        action = self.animation.action
        anchor = self.animation.anchor_position
        anchor_time = self.animation.anchor_monotonic
        speed = self.animation.speed_percent_per_second
        if action is None or anchor is None or anchor_time is None or speed is None:
            return None
        if not self.animation.confirmed_moving:
            return anchor

        elapsed = max(0.0, time.monotonic() - anchor_time)
        curve = self.animation.speed_curve
        calculated = advance(anchor, elapsed, action, curve) if curve is not None else anchor + (1 if action == "open" else -1) * speed * elapsed
        if action == "open":
            target = self.animation.target_position
            if target is None:
                cap = 100.0 if anchor >= 100.0 else max(99.0, anchor)
            else:
                cap = target if anchor < target else anchor
            return max(0.0, min(calculated, cap))

        target = self.animation.target_position
        if target is None:
            floor = 0.0 if anchor <= 0.0 else min(1.0, anchor)
        else:
            floor = target if anchor > target else anchor
        return min(100.0, max(calculated, floor))

    def _sync_position_simulation_from_status(self, status: NiceBidiStatus) -> None:
        """Rebase or stop simulated display movement from a real status update."""
        self._observe_position_speed(status)
        if status.observed_fields is not None and not {"state", "position"} & status.observed_fields:
            return
        if status.observed_fields is not None and "position" not in status.observed_fields:
            status = replace(status, position=None)
        if self._coordinator.calibration_controller.calibration_state == CALIBRATION_STATE_RUNNING:
            self._clear_position_simulation(notify=False)
            return
        real_action = self._motion_action_from_state(status.state)
        if (
            self.animation.action == "open"
            and status.state == STATE_OPEN
            or self.animation.action == "close"
            and status.state == STATE_CLOSED
        ):
            self._clear_position_simulation(notify=False)
            return
        if status.position is None:
            if real_action is not None:
                if self.animation.action is None:
                    self._start_position_simulation(real_action)
                if self.animation.action == real_action:
                    if not self.animation.confirmed_moving:
                        self.animation.anchor_monotonic = time.monotonic()
                    self.animation.confirmed_moving = True
                return
            if status.state in {STATE_STOPPED, STATE_PARTIALLY_OPEN}:
                self._hold_position_simulation()
            return

        if real_action is not None:
            self._rebase_position_simulation(
                real_action,
                status.position,
                confirmed_moving=True,
                keep_target=real_action == self.animation.action,
            )
            return

        if self.animation.action is None:
            return
        if not self.animation.confirmed_moving:
            started = self.animation.started_monotonic
            if (
                started is not None
                and time.monotonic() - started < POSITION_SIMULATION_START_GRACE_SECONDS
            ):
                return
        self._clear_position_simulation(notify=False)

    def _observe_position_speed(self, status: NiceBidiStatus) -> None:
        """Learn movement speed from consecutive live position samples."""
        self._learn_movement_speed(status)
        if status.observed_fields is not None and "position" not in status.observed_fields:
            return
        action = self._motion_action_from_state(status.state)
        position = status.position
        if action is None or position is None:
            self.estimator.observation = None
            return

        now = (status.observation_times or {}).get("position", time.monotonic())
        previous = self.estimator.observation
        if previous is None or previous[0] != action:
            self.estimator.observation = (action, position, now)
            return

        _, previous_position, previous_time = previous
        elapsed = now - previous_time
        if elapsed < POSITION_OBSERVED_SPEED_MIN_INTERVAL_SECONDS:
            return
        if elapsed > POSITION_OBSERVED_SPEED_MAX_INTERVAL_SECONDS:
            self.estimator.observation = (action, position, now)
            return

        distance = (
            position - previous_position
            if action == "open"
            else previous_position - position
        )
        if distance < 0:
            self.estimator.observation = (action, position, now)
            return
        if distance == 0:
            return
        if distance < POSITION_OBSERVED_SPEED_MIN_DELTA_PERCENT:
            return

        speed = distance / elapsed
        self.estimator.observation = (action, position, now)
        if not 0 < speed <= POSITION_OBSERVED_SPEED_MAX_PERCENT_PER_SECOND:
            return
        previous_speed = self.estimator.observed_speeds.get(action)
        if previous_speed is not None:
            smoothing = POSITION_OBSERVED_SPEED_SMOOTHING_FACTOR
            speed = (previous_speed * (1 - smoothing)) + (speed * smoothing)
        self.estimator.observed_speeds[action] = speed

    def _hold_position_simulation(self) -> None:
        """Freeze an estimated position when movement ends without real position."""
        simulated = self._current_simulated_position()
        if simulated is not None:
            self.estimator.last_known_position = round(simulated, 1)
        self._clear_position_simulation(notify=False)

    def _rebase_position_simulation(
        self,
        action: str,
        position: float,
        *,
        confirmed_moving: bool,
        keep_target: bool,
    ) -> None:
        """Follow readings without rewinding the display during continuous motion."""
        now = time.monotonic()
        if action == self.animation.action:
            displayed = self._current_simulated_position()
            if displayed is not None:
                position = max(position, displayed) if action == "open" else min(position, displayed)
        curve = self._coordinator.calibration_controller._position_speed_curve(action)
        speed = speed_at(position, curve)
        target = self.animation.target_position if keep_target else None
        limit = 100.0 if action == "open" and target is None else 0.0 if target is None else target
        distance = abs(limit - position)
        expected_seconds = distance / min(curve)
        self.animation.action = action
        self.animation.anchor_position = max(0.0, min(100.0, position))
        self.animation.anchor_monotonic = now
        if self.animation.started_monotonic is None:
            self.animation.started_monotonic = now
        self.animation.deadline_monotonic = now + max(
            POSITION_SIMULATION_START_GRACE_SECONDS,
            expected_seconds + POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
        )
        self.animation.confirmed_moving = confirmed_moving
        self.animation.target_position = target
        self.animation.speed_percent_per_second = speed
        self.animation.speed_curve = curve
        self._ensure_position_simulation_task()

    def _clear_position_simulation(self, *, notify: bool = True) -> None:
        """Clear optimistic position animation state."""
        task = self.animation.task
        self.animation.task = None
        self.animation.action = None
        self.animation.anchor_position = None
        self.animation.anchor_monotonic = None
        self.animation.started_monotonic = None
        self.animation.deadline_monotonic = None
        self.animation.confirmed_moving = False
        self.animation.target_position = None
        self.animation.speed_percent_per_second = None
        self.animation.speed_curve = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        if notify:
            self._coordinator.async_update_listeners()

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
        speed = self._coordinator.calibration_controller._calibrated_travel_speed_percent_per_second(action)
        if speed is None or speed <= 0:
            return None
        if action == "open":
            remaining = stop_position - position
        else:
            remaining = position - stop_position
        return max(0.0, remaining) / speed

    async def async_set_position(self, target_position: int) -> None:
        """Move toward a target percentage and stop after the target is reached."""
        await self._coordinator.calibration_controller._async_cancel_calibration(reason="set_position")
        await self._async_cancel_post_command_refresh()
        target = max(0, min(100, target_position))
        status = self._coordinator.data
        current = status.position if status is not None and status.position is not None else self.display_position
        if status is None or current is None:
            await self._coordinator.async_request_refresh()
            status = self._coordinator.data
            current = status.position if status is not None and status.position is not None else self.display_position
        if status is None or current is None:
            raise HomeAssistantError("Nice position is not available")

        if target <= 0:
            await self._coordinator.async_send_action("close")
            return
        if target >= 100:
            await self._coordinator.async_send_action("open")
            return
        if abs(current - target) <= POSITION_TARGET_TOLERANCE:
            if status.is_moving:
                await self._coordinator.async_send_action("stop")
            return

        action = "open" if target > current else "close"
        calibrated_stop_percent = self._coordinator.calibration_controller._calibrated_stop_percent(float(target), action)
        stop_position = float(target) if calibrated_stop_percent is None else calibrated_stop_percent
        stop_raw = None
        if calibrated_stop_percent is not None and self._coordinator.calibration_controller._has_encoder_calibration_data(status):
            stop_raw = self._raw_for_percent(status, stop_position)
        stop_delay_seconds = self._coordinator.calibration_controller._calibrated_stop_delay_seconds(current, stop_position, action)
        await self._async_cancel_position_target()
        await self._coordinator._async_send_action(action, refresh=False, simulation_target_position=target)
        self.control.target_task = self._coordinator.tasks.create(self._async_stop_at_position(
                target,
                action,
                stop_raw,
                stop_delay_seconds=stop_delay_seconds,
                start_position=current,
                stop_position=stop_position,
            ), name=f"{DOMAIN} stop at {target}%")

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
        timing_started_monotonic = time.monotonic()
        movement_start_deadline = time.monotonic() + 8.0
        stop_deadline_monotonic = (
            timing_started_monotonic + stop_delay_seconds
            if stop_delay_seconds is not None
            else None
        )
        live_adjustment_position = start_position
        target_stop_position = float(target) if stop_position is None else float(stop_position)
        try:
            while True:
                await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
                now = time.monotonic()
                status = await self._coordinator.calibration_controller._async_read_motion_status()

                if status.state == moving_state:
                    started_moving = True

                position = status.position
                if position is not None:
                    if stop_raw is not None and status.current_position is not None:
                        if self._raw_reached(action, status, status.current_position, stop_raw):
                            await self._coordinator._async_send_action("stop")
                            return

                    if stop_delay_seconds is None:
                        if self._position_reached(action, position, target_stop_position):
                            await self._coordinator._async_send_action("stop")
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
                            await self._coordinator._async_send_action("stop")
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
                                candidate_deadline = now + remaining_delay
                                stop_deadline_monotonic = min(
                                    stop_deadline_monotonic,
                                    candidate_deadline,
                                )
                                live_adjustment_position = position

                if stop_deadline_monotonic is not None and now >= stop_deadline_monotonic:
                    await self._coordinator._async_send_action("stop")
                    return
                if status.state == moving_state:
                    continue
                if stop_delay_seconds is not None:
                    continue
                if status.state in {STATE_OPEN, STATE_CLOSED}:
                    return
                if started_moving and status.state in terminal_states:
                    return
                if not started_moving and now > movement_start_deadline:
                    return
        except asyncio.CancelledError:
            raise
        except NiceBidiAuthError as err:
            await self._coordinator.hass.async_add_executor_job(self._coordinator.client.close)
            self._coordinator.connection_runtime.state = CONNECTION_STATE_AUTH_FAILED
            self._coordinator.connection_runtime.last_error = str(err)
            _LOGGER.warning("Nice target-position authentication failed: %s", err)
        except (NiceBidiConnectionError, OSError) as err:
            await self._coordinator.hass.async_add_executor_job(self._coordinator.client.close)
            self._coordinator.connection_runtime.state = CONNECTION_STATE_FAILED
            self._coordinator.connection_runtime.last_error = str(err)
            self._coordinator.update_interval = ERROR_UPDATE_INTERVAL
            _LOGGER.warning("Nice target-position tracking failed: %s", err)
        finally:
            if self.control.target_task is task:
                self.control.target_task = None

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
