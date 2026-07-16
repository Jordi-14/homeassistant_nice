"""Position calibration support for Nice."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .calibration_constants import (
    CALIBRATION_COMMAND_PAUSE_SECONDS,
    CALIBRATION_ENDPOINT_TOLERANCE,
    CALIBRATION_MAX_ATTEMPTS,
    CALIBRATION_MOVEMENT_TIMEOUT_SECONDS,
    CALIBRATION_OUTLIER_ERROR_PERCENT,
    CALIBRATION_REPORT_LOG_CHUNK_SIZE,
    CALIBRATION_SETTLE_SECONDS,
    CALIBRATION_SETTLE_TIMEOUT_SECONDS,
    CALIBRATION_STABILITY_ATTEMPTS,
    CALIBRATION_STATE_CALIBRATED,
    CALIBRATION_STATE_CANCELLED,
    CALIBRATION_STATE_FAILED,
    CALIBRATION_STATE_NOT_CALIBRATED,
    CALIBRATION_STATE_RUNNING,
    CALIBRATION_STOPPED_ENDPOINT_GRACE_SECONDS,
    CALIBRATION_STOPPED_ENDPOINT_MIN_DURATION_RATIO,
    CALIBRATION_STORAGE_VERSION,
    CALIBRATION_TARGET_TOLERANCE_PERCENT,
    CALIBRATION_TARGETS,
    CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS,
)
from .calibration_report import (
    build_calibration_report,
    build_live_calibration_report,
    calibration_quality as report_calibration_quality,
    calibration_report_attributes as report_calibration_report_attributes,
    calibration_report_summary as report_calibration_report_summary,
    format_calibration_report,
)
from .calibration_types import CalibrationEvent, CalibrationProfile, CalibrationReport
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
from .const import DOMAIN, ERROR_UPDATE_INTERVAL
from .position import (
    POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR,
    POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND,
    POSITION_SIMULATION_START_GRACE_SECONDS,
    POSITION_TARGET_POLL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

CALIBRATION_MODE_ENCODER = "encoder"
CALIBRATION_MODE_LIVE_PERCENT = "live_percent"
CALIBRATION_MODE_LIVE_SCALAR = "live_scalar"
CALIBRATION_MODE_TIME = "time"


@dataclass
class _CalibrationPositionSource:
    """Position source selected during one calibration run."""

    mode: str
    scalar_closed_raw: int | None = None
    scalar_open_raw: int | None = None
    scalar_min_raw: int | None = None
    scalar_max_raw: int | None = None


class NiceBidiCalibrationMixin:
    """Calibration state, movement routine, and report behavior."""

    def _init_calibration_state(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize calibration-related runtime state."""
        self._calibration_task: asyncio.Task[None] | None = None
        self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
        self.calibration_last_error: str | None = None
        self.calibration_updated_at: datetime | None = None
        self.calibration_profile: CalibrationProfile | None = None
        self.calibration_report: CalibrationReport | None = None
        self._calibration_events: list[CalibrationEvent] = []
        self._calibration_store = Store[CalibrationProfile](
            hass,
            CALIBRATION_STORAGE_VERSION,
            f"{DOMAIN}.calibration.{entry.entry_id}",
        )

    async def async_load_calibration(self) -> None:
        """Load stored position calibration data."""
        try:
            stored_profile = await self._calibration_store.async_load()
        except Exception as err:
            self.calibration_profile = None
            self.calibration_report = None
            self.calibration_updated_at = None
            self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
            self.calibration_last_error = f"Stored calibration could not be loaded: {err}"
            _LOGGER.warning("Nice stored calibration could not be loaded: %s", err)
            return

        if not isinstance(stored_profile, dict):
            self.calibration_profile = None
            self.calibration_report = None
            self.calibration_updated_at = None
            self.calibration_state = CALIBRATION_STATE_NOT_CALIBRATED
            self.calibration_last_error = None
            return

        self.calibration_profile = stored_profile
        self.calibration_state = CALIBRATION_STATE_CALIBRATED
        self.calibration_last_error = None
        self.calibration_report = self._build_calibration_report(stored_profile)
        updated_at = self.calibration_profile.get("updated_at")
        if isinstance(updated_at, str):
            with suppress(ValueError):
                self.calibration_updated_at = datetime.fromisoformat(updated_at)

    async def _async_cancel_calibration(self, *, stop: bool = True) -> None:
        """Cancel a running calibration task."""
        task = self._calibration_task
        if task is None:
            return
        if task.done():
            self._calibration_task = None
            return
        if task is asyncio.current_task():
            return

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if stop:
            with suppress(HomeAssistantError):
                await self._async_send_action("stop")

    async def async_start_position_calibration(self) -> None:
        """Start a background position calibration run."""
        task = self._calibration_task
        if task is not None and not task.done():
            raise HomeAssistantError("Nice position calibration is already running")

        await self._async_cancel_position_target()
        await self._async_cancel_post_command_refresh()
        self._calibration_events = []
        self.calibration_state = CALIBRATION_STATE_RUNNING
        self.calibration_last_error = None
        self.calibration_report = self._build_live_calibration_report("Calibration started")
        self._add_calibration_event(
            "run",
            "Position calibration requested",
            targets=list(CALIBRATION_TARGETS),
            max_attempts=CALIBRATION_MAX_ATTEMPTS,
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
            tolerance_percent=CALIBRATION_TARGET_TOLERANCE_PERCENT,
            poll_seconds=POSITION_TARGET_POLL_SECONDS,
            command_pause_seconds=CALIBRATION_COMMAND_PAUSE_SECONDS,
        )
        self.async_update_listeners()
        self._calibration_task = self.hass.async_create_task(
            self._async_run_position_calibration(),
            name=f"{DOMAIN} position calibration",
        )

    async def _async_run_position_calibration(self) -> None:
        """Run a predefined movement pattern and store learned stop thresholds."""
        task = asyncio.current_task()
        try:
            profile = await self._async_build_position_calibration()
            self._add_calibration_event("run", "Position calibration completed")
            profile["events"] = list(self._calibration_events)
            self.calibration_profile = profile
            self.calibration_state = CALIBRATION_STATE_CALIBRATED
            self.calibration_last_error = None
            self.calibration_report = self._build_calibration_report(profile)
            updated_at = profile.get("updated_at")
            if isinstance(updated_at, str):
                self.calibration_updated_at = datetime.fromisoformat(updated_at)
            await self._calibration_store.async_save(profile)
            self._log_calibration_report(self.calibration_report, "completed")
        except asyncio.CancelledError:
            self.calibration_state = CALIBRATION_STATE_CANCELLED
            self.calibration_last_error = "cancelled"
            self._add_calibration_event("run", "Position calibration cancelled")
            self.calibration_report = self._build_live_calibration_report("Calibration cancelled")
            raise
        except (NiceBidiAuthError, NiceBidiConnectionError, OSError, HomeAssistantError) as err:
            self.calibration_state = CALIBRATION_STATE_FAILED
            self.calibration_last_error = str(err)
            self._add_calibration_event("run", "Position calibration failed", error=str(err))
            self.calibration_report = self._build_live_calibration_report("Calibration failed")
            self._log_calibration_report(self.calibration_report, "failed")
            _LOGGER.warning("Nice position calibration failed: %s", err)
        finally:
            if self._calibration_task is task:
                self._calibration_task = None
            self.async_update_listeners()

    async def _async_build_position_calibration(self) -> CalibrationProfile:
        """Build a direction-specific calibration profile."""
        started_at = datetime.now(UTC)
        self._add_calibration_event("run", "Reading initial status")
        start_status = await self._async_read_motion_status()
        if not self._has_encoder_calibration_data(start_status):
            source = self._calibration_position_source_for_status(start_status)
            self._add_calibration_event(
                "run",
                "Encoder position data unavailable; starting standardized non-encoder calibration",
                mode=source.mode,
                state=start_status.state,
                position=start_status.position,
                current_raw=start_status.current_position,
                closed_raw=start_status.closed_position,
                open_raw=start_status.open_position,
                live_raw=self._live_scalar_raw_from_status(start_status),
                live_scale=start_status.registers.get("NHK/T4InstantPositionScale"),
            )
            return await self._async_build_non_encoder_position_calibration(
                started_at,
                start_status,
                source,
            )

        self._validate_position_bounds(start_status)
        self._add_calibration_event(
            "run",
            "Initial encoder bounds read",
            current_raw=start_status.current_position,
            current_percent=start_status.position,
            closed_raw=start_status.closed_position,
            open_raw=start_status.open_position,
            state=start_status.state,
        )

        self._add_calibration_event("speed", "Moving fully closed before speed calibration")
        speed_start_status = await self._async_move_to_end("close")
        self._add_calibration_event("speed", "Measuring full opening speed")
        opening_travel = await self._async_measure_full_travel("open")
        self._add_calibration_event("speed", "Measuring full closing speed")
        closing_travel = await self._async_measure_full_travel("close")

        opening_samples = []
        for target in CALIBRATION_TARGETS:
            self._add_calibration_event("target", f"Opening-side calibration for {target}% started")
            opening_samples.append(await self._async_calibrate_target_from_endpoint(target, "open", "close"))

        self._add_calibration_event("endpoint", "Moving fully open before closing-side calibration")
        open_status = await self._async_move_to_end("open")
        closing_samples = []
        for target in reversed(CALIBRATION_TARGETS):
            self._add_calibration_event("target", f"Closing-side calibration for {target}% started")
            closing_samples.append(await self._async_calibrate_target_from_endpoint(target, "close", "open"))

        self._add_calibration_event("endpoint", "Final close started")
        final_status = await self._async_move_to_end("close")
        self._add_calibration_event(
            "endpoint",
            "Final close completed",
            current_raw=final_status.current_position,
            current_percent=final_status.position,
            state=final_status.state,
        )
        updated_at = datetime.now(UTC)

        return {
            "version": 5,
            "mode": CALIBRATION_MODE_ENCODER,
            "created_at": started_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "poll_seconds": POSITION_TARGET_POLL_SECONDS,
            "settle_seconds": CALIBRATION_SETTLE_SECONDS,
            "command_pause_seconds": CALIBRATION_COMMAND_PAUSE_SECONDS,
            "max_attempts": CALIBRATION_MAX_ATTEMPTS,
            "stability_attempts": CALIBRATION_STABILITY_ATTEMPTS,
            "target_tolerance_percent": CALIBRATION_TARGET_TOLERANCE_PERCENT,
            "targets": list(CALIBRATION_TARGETS),
            "bounds": {
                "initial_closed_raw": start_status.closed_position,
                "initial_open_raw": start_status.open_position,
                "speed_start_closed_raw": speed_start_status.closed_position,
                "speed_start_open_raw": speed_start_status.open_position,
                "open_run_closed_raw": open_status.closed_position,
                "open_run_open_raw": open_status.open_position,
                "final_closed_raw": final_status.closed_position,
                "final_open_raw": final_status.open_position,
                "final_state": final_status.state,
            },
            "travel_speed": {
                "open": opening_travel,
                "close": closing_travel,
            },
            "samples": {
                "open": sorted(opening_samples, key=lambda sample: sample["target_percent"]),
                "close": sorted(closing_samples, key=lambda sample: sample["target_percent"]),
            },
        }

    async def _async_build_non_encoder_position_calibration(
        self,
        started_at: datetime,
        start_status: NiceBidiStatus,
        source: _CalibrationPositionSource,
    ) -> CalibrationProfile:
        """Build a calibration profile without DMP encoder bounds."""
        self._add_calibration_event(
            "run",
            "Initial non-encoder calibration status read",
            mode=source.mode,
            state=start_status.state,
            position=start_status.position,
            live_raw=self._live_scalar_raw_from_status(start_status),
            live_scale=start_status.registers.get("NHK/T4InstantPositionScale"),
        )

        self._add_calibration_event("speed", "Moving fully closed before standardized calibration")
        closed_status = await self._async_move_to_end_with_position_source("close", source)
        opening_travel_samples = []
        closing_travel_samples = []
        for attempt in range(1, CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS + 1):
            self._add_calibration_event(
                "speed",
                f"Measuring standardized full opening {attempt}/{CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS}",
                mode=source.mode,
            )
            opening_travel_samples.append(
                await self._async_measure_full_travel_with_position_source(
                    "open",
                    source,
                    attempt=attempt,
                )
            )
            expected_close_duration_ms = self._median_time_travel_duration_ms(opening_travel_samples)
            self._add_calibration_event(
                "speed",
                f"Measuring standardized full closing {attempt}/{CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS}",
                mode=source.mode,
            )
            closing_travel_samples.append(
                await self._async_measure_full_travel_with_position_source(
                    "close",
                    source,
                    attempt=attempt,
                    expected_duration_ms=expected_close_duration_ms,
                )
            )
            self._finalize_live_scalar_bounds(source)
            if self._calibration_source_can_measure_targets(source):
                break

        if self._calibration_source_can_measure_targets(source):
            opening_travel = opening_travel_samples[-1]
            closing_travel = closing_travel_samples[-1]
            opening_samples = []
            for target in CALIBRATION_TARGETS:
                self._add_calibration_event(
                    "target",
                    f"Opening-side calibration for {target}% started",
                    mode=source.mode,
                )
                opening_samples.append(
                    await self._async_calibrate_target_from_endpoint_with_position_source(
                        target,
                        "open",
                        "close",
                        source,
                    )
                )

            self._add_calibration_event("endpoint", "Moving fully open before closing-side calibration")
            open_status = await self._async_move_to_end_with_position_source("open", source)
            closing_samples = []
            for target in reversed(CALIBRATION_TARGETS):
                self._add_calibration_event(
                    "target",
                    f"Closing-side calibration for {target}% started",
                    mode=source.mode,
                )
                closing_samples.append(
                    await self._async_calibrate_target_from_endpoint_with_position_source(
                        target,
                        "close",
                        "open",
                        source,
                    )
                )
            targets = list(CALIBRATION_TARGETS)
            max_attempts = CALIBRATION_MAX_ATTEMPTS
            stability_attempts = CALIBRATION_STABILITY_ATTEMPTS
        else:
            source.mode = CALIBRATION_MODE_TIME
            opening_travel = self._select_time_travel_sample("open", opening_travel_samples)
            closing_travel = self._select_time_travel_sample("close", closing_travel_samples)
            opening_samples = []
            closing_samples = []
            open_status = None
            targets = []
            max_attempts = CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS
            stability_attempts = 1

        self._add_calibration_event("endpoint", "Final close started")
        final_status = await self._async_move_to_end_with_position_source("close", source)
        final_percent = self._calibration_percent_for_status(final_status, source)
        self._add_calibration_event(
            "endpoint",
            "Final close completed",
            mode=source.mode,
            current_percent=final_percent,
            state=final_status.state,
        )
        updated_at = datetime.now(UTC)

        return {
            "version": 7,
            "mode": source.mode,
            "created_at": started_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "poll_seconds": POSITION_TARGET_POLL_SECONDS,
            "settle_seconds": CALIBRATION_SETTLE_SECONDS,
            "command_pause_seconds": CALIBRATION_COMMAND_PAUSE_SECONDS,
            "max_attempts": max_attempts,
            "stability_attempts": stability_attempts,
            "target_tolerance_percent": CALIBRATION_TARGET_TOLERANCE_PERCENT,
            "targets": targets,
            "bounds": {
                "mode": source.mode,
                "initial_state": start_status.state,
                "initial_position": start_status.position,
                "closed_state": closed_status.state,
                "open_state": open_status.state if open_status is not None else None,
                "final_state": final_status.state,
                "full_travel_attempts": len(opening_travel_samples),
                **self._calibration_source_bounds(source),
            },
            "travel_speed": {
                "open": opening_travel,
                "close": closing_travel,
            },
            "samples": {
                "open": sorted(opening_samples, key=lambda sample: sample["target_percent"]),
                "close": sorted(closing_samples, key=lambda sample: sample["target_percent"]),
            },
        }

    async def _async_build_time_position_calibration(
        self,
        started_at: datetime,
        start_status: NiceBidiStatus,
    ) -> CalibrationProfile:
        """Build a time-based calibration profile for devices without encoders."""
        self._add_calibration_event(
            "run",
            "Initial time-calibration status read",
            state=start_status.state,
            position=start_status.position,
        )
        self._add_calibration_event("speed", "Moving fully closed before time calibration")
        closed_status = await self._async_move_to_end_without_encoder("close")
        opening_samples = []
        closing_samples = []
        for attempt in range(1, CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS + 1):
            self._add_calibration_event(
                "speed",
                f"Measuring timed full opening {attempt}/{CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS}",
            )
            opening_samples.append(
                await self._async_measure_full_travel_time(
                    "open",
                    attempt=attempt,
                )
            )
            expected_close_duration_ms = self._median_time_travel_duration_ms(opening_samples)
            self._add_calibration_event(
                "speed",
                f"Measuring timed full closing {attempt}/{CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS}",
            )
            closing_samples.append(
                await self._async_measure_full_travel_time(
                    "close",
                    attempt=attempt,
                    expected_duration_ms=expected_close_duration_ms,
                )
            )

        opening_travel = self._select_time_travel_sample("open", opening_samples)
        closing_travel = self._select_time_travel_sample("close", closing_samples)
        updated_at = datetime.now(UTC)

        return {
            "version": 6,
            "mode": "time",
            "created_at": started_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "poll_seconds": POSITION_TARGET_POLL_SECONDS,
            "settle_seconds": CALIBRATION_SETTLE_SECONDS,
            "command_pause_seconds": CALIBRATION_COMMAND_PAUSE_SECONDS,
            "max_attempts": CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS,
            "stability_attempts": 1,
            "target_tolerance_percent": CALIBRATION_TARGET_TOLERANCE_PERCENT,
            "targets": [],
            "bounds": {
                "mode": "time",
                "initial_state": start_status.state,
                "initial_position": start_status.position,
                "closed_state": closed_status.state,
                "final_state": closing_travel.get("end_state"),
                "full_travel_attempts": CALIBRATION_TIME_FULL_TRAVEL_ATTEMPTS,
            },
            "travel_speed": {
                "open": opening_travel,
                "close": closing_travel,
            },
            "samples": {
                "open": [],
                "close": [],
            },
        }

    async def _async_move_to_end_without_encoder(self, action: str) -> NiceBidiStatus:
        """Move fully open or closed using only state/endpoint status."""
        status = await self._async_read_motion_status()
        if self._is_at_endpoint(status, action):
            self._add_calibration_event(
                "endpoint",
                f"Already at {action} endpoint",
                action=action,
                current_percent=status.position,
                state=status.state,
                mode="time",
            )
            return status

        self._add_calibration_event(
            "endpoint",
            f"Moving to {action} endpoint",
            action=action,
            current_percent=status.position,
            state=status.state,
            mode="time",
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        started = time.monotonic()
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + 8.0
        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                self._add_calibration_event(
                    "endpoint",
                    f"Reached {action} endpoint",
                    action=action,
                    current_percent=status.position,
                    state=status.state,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    mode="time",
                )
                await self._async_pause_before_next_calibration_command()
                return status
            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (started_moving or time.monotonic() > movement_start_deadline):
                stopped_at = time.monotonic()
                self._add_calibration_event(
                    "endpoint",
                    f"{action} endpoint reported stopped before endpoint confirmation",
                    action=action,
                    current_percent=status.position,
                    state=status.state,
                    duration_ms=round((stopped_at - started) * 1000),
                    mode="time",
                )
                confirmed = await self._async_wait_for_endpoint_after_stopped(action)
                if confirmed is not None:
                    self._add_calibration_event(
                        "endpoint",
                        f"Reached {action} endpoint after stopped confirmation",
                        action=action,
                        current_percent=confirmed.position,
                        state=confirmed.state,
                        duration_ms=round((stopped_at - started) * 1000),
                        mode="time",
                    )
                    await self._async_pause_before_next_calibration_command()
                    return confirmed
                raise HomeAssistantError(f"Position calibration stopped before reaching {action} endpoint")

        raise HomeAssistantError(f"Timed out moving to {action} endpoint during position calibration")

    async def _async_measure_full_travel_time(
        self,
        action: str,
        *,
        attempt: int | None = None,
        expected_duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """Measure full travel duration when encoder position is unavailable."""
        start_status = await self._async_read_motion_status()
        if self._is_at_endpoint(start_status, action):
            raise HomeAssistantError(f"Nice gate is already at {action} endpoint")

        start_percent = 0.0 if action == "open" else 100.0
        end_percent = 100.0 if action == "open" else 0.0
        started = time.monotonic()
        self._add_calibration_event(
            "speed",
            f"Timed full {action} speed measurement started",
            action=action,
            attempt=attempt,
            start_percent=start_status.position if start_status.position is not None else start_percent,
            state=start_status.state,
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        movement_started_at: float | None = None
        movement_start_deadline = started + POSITION_SIMULATION_START_GRACE_SECONDS

        def build_result(
            status: NiceBidiStatus,
            finished: float,
            *,
            endpoint_confirmed_after_stopped: bool = False,
            endpoint_inferred_from_stopped: bool = False,
            stopped_duration_ratio: float | None = None,
        ) -> dict[str, Any]:
            measured_from = movement_started_at if movement_started_at is not None else started
            duration_ms = max(1, round((finished - measured_from) * 1000))
            duration_seconds = duration_ms / 1000
            speed_percent_per_second = 100.0 / duration_seconds
            result: dict[str, Any] = {
                "action": action,
                "mode": "time",
                "start_percent": start_percent,
                "end_percent": end_percent,
                "end_state": status.state,
                "duration_ms": duration_ms,
                "movement_start_delay_ms": (
                    round((movement_started_at - started) * 1000)
                    if movement_started_at is not None
                    else None
                ),
                "distance_percent": abs(end_percent - start_percent),
                "speed_percent_per_second": round(speed_percent_per_second, 2),
            }
            if attempt is not None:
                result["attempt"] = attempt
            if endpoint_confirmed_after_stopped:
                result["endpoint_confirmed_after_stopped"] = True
            if endpoint_inferred_from_stopped:
                result["endpoint_inferred_from_stopped"] = True
            if stopped_duration_ratio is not None:
                result["stopped_duration_ratio"] = round(stopped_duration_ratio, 2)
            return result

        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if status.state == moving_state and movement_started_at is None:
                movement_started_at = time.monotonic()
            if self._is_at_endpoint(status, action):
                finished = time.monotonic()
                result = build_result(status, finished)
                self._add_calibration_event(
                    "speed",
                    f"Timed full {action} speed measurement completed",
                    **result,
                )
                await self._async_pause_before_next_calibration_command()
                return result
            if status.state == STATE_STOPPED and (
                movement_started_at is not None or time.monotonic() > movement_start_deadline
            ):
                stopped_at = time.monotonic()
                measured_from = movement_started_at if movement_started_at is not None else started
                duration_ms = max(1, round((stopped_at - measured_from) * 1000))
                duration_ratio = (
                    duration_ms / expected_duration_ms
                    if isinstance(expected_duration_ms, (int, float)) and expected_duration_ms > 0
                    else None
                )
                self._add_calibration_event(
                    "speed",
                    f"Timed full {action} reported stopped before endpoint confirmation",
                    action=action,
                    attempt=attempt,
                    current_percent=status.position,
                    state=status.state,
                    duration_ms=duration_ms,
                    expected_duration_ms=expected_duration_ms,
                    stopped_duration_ratio=round(duration_ratio, 2) if duration_ratio is not None else None,
                )
                confirmed = await self._async_wait_for_endpoint_after_stopped(action)
                if confirmed is not None:
                    result = build_result(
                        confirmed,
                        stopped_at,
                        endpoint_confirmed_after_stopped=True,
                        stopped_duration_ratio=duration_ratio,
                    )
                    self._add_calibration_event(
                        "speed",
                        f"Timed full {action} speed measurement completed after stopped confirmation",
                        **result,
                    )
                    await self._async_pause_before_next_calibration_command()
                    return result
                if (
                    duration_ratio is not None
                    and duration_ratio >= CALIBRATION_STOPPED_ENDPOINT_MIN_DURATION_RATIO
                ):
                    result = build_result(
                        status,
                        stopped_at,
                        endpoint_inferred_from_stopped=True,
                        stopped_duration_ratio=duration_ratio,
                    )
                    self._add_calibration_event(
                        "speed",
                        f"Timed full {action} speed measurement accepted stopped endpoint",
                        **result,
                    )
                    await self._async_pause_before_next_calibration_command()
                    return result
                raise HomeAssistantError(f"Position calibration stopped during full {action}")

        raise HomeAssistantError(f"Timed out measuring full {action} speed")

    @staticmethod
    def _median_time_travel_duration_ms(samples: list[dict[str, Any]]) -> int | None:
        """Return the median duration from time-based full-travel samples."""
        durations = [
            sample["duration_ms"]
            for sample in samples
            if isinstance(sample.get("duration_ms"), int) and sample["duration_ms"] > 0
        ]
        if not durations:
            return None
        sorted_durations = sorted(durations)
        return sorted_durations[len(sorted_durations) // 2]

    @staticmethod
    def _select_time_travel_sample(action: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
        """Select the median duration sample for one time-calibrated direction."""
        valid_samples = [
            sample
            for sample in samples
            if isinstance(sample.get("duration_ms"), int) and sample["duration_ms"] > 0
        ]
        if not valid_samples:
            raise HomeAssistantError(f"Position calibration produced no timed {action} samples")

        selected = sorted(valid_samples, key=lambda sample: sample["duration_ms"])[len(valid_samples) // 2]
        result = dict(selected)
        result.update(
            {
                "selection_strategy": "median_duration",
                "selected_attempt": selected.get("attempt"),
                "measurement_count": len(valid_samples),
                "duration_samples_ms": [sample["duration_ms"] for sample in valid_samples],
                "speed_samples_percent_per_second": [
                    sample.get("speed_percent_per_second") for sample in valid_samples
                ],
                "samples": valid_samples,
            }
        )
        return result

    async def _async_move_to_end_with_position_source(
        self,
        action: str,
        source: _CalibrationPositionSource,
    ) -> NiceBidiStatus:
        """Move fully open or closed while tracking the active position source."""
        status = await self._async_read_motion_status()
        self._update_calibration_position_source(source, status)
        current_percent = self._calibration_percent_for_status(status, source)
        if self._is_at_endpoint(status, action):
            self._add_calibration_event(
                "endpoint",
                f"Already at {action} endpoint",
                action=action,
                current_percent=current_percent,
                state=status.state,
                mode=source.mode,
            )
            return status

        self._add_calibration_event(
            "endpoint",
            f"Moving to {action} endpoint",
            action=action,
            current_percent=current_percent,
            state=status.state,
            mode=source.mode,
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        started = time.monotonic()
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + POSITION_SIMULATION_START_GRACE_SECONDS
        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            self._update_calibration_position_source(source, status)
            current_percent = self._calibration_percent_for_status(status, source)
            if self._is_at_endpoint(status, action):
                self._add_calibration_event(
                    "endpoint",
                    f"Reached {action} endpoint",
                    action=action,
                    current_percent=current_percent,
                    state=status.state,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    mode=source.mode,
                )
                await self._async_pause_before_next_calibration_command()
                return status
            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                stopped_at = time.monotonic()
                self._add_calibration_event(
                    "endpoint",
                    f"{action} endpoint reported stopped before endpoint confirmation",
                    action=action,
                    current_percent=current_percent,
                    state=status.state,
                    duration_ms=round((stopped_at - started) * 1000),
                    mode=source.mode,
                )
                confirmed = await self._async_wait_for_endpoint_after_stopped(action)
                if confirmed is not None:
                    self._update_calibration_position_source(source, confirmed)
                    self._add_calibration_event(
                        "endpoint",
                        f"Reached {action} endpoint after stopped confirmation",
                        action=action,
                        current_percent=self._calibration_percent_for_status(confirmed, source),
                        state=confirmed.state,
                        duration_ms=round((stopped_at - started) * 1000),
                        mode=source.mode,
                    )
                    await self._async_pause_before_next_calibration_command()
                    return confirmed
                raise HomeAssistantError(f"Position calibration stopped before reaching {action} endpoint")

        raise HomeAssistantError(f"Timed out moving to {action} endpoint during position calibration")

    async def _async_measure_full_travel_with_position_source(
        self,
        action: str,
        source: _CalibrationPositionSource,
        *,
        attempt: int | None = None,
        expected_duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """Measure full travel and discover live position sources while moving."""
        start_status = await self._async_read_motion_status()
        self._update_calibration_position_source(source, start_status)
        if self._is_at_endpoint(start_status, action):
            raise HomeAssistantError(f"Nice gate is already at {action} endpoint")

        start_raw = self._calibration_raw_for_status(start_status, source)
        first_raw = start_raw
        last_raw = start_raw
        start_percent = self._calibration_percent_for_status(start_status, source)
        if start_percent is None:
            start_percent = 0.0 if action == "open" else 100.0
        end_percent = 100.0 if action == "open" else 0.0
        started = time.monotonic()
        self._add_calibration_event(
            "speed",
            f"Standardized full {action} speed measurement started",
            action=action,
            attempt=attempt,
            mode=source.mode,
            start_raw=start_raw,
            start_percent=start_percent,
            state=start_status.state,
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        movement_started_at: float | None = None
        movement_start_deadline = started + POSITION_SIMULATION_START_GRACE_SECONDS

        def build_result(
            status: NiceBidiStatus,
            finished: float,
            *,
            endpoint_confirmed_after_stopped: bool = False,
            endpoint_inferred_from_stopped: bool = False,
            stopped_duration_ratio: float | None = None,
        ) -> dict[str, Any]:
            measured_from = movement_started_at if movement_started_at is not None else started
            duration_ms = max(1, round((finished - measured_from) * 1000))
            duration_seconds = duration_ms / 1000
            final_raw = self._calibration_raw_for_status(status, source)
            self._learn_live_scalar_bounds_from_travel(
                source,
                action,
                first_raw,
                final_raw if final_raw is not None else last_raw,
            )
            final_percent = self._calibration_percent_for_status(status, source)
            if final_percent is None:
                final_percent = end_percent
            distance_percent = final_percent - start_percent
            speed_percent_per_second = abs(distance_percent) / duration_seconds
            result: dict[str, Any] = {
                "action": action,
                "mode": source.mode,
                "start_raw": start_raw,
                "start_percent": round(start_percent, 2),
                "end_raw": final_raw,
                "end_percent": round(final_percent, 2),
                "end_state": status.state,
                "duration_ms": duration_ms,
                "movement_start_delay_ms": (
                    round((movement_started_at - started) * 1000)
                    if movement_started_at is not None
                    else None
                ),
                "distance_percent": round(distance_percent, 2),
                "speed_percent_per_second": round(speed_percent_per_second, 2),
            }
            if attempt is not None:
                result["attempt"] = attempt
            if endpoint_confirmed_after_stopped:
                result["endpoint_confirmed_after_stopped"] = True
            if endpoint_inferred_from_stopped:
                result["endpoint_inferred_from_stopped"] = True
            if stopped_duration_ratio is not None:
                result["stopped_duration_ratio"] = round(stopped_duration_ratio, 2)
            return result

        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            self._update_calibration_position_source(source, status)
            current_raw = self._calibration_raw_for_status(status, source)
            if current_raw is not None:
                if first_raw is None:
                    first_raw = current_raw
                last_raw = current_raw
            if status.state == moving_state and movement_started_at is None:
                movement_started_at = time.monotonic()
            if self._is_at_endpoint(status, action):
                finished = time.monotonic()
                result = build_result(status, finished)
                self._add_calibration_event(
                    "speed",
                    f"Standardized full {action} speed measurement completed",
                    **result,
                )
                await self._async_pause_before_next_calibration_command()
                return result
            if status.state == STATE_STOPPED and (
                movement_started_at is not None or time.monotonic() > movement_start_deadline
            ):
                stopped_at = time.monotonic()
                measured_from = movement_started_at if movement_started_at is not None else started
                duration_ms = max(1, round((stopped_at - measured_from) * 1000))
                duration_ratio = (
                    duration_ms / expected_duration_ms
                    if isinstance(expected_duration_ms, (int, float)) and expected_duration_ms > 0
                    else None
                )
                self._add_calibration_event(
                    "speed",
                    f"Standardized full {action} reported stopped before endpoint confirmation",
                    action=action,
                    attempt=attempt,
                    current_percent=self._calibration_percent_for_status(status, source),
                    state=status.state,
                    duration_ms=duration_ms,
                    expected_duration_ms=expected_duration_ms,
                    stopped_duration_ratio=round(duration_ratio, 2) if duration_ratio is not None else None,
                    mode=source.mode,
                )
                confirmed = await self._async_wait_for_endpoint_after_stopped(action)
                if confirmed is not None:
                    self._update_calibration_position_source(source, confirmed)
                    result = build_result(
                        confirmed,
                        stopped_at,
                        endpoint_confirmed_after_stopped=True,
                        stopped_duration_ratio=duration_ratio,
                    )
                    self._add_calibration_event(
                        "speed",
                        f"Standardized full {action} speed measurement completed after stopped confirmation",
                        **result,
                    )
                    await self._async_pause_before_next_calibration_command()
                    return result
                if (
                    source.mode == CALIBRATION_MODE_TIME
                    and duration_ratio is not None
                    and duration_ratio >= CALIBRATION_STOPPED_ENDPOINT_MIN_DURATION_RATIO
                ):
                    result = build_result(
                        status,
                        stopped_at,
                        endpoint_inferred_from_stopped=True,
                        stopped_duration_ratio=duration_ratio,
                    )
                    self._add_calibration_event(
                        "speed",
                        f"Standardized full {action} speed measurement accepted stopped endpoint",
                        **result,
                    )
                    await self._async_pause_before_next_calibration_command()
                    return result
                raise HomeAssistantError(f"Position calibration stopped during full {action}")

        raise HomeAssistantError(f"Timed out measuring full {action} speed")

    async def _async_calibrate_target_from_endpoint_with_position_source(
        self,
        target: int,
        action: str,
        endpoint_action: str,
        source: _CalibrationPositionSource,
    ) -> dict[str, Any]:
        """Calibrate one target using a non-encoder position source."""
        attempts = []
        stop_percent: float | None = None

        for attempt in range(1, CALIBRATION_MAX_ATTEMPTS + 1):
            self._add_calibration_event(
                "attempt",
                f"{action} {target}% attempt {attempt}: moving to known {endpoint_action} endpoint",
                action=action,
                target_percent=target,
                attempt=attempt,
                endpoint_action=endpoint_action,
                requested_stop_percent=stop_percent,
                mode=source.mode,
            )
            await self._async_move_to_end_with_position_source(endpoint_action, source)
            if stop_percent is None:
                stop_percent = float(target)
                self._add_calibration_event(
                    "attempt",
                    f"{action} {target}% attempt {attempt}: using nominal first stop threshold",
                    action=action,
                    target_percent=target,
                    attempt=attempt,
                    requested_stop_percent=round(stop_percent, 2),
                    mode=source.mode,
                )

            sample = await self._async_calibrate_target_with_position_source(
                target,
                action,
                endpoint_action,
                attempt,
                stop_percent,
                source,
            )
            attempts.append(sample)
            selection_so_far = self._select_calibration_sample(attempts)
            stop_percent = selection_so_far["sample"]["corrected_stop_percent"]
            attempt_successful = self._calibration_attempt_successful(sample)
            event_details = {
                "action": action,
                "target_percent": target,
                "attempt": attempt,
                "valid": self._calibration_attempt_valid(sample),
                "successful": attempt_successful,
                "stable_success": self._calibration_attempts_stable(attempts),
                "successful_attempts": self._calibration_success_count(attempts),
                "final_percent": sample["final_percent"],
                "error_percent": sample["error_percent"],
                "requested_stop_percent": sample["requested_stop_percent"],
                "corrected_stop_percent": sample["corrected_stop_percent"],
                "stop_command_latency_ms": sample["stop_command_latency_ms"],
                "move_duration_ms": sample["move_duration_ms"],
                "mode": source.mode,
            }
            failure_reason = sample.get("failure_reason")
            if failure_reason is not None:
                event_details["failure_reason"] = failure_reason
            if self._calibration_attempt_valid(sample):
                event_message = f"{action} {target}% attempt {attempt}: settled with {sample['error_percent']}% error"
            else:
                event_message = f"{action} {target}% attempt {attempt}: invalid after {failure_reason}"
            self._add_calibration_event("attempt", event_message, **event_details)

            await self._async_move_to_end_with_position_source(endpoint_action, source)

        selection = self._select_calibration_sample(attempts)
        selected_sample = selection["sample"]
        successful = selection["strategy"] == "stable_window"
        self._add_calibration_event(
            "target",
            f"{action} {target}% calibration finished",
            action=action,
            target_percent=target,
            successful=successful,
            successful_attempts=self._calibration_success_count(attempts),
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
            attempts_used=len(attempts),
            selection_strategy=selection["strategy"],
            selected_attempt=selection["selected_attempt"],
            selected_attempts=selection["selected_attempts"],
            selected_abs_error_percent=selection["selected_abs_error_percent"],
            ignored_outlier_attempts=selection["ignored_outlier_attempts"],
            ignored_invalid_attempts=selection["ignored_invalid_attempts"],
            final_error_percent=selected_sample["error_percent"],
            corrected_stop_percent=selected_sample["corrected_stop_percent"],
            mode=source.mode,
        )
        return {
            **selected_sample,
            "successful": successful,
            "successful_attempts": self._calibration_success_count(attempts),
            "stability_attempts": CALIBRATION_STABILITY_ATTEMPTS,
            "attempts_used": len(attempts),
            "selection_strategy": selection["strategy"],
            "selected_attempt": selection["selected_attempt"],
            "selected_attempts": selection["selected_attempts"],
            "selected_window_avg_abs_error_percent": selection["selected_window_avg_abs_error_percent"],
            "selected_abs_error_percent": selection["selected_abs_error_percent"],
            "ignored_outlier_attempts": selection["ignored_outlier_attempts"],
            "ignored_invalid_attempts": selection["ignored_invalid_attempts"],
            "outlier_error_percent": CALIBRATION_OUTLIER_ERROR_PERCENT,
            "last_attempt": attempts[-1],
            "attempts": attempts,
        }

    async def _async_calibrate_target_with_position_source(
        self,
        target: int,
        action: str,
        endpoint_action: str,
        attempt: int,
        requested_stop_percent: float,
        source: _CalibrationPositionSource,
    ) -> dict[str, Any]:
        """Measure one target approach using normalized percentages."""
        status = await self._async_read_motion_status()
        self._update_calibration_position_source(source, status)
        start_percent = self._calibration_percent_for_status(status, source)
        if start_percent is None:
            start_percent = 0.0 if endpoint_action == "close" else 100.0
        start_raw = self._calibration_raw_for_status(status, source)
        target_percent = float(target)
        if self._position_reached_for_calibration(action, start_percent, target_percent):
            raise HomeAssistantError(f"Position calibration already crossed {target}% while preparing to {action}")

        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: movement command sent",
            action=action,
            target_percent=target,
            attempt=attempt,
            mode=source.mode,
            start_raw=start_raw,
            start_percent=round(start_percent, 2),
            requested_stop_percent=round(requested_stop_percent, 2),
        )
        move_started = time.monotonic()
        await self._async_send_action(action, refresh=False, simulate=False)
        stop_command_percent: float | None = None
        stop_command_raw: int | None = None
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = move_started + POSITION_SIMULATION_START_GRACE_SECONDS

        while time.monotonic() - move_started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            self._update_calibration_position_source(source, status)
            current_percent = self._calibration_percent_for_status(status, source)
            current_raw = self._calibration_raw_for_status(status, source)
            if (
                current_percent is not None
                and self._position_reached_for_calibration(action, current_percent, requested_stop_percent)
            ):
                stop_command_percent = current_percent
                stop_command_raw = current_raw
                self._add_calibration_event(
                    "attempt",
                    f"{action} {target}% attempt {attempt}: stop threshold reached",
                    action=action,
                    target_percent=target,
                    attempt=attempt,
                    mode=source.mode,
                    current_raw=current_raw,
                    current_percent=round(current_percent, 2),
                    requested_stop_percent=round(requested_stop_percent, 2),
                    state=status.state,
                )
                await self._async_send_action("stop", refresh=False, simulate=False)
                break
            if status.state == moving_state:
                started_moving = True
            if status.state in {STATE_OPEN, STATE_CLOSED, STATE_STOPPED} and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                raise HomeAssistantError(f"Position calibration stopped before reaching {target}%")

        if stop_command_percent is None:
            raise HomeAssistantError(f"Timed out calibrating {target}% while moving {action}")

        settled, settle_timed_out = await self._async_wait_for_settle(
            action=action,
            target=target,
            attempt=attempt,
        )
        self._update_calibration_position_source(source, settled)
        final_percent = self._calibration_percent_for_status(settled, source)
        final_raw = self._calibration_raw_for_status(settled, source)

        if settle_timed_out or final_percent is None:
            return self._invalid_calibration_sample_with_position_source(
                action=action,
                endpoint_action=endpoint_action,
                attempt=attempt,
                target=target,
                source=source,
                start_raw=start_raw,
                start_percent=start_percent,
                requested_stop_percent=requested_stop_percent,
                stop_command_raw=stop_command_raw,
                stop_command_percent=stop_command_percent,
                final_raw=final_raw,
                final_percent=final_percent,
                move_started=move_started,
                failure_reason="settle_timeout" if settle_timed_out else "final_position_unavailable",
            )

        error_percent = round(final_percent - target_percent, 2)
        corrected_stop_percent = self._clamp_calibration_percent(stop_command_percent - error_percent)
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        travel_percent = stop_command_percent - start_percent
        speed_percent_per_second = (travel_percent / move_duration_ms) * 1000 if move_duration_ms > 0 else None
        target_raw = self._raw_for_percent_from_source(source, target_percent)
        requested_stop_raw = self._raw_for_percent_from_source(source, requested_stop_percent)
        error_raw = (
            final_raw - target_raw
            if final_raw is not None and target_raw is not None
            else None
        )
        corrected_stop_raw = self._raw_for_percent_from_source(source, corrected_stop_percent)
        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: settled position read",
            action=action,
            target_percent=target,
            attempt=attempt,
            mode=source.mode,
            final_raw=final_raw,
            final_percent=round(final_percent, 2),
            error_raw=error_raw,
            error_percent=error_percent,
            stop_command_raw=stop_command_raw,
            stop_command_percent=round(stop_command_percent, 2),
            corrected_stop_raw=corrected_stop_raw,
            corrected_stop_percent=round(corrected_stop_percent, 2),
            move_duration_ms=move_duration_ms,
            stop_command_latency_ms=self.last_command_latency_ms,
            speed_percent_per_second=round(speed_percent_per_second, 2) if speed_percent_per_second is not None else None,
        )

        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "mode": source.mode,
            "valid": True,
            "failure_reason": None,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(start_percent, 2),
            "target_raw": target_raw,
            "requested_stop_raw": requested_stop_raw,
            "requested_stop_percent": round(requested_stop_percent, 2),
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": round(stop_command_percent, 2),
            "corrected_stop_raw": corrected_stop_raw,
            "corrected_stop_percent": round(corrected_stop_percent, 2),
            "final_raw": final_raw,
            "final_percent": round(final_percent, 2),
            "error_raw": error_raw,
            "error_percent": error_percent,
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": None,
            "speed_percent_per_second": round(speed_percent_per_second, 2) if speed_percent_per_second is not None else None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    def _invalid_calibration_sample_with_position_source(
        self,
        *,
        action: str,
        endpoint_action: str,
        attempt: int,
        target: int,
        source: _CalibrationPositionSource,
        start_raw: int | None,
        start_percent: float,
        requested_stop_percent: float,
        stop_command_raw: int | None,
        stop_command_percent: float,
        final_raw: int | None,
        final_percent: float | None,
        move_started: float,
        failure_reason: str,
    ) -> dict[str, Any]:
        """Return an invalid non-encoder calibration attempt."""
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: marked invalid",
            action=action,
            target_percent=target,
            attempt=attempt,
            mode=source.mode,
            failure_reason=failure_reason,
            final_raw=final_raw,
            final_percent=final_percent,
            requested_stop_percent=round(requested_stop_percent, 2),
            stop_command_raw=stop_command_raw,
            stop_command_percent=round(stop_command_percent, 2),
            move_duration_ms=move_duration_ms,
            stop_command_latency_ms=self.last_command_latency_ms,
        )
        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "mode": source.mode,
            "valid": False,
            "failure_reason": failure_reason,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(start_percent, 2),
            "target_raw": self._raw_for_percent_from_source(source, float(target)),
            "requested_stop_raw": self._raw_for_percent_from_source(source, requested_stop_percent),
            "requested_stop_percent": round(requested_stop_percent, 2),
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": round(stop_command_percent, 2),
            "corrected_stop_raw": self._raw_for_percent_from_source(source, requested_stop_percent),
            "corrected_stop_percent": round(requested_stop_percent, 2),
            "final_raw": final_raw,
            "final_percent": round(final_percent, 2) if final_percent is not None else None,
            "error_raw": None,
            "error_percent": None,
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": None,
            "speed_percent_per_second": None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    async def _async_wait_for_endpoint_after_stopped(self, action: str) -> NiceBidiStatus | None:
        """Wait briefly for an endpoint update after a stopped report."""
        started = time.monotonic()
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        while time.monotonic() - started < CALIBRATION_STOPPED_ENDPOINT_GRACE_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                return status
            if status.state == moving_state:
                return None
        return None

    async def _async_calibrate_target_from_endpoint(
        self,
        target: int,
        action: str,
        endpoint_action: str,
    ) -> dict[str, Any]:
        """Calibrate one target by retrying from a known endpoint."""
        attempts = []
        stop_raw: int | None = None

        for attempt in range(1, CALIBRATION_MAX_ATTEMPTS + 1):
            self._add_calibration_event(
                "attempt",
                f"{action} {target}% attempt {attempt}: moving to known {endpoint_action} endpoint",
                action=action,
                target_percent=target,
                attempt=attempt,
                endpoint_action=endpoint_action,
                requested_stop_raw=stop_raw,
            )
            endpoint_status = await self._async_move_to_end(endpoint_action)
            if stop_raw is None:
                stop_raw = self._raw_for_percent(endpoint_status, target)
                self._add_calibration_event(
                    "attempt",
                    f"{action} {target}% attempt {attempt}: using nominal first stop threshold",
                    action=action,
                    target_percent=target,
                    attempt=attempt,
                    requested_stop_raw=stop_raw,
                    requested_stop_percent=round(self._percent_for_raw(endpoint_status, stop_raw), 2),
                )

            sample = await self._async_calibrate_target(target, action, endpoint_action, attempt, stop_raw)
            attempts.append(sample)
            selection_so_far = self._select_calibration_sample(attempts)
            stop_raw = selection_so_far["sample"]["corrected_stop_raw"]
            attempt_successful = self._calibration_attempt_successful(sample)
            event_details = {
                "action": action,
                "target_percent": target,
                "attempt": attempt,
                "valid": self._calibration_attempt_valid(sample),
                "successful": attempt_successful,
                "stable_success": self._calibration_attempts_stable(attempts),
                "successful_attempts": self._calibration_success_count(attempts),
                "final_percent": sample["final_percent"],
                "error_percent": sample["error_percent"],
                "requested_stop_percent": sample["requested_stop_percent"],
                "corrected_stop_percent": sample["corrected_stop_percent"],
                "stop_command_latency_ms": sample["stop_command_latency_ms"],
                "move_duration_ms": sample["move_duration_ms"],
            }
            failure_reason = sample.get("failure_reason")
            if failure_reason is not None:
                event_details["failure_reason"] = failure_reason
            if self._calibration_attempt_valid(sample):
                event_message = f"{action} {target}% attempt {attempt}: settled with {sample['error_percent']}% error"
            else:
                event_message = f"{action} {target}% attempt {attempt}: invalid after {failure_reason}"
            self._add_calibration_event("attempt", event_message, **event_details)

            await self._async_move_to_end(endpoint_action)

        selection = self._select_calibration_sample(attempts)
        selected_sample = selection["sample"]
        successful = selection["strategy"] == "stable_window"
        self._add_calibration_event(
            "target",
            f"{action} {target}% calibration finished",
            action=action,
            target_percent=target,
            successful=successful,
            successful_attempts=self._calibration_success_count(attempts),
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
            attempts_used=len(attempts),
            selection_strategy=selection["strategy"],
            selected_attempt=selection["selected_attempt"],
            selected_attempts=selection["selected_attempts"],
            selected_abs_error_percent=selection["selected_abs_error_percent"],
            ignored_outlier_attempts=selection["ignored_outlier_attempts"],
            ignored_invalid_attempts=selection["ignored_invalid_attempts"],
            final_error_percent=selected_sample["error_percent"],
            corrected_stop_percent=selected_sample["corrected_stop_percent"],
        )
        return {
            **selected_sample,
            "successful": successful,
            "successful_attempts": self._calibration_success_count(attempts),
            "stability_attempts": CALIBRATION_STABILITY_ATTEMPTS,
            "attempts_used": len(attempts),
            "selection_strategy": selection["strategy"],
            "selected_attempt": selection["selected_attempt"],
            "selected_attempts": selection["selected_attempts"],
            "selected_window_avg_abs_error_percent": selection["selected_window_avg_abs_error_percent"],
            "selected_abs_error_percent": selection["selected_abs_error_percent"],
            "ignored_outlier_attempts": selection["ignored_outlier_attempts"],
            "ignored_invalid_attempts": selection["ignored_invalid_attempts"],
            "outlier_error_percent": CALIBRATION_OUTLIER_ERROR_PERCENT,
            "last_attempt": attempts[-1],
            "attempts": attempts,
        }

    @staticmethod
    def _calibration_attempt_valid(attempt: dict[str, Any]) -> bool:
        """Return true if an attempt should be used for learned stop thresholds."""
        return attempt.get("valid", True) is not False

    @staticmethod
    def _calibration_attempt_abs_error(attempt: dict[str, Any]) -> float:
        """Return an attempt's absolute percentage error."""
        if not NiceBidiCalibrationMixin._calibration_attempt_valid(attempt):
            return 1000.0
        try:
            return abs(float(attempt.get("error_percent", 1000.0)))
        except (TypeError, ValueError):
            return 1000.0

    @staticmethod
    def _calibration_attempt_successful(attempt: dict[str, Any]) -> bool:
        """Return true if an attempt finished inside the target tolerance."""
        return (
            NiceBidiCalibrationMixin._calibration_attempt_valid(attempt)
            and NiceBidiCalibrationMixin._calibration_attempt_abs_error(attempt)
            <= CALIBRATION_TARGET_TOLERANCE_PERCENT
        )

    @staticmethod
    def _calibration_success_count(attempts: list[dict[str, Any]]) -> int:
        """Return how many attempts finished inside the calibration tolerance."""
        return sum(
            1
            for attempt in attempts
            if NiceBidiCalibrationMixin._calibration_attempt_successful(attempt)
        )

    @staticmethod
    def _calibration_attempts_stable(attempts: list[dict[str, Any]]) -> bool:
        """Return true when any consecutive attempts show repeatable accuracy."""
        if len(attempts) < CALIBRATION_STABILITY_ATTEMPTS:
            return False
        for start in range(0, len(attempts) - CALIBRATION_STABILITY_ATTEMPTS + 1):
            window = attempts[start : start + CALIBRATION_STABILITY_ATTEMPTS]
            if (
                NiceBidiCalibrationMixin._calibration_success_count(window)
                == CALIBRATION_STABILITY_ATTEMPTS
            ):
                return True
        return False

    @staticmethod
    def _select_calibration_sample(attempts: list[dict[str, Any]]) -> dict[str, Any]:
        """Choose the calibration result that should be stored for one target."""
        if not attempts:
            raise HomeAssistantError("Position calibration produced no attempts")

        ignored_invalid_attempts = [
            int(attempt["attempt"])
            for attempt in attempts
            if not NiceBidiCalibrationMixin._calibration_attempt_valid(attempt)
            and isinstance(attempt.get("attempt"), int)
        ]
        valid_attempts = [
            attempt
            for attempt in attempts
            if NiceBidiCalibrationMixin._calibration_attempt_valid(attempt)
        ]
        ignored_outliers = [
            int(attempt["attempt"])
            for attempt in valid_attempts
            if NiceBidiCalibrationMixin._calibration_attempt_abs_error(attempt)
            > CALIBRATION_OUTLIER_ERROR_PERCENT
            and isinstance(attempt.get("attempt"), int)
        ]
        stable_windows: list[tuple[float, int, list[dict[str, Any]]]] = []
        for start in range(0, len(attempts) - CALIBRATION_STABILITY_ATTEMPTS + 1):
            window = attempts[start : start + CALIBRATION_STABILITY_ATTEMPTS]
            if (
                NiceBidiCalibrationMixin._calibration_success_count(window)
                != CALIBRATION_STABILITY_ATTEMPTS
            ):
                continue
            avg_abs_error = sum(
                NiceBidiCalibrationMixin._calibration_attempt_abs_error(attempt)
                for attempt in window
            ) / len(window)
            stable_windows.append((avg_abs_error, start, window))

        if stable_windows:
            avg_abs_error, _, selected_window = min(
                stable_windows,
                key=lambda item: (item[0], -item[1]),
            )
            selected_sample = min(
                selected_window,
                key=NiceBidiCalibrationMixin._calibration_attempt_abs_error,
            )
            return {
                "sample": selected_sample,
                "strategy": "stable_window",
                "selected_attempt": selected_sample.get("attempt"),
                "selected_attempts": [attempt.get("attempt") for attempt in selected_window],
                "selected_window_avg_abs_error_percent": round(avg_abs_error, 2),
                "selected_abs_error_percent": round(
                    NiceBidiCalibrationMixin._calibration_attempt_abs_error(selected_sample), 2
                ),
                "ignored_outlier_attempts": ignored_outliers,
                "ignored_invalid_attempts": ignored_invalid_attempts,
            }

        non_outlier_attempts = [
            attempt
            for attempt in valid_attempts
            if NiceBidiCalibrationMixin._calibration_attempt_abs_error(attempt)
            <= CALIBRATION_OUTLIER_ERROR_PERCENT
        ]
        candidates = non_outlier_attempts or valid_attempts
        if not candidates:
            selected_sample = attempts[-1]
            return {
                "sample": selected_sample,
                "strategy": "no_valid_attempt",
                "selected_attempt": selected_sample.get("attempt"),
                "selected_attempts": [],
                "selected_window_avg_abs_error_percent": None,
                "selected_abs_error_percent": None,
                "ignored_outlier_attempts": ignored_outliers,
                "ignored_invalid_attempts": ignored_invalid_attempts,
            }
        selected_sample = min(
            candidates,
            key=NiceBidiCalibrationMixin._calibration_attempt_abs_error,
        )
        return {
            "sample": selected_sample,
            "strategy": "best_non_outlier_attempt" if non_outlier_attempts else "best_attempt",
            "selected_attempt": selected_sample.get("attempt"),
            "selected_attempts": [selected_sample.get("attempt")],
            "selected_window_avg_abs_error_percent": None,
            "selected_abs_error_percent": round(
                NiceBidiCalibrationMixin._calibration_attempt_abs_error(selected_sample), 2
            ),
            "ignored_outlier_attempts": ignored_outliers,
            "ignored_invalid_attempts": ignored_invalid_attempts,
        }

    async def _async_calibrate_target(
        self,
        target: int,
        action: str,
        endpoint_action: str,
        attempt: int,
        requested_stop_raw: int,
    ) -> dict[str, Any]:
        """Measure one target approach and return its corrected stop threshold."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if status.current_position is None:
            raise HomeAssistantError("Nice current encoder position is not available")

        start_raw = status.current_position
        target_raw = self._raw_for_percent(status, target)
        if self._raw_reached(action, status, start_raw, target_raw):
            raise HomeAssistantError(f"Position calibration already crossed {target}% while preparing to {action}")

        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: movement command sent",
            action=action,
            target_percent=target,
            attempt=attempt,
            start_raw=start_raw,
            start_percent=round(self._percent_for_raw(status, start_raw), 2),
            target_raw=target_raw,
            requested_stop_raw=requested_stop_raw,
            requested_stop_percent=round(self._percent_for_raw(status, requested_stop_raw), 2),
        )
        move_started = time.monotonic()
        await self._async_send_action(action, refresh=False, simulate=False)
        stop_command_raw: int | None = None
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = move_started + 8.0

        while time.monotonic() - move_started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            current_raw = status.current_position
            if current_raw is not None and self._raw_reached(action, status, current_raw, requested_stop_raw):
                stop_command_raw = current_raw
                self._add_calibration_event(
                    "attempt",
                    f"{action} {target}% attempt {attempt}: stop threshold reached",
                    action=action,
                    target_percent=target,
                    attempt=attempt,
                    current_raw=current_raw,
                    current_percent=round(self._percent_for_raw(status, current_raw), 2),
                    requested_stop_raw=requested_stop_raw,
                    requested_stop_percent=round(self._percent_for_raw(status, requested_stop_raw), 2),
                    state=status.state,
                )
                await self._async_send_action("stop", refresh=False, simulate=False)
                break
            if status.state == moving_state:
                started_moving = True
            if status.state in {STATE_OPEN, STATE_CLOSED, STATE_STOPPED} and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                raise HomeAssistantError(f"Position calibration stopped before reaching {target}%")

        if stop_command_raw is None:
            raise HomeAssistantError(f"Timed out calibrating {target}% while moving {action}")

        settled, settle_timed_out = await self._async_wait_for_settle(
            action=action,
            target=target,
            attempt=attempt,
        )
        if settled.current_position is None:
            raise HomeAssistantError("Nice final encoder position is not available")

        if settle_timed_out:
            return self._invalid_calibration_sample(
                action=action,
                endpoint_action=endpoint_action,
                attempt=attempt,
                target=target,
                status=status,
                settled=settled,
                start_raw=start_raw,
                target_raw=target_raw,
                requested_stop_raw=requested_stop_raw,
                stop_command_raw=stop_command_raw,
                move_started=move_started,
                failure_reason="settle_timeout",
            )

        final_raw = settled.current_position
        error_raw = final_raw - target_raw
        corrected_stop_raw = self._clamp_raw(status, stop_command_raw - error_raw)
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        travel_raw = stop_command_raw - start_raw
        speed_raw_per_second = (travel_raw / move_duration_ms) * 1000 if move_duration_ms > 0 else None
        final_percent = round(self._percent_for_raw(settled, final_raw), 2)
        error_percent = round(final_percent - target, 2)
        stop_command_percent = round(self._percent_for_raw(status, stop_command_raw), 2)
        corrected_stop_percent = round(self._percent_for_raw(status, corrected_stop_raw), 2)
        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: settled position read",
            action=action,
            target_percent=target,
            attempt=attempt,
            final_raw=final_raw,
            final_percent=final_percent,
            error_raw=error_raw,
            error_percent=error_percent,
            stop_command_raw=stop_command_raw,
            stop_command_percent=stop_command_percent,
            corrected_stop_raw=corrected_stop_raw,
            corrected_stop_percent=corrected_stop_percent,
            move_duration_ms=move_duration_ms,
            stop_command_latency_ms=self.last_command_latency_ms,
            speed_raw_per_second=round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
        )

        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "valid": True,
            "failure_reason": None,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(self._percent_for_raw(status, start_raw), 2),
            "target_raw": target_raw,
            "requested_stop_raw": requested_stop_raw,
            "requested_stop_percent": round(self._percent_for_raw(status, requested_stop_raw), 2),
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": stop_command_percent,
            "corrected_stop_raw": corrected_stop_raw,
            "corrected_stop_percent": corrected_stop_percent,
            "final_raw": final_raw,
            "final_percent": final_percent,
            "error_raw": error_raw,
            "error_percent": error_percent,
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    def _invalid_calibration_sample(
        self,
        *,
        action: str,
        endpoint_action: str,
        attempt: int,
        target: int,
        status: NiceBidiStatus,
        settled: NiceBidiStatus,
        start_raw: int,
        target_raw: int,
        requested_stop_raw: int,
        stop_command_raw: int,
        move_started: float,
        failure_reason: str,
    ) -> dict[str, Any]:
        """Return an invalid calibration attempt that is excluded from learning."""
        final_raw = settled.current_position
        move_duration_ms = round((time.monotonic() - move_started) * 1000)
        travel_raw = stop_command_raw - start_raw
        speed_raw_per_second = (travel_raw / move_duration_ms) * 1000 if move_duration_ms > 0 else None
        final_percent = round(self._percent_for_raw(settled, final_raw), 2) if final_raw is not None else None
        stop_command_percent = round(self._percent_for_raw(status, stop_command_raw), 2)
        requested_stop_percent = round(self._percent_for_raw(status, requested_stop_raw), 2)
        self._add_calibration_event(
            "attempt",
            f"{action} {target}% attempt {attempt}: marked invalid",
            action=action,
            target_percent=target,
            attempt=attempt,
            failure_reason=failure_reason,
            final_raw=final_raw,
            final_percent=final_percent,
            requested_stop_raw=requested_stop_raw,
            requested_stop_percent=requested_stop_percent,
            stop_command_raw=stop_command_raw,
            stop_command_percent=stop_command_percent,
            move_duration_ms=move_duration_ms,
            stop_command_latency_ms=self.last_command_latency_ms,
            speed_raw_per_second=round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            state=settled.state,
        )
        return {
            "action": action,
            "endpoint_action": endpoint_action,
            "valid": False,
            "failure_reason": failure_reason,
            "attempt": attempt,
            "target_percent": target,
            "start_raw": start_raw,
            "start_percent": round(self._percent_for_raw(status, start_raw), 2),
            "target_raw": target_raw,
            "requested_stop_raw": requested_stop_raw,
            "requested_stop_percent": requested_stop_percent,
            "stop_command_raw": stop_command_raw,
            "stop_command_percent": stop_command_percent,
            "corrected_stop_raw": requested_stop_raw,
            "corrected_stop_percent": requested_stop_percent,
            "final_raw": final_raw,
            "final_percent": final_percent,
            "error_raw": None,
            "error_percent": None,
            "move_duration_ms": move_duration_ms,
            "speed_raw_per_second": round(speed_raw_per_second, 2) if speed_raw_per_second is not None else None,
            "stop_command_latency_ms": self.last_command_latency_ms,
        }

    async def _async_measure_full_travel(self, action: str) -> dict[str, Any]:
        """Measure one full endpoint-to-endpoint movement for display speed."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if status.current_position is None or status.position is None:
            raise HomeAssistantError("Nice current position is not available")

        start_raw = status.current_position
        start_percent = status.position
        started = time.monotonic()
        self._add_calibration_event(
            "speed",
            f"Full {action} speed measurement started",
            action=action,
            start_raw=start_raw,
            start_percent=start_percent,
            state=status.state,
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + POSITION_SIMULATION_START_GRACE_SECONDS

        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                if status.current_position is None or status.position is None:
                    raise HomeAssistantError("Nice endpoint position is not available")
                duration_ms = round((time.monotonic() - started) * 1000)
                distance_raw = status.current_position - start_raw
                distance_percent = status.position - start_percent
                duration_seconds = duration_ms / 1000
                speed_raw_per_second = (
                    distance_raw / duration_seconds if duration_seconds > 0 else None
                )
                speed_percent_per_second = (
                    abs(distance_percent) / duration_seconds if duration_seconds > 0 else None
                )
                result = {
                    "action": action,
                    "start_raw": start_raw,
                    "start_percent": round(start_percent, 2),
                    "end_raw": status.current_position,
                    "end_percent": round(status.position, 2),
                    "end_state": status.state,
                    "end_closed_raw": status.closed_position,
                    "end_open_raw": status.open_position,
                    "distance_raw": distance_raw,
                    "distance_percent": round(distance_percent, 2),
                    "duration_ms": duration_ms,
                    "speed_raw_per_second": (
                        round(speed_raw_per_second, 2)
                        if speed_raw_per_second is not None
                        else None
                    ),
                    "speed_percent_per_second": (
                        round(speed_percent_per_second, 2)
                        if speed_percent_per_second is not None
                        else None
                    ),
                }
                self._add_calibration_event(
                    "speed",
                    f"Full {action} speed measurement completed",
                    **result,
                )
                await self._async_pause_before_next_calibration_command()
                return result

            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                raise HomeAssistantError(f"Position calibration stopped during full {action}")

        raise HomeAssistantError(f"Timed out measuring full {action} speed")

    async def _async_move_to_end(self, action: str) -> NiceBidiStatus:
        """Move fully open or closed for a known calibration starting point."""
        status = await self._async_read_motion_status()
        self._validate_position_bounds(status)
        if self._is_at_endpoint(status, action):
            self._add_calibration_event(
                "endpoint",
                f"Already at {action} endpoint",
                action=action,
                current_raw=status.current_position,
                current_percent=status.position,
                state=status.state,
            )
            return status

        self._add_calibration_event(
            "endpoint",
            f"Moving to {action} endpoint",
            action=action,
            current_raw=status.current_position,
            current_percent=status.position,
            state=status.state,
        )
        await self._async_send_action(action, refresh=False, simulate=False)
        started = time.monotonic()
        moving_state = STATE_OPENING if action == "open" else STATE_CLOSING
        started_moving = False
        movement_start_deadline = started + 8.0
        while time.monotonic() - started < CALIBRATION_MOVEMENT_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
            if self._is_at_endpoint(status, action):
                self._add_calibration_event(
                    "endpoint",
                    f"Reached {action} endpoint",
                    action=action,
                    current_raw=status.current_position,
                    current_percent=status.position,
                    state=status.state,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                await self._async_pause_before_next_calibration_command()
                return status
            if status.state == moving_state:
                started_moving = True
            if status.state == STATE_STOPPED and (
                started_moving or time.monotonic() > movement_start_deadline
            ):
                stopped_at = time.monotonic()
                self._add_calibration_event(
                    "endpoint",
                    f"{action} endpoint reported stopped before endpoint confirmation",
                    action=action,
                    current_raw=status.current_position,
                    current_percent=status.position,
                    state=status.state,
                    duration_ms=round((stopped_at - started) * 1000),
                )
                confirmed = await self._async_wait_for_endpoint_after_stopped(action)
                if confirmed is not None:
                    self._add_calibration_event(
                        "endpoint",
                        f"Reached {action} endpoint after stopped confirmation",
                        action=action,
                        current_raw=confirmed.current_position,
                        current_percent=confirmed.position,
                        state=confirmed.state,
                        duration_ms=round((stopped_at - started) * 1000),
                    )
                    await self._async_pause_before_next_calibration_command()
                    return confirmed
                raise HomeAssistantError(
                    f"Position calibration stopped before reaching {action} endpoint"
                )

        raise HomeAssistantError(f"Timed out moving to {action} endpoint during position calibration")

    async def _async_wait_for_settle(
        self,
        *,
        action: str,
        target: int,
        attempt: int,
    ) -> tuple[NiceBidiStatus, bool]:
        """Wait after a stop command and return whether settling timed out."""
        await asyncio.sleep(CALIBRATION_SETTLE_SECONDS)
        status = await self._async_read_motion_status()
        started = time.monotonic()
        while status.is_moving and time.monotonic() - started < CALIBRATION_SETTLE_TIMEOUT_SECONDS:
            await asyncio.sleep(POSITION_TARGET_POLL_SECONDS)
            status = await self._async_read_motion_status()
        settle_timed_out = status.is_moving
        if settle_timed_out:
            self._add_calibration_event(
                "attempt",
                f"{action} {target}% attempt {attempt}: still moving after settle timeout",
                action=action,
                target_percent=target,
                attempt=attempt,
                current_raw=status.current_position,
                current_percent=status.position,
                state=status.state,
                settle_timeout_seconds=CALIBRATION_SETTLE_TIMEOUT_SECONDS,
            )
            await self._async_send_action("stop", refresh=False, simulate=False)
            await asyncio.sleep(CALIBRATION_COMMAND_PAUSE_SECONDS)
            status = await self._async_read_motion_status()
        await self._async_pause_before_next_calibration_command()
        return status, settle_timed_out

    async def _async_pause_before_next_calibration_command(self) -> None:
        """Give the gate controller a small quiet period before the next command."""
        if CALIBRATION_COMMAND_PAUSE_SECONDS > 0:
            await asyncio.sleep(CALIBRATION_COMMAND_PAUSE_SECONDS)

    async def _async_read_motion_status(self) -> NiceBidiStatus:
        """Read status during motion-sensitive operations."""
        try:
            status = await self.hass.async_add_executor_job(self._read_motion_status)
        except NiceBidiAuthError as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_AUTH_FAILED
            self.last_error = str(err)
            raise
        except (NiceBidiConnectionError, OSError) as err:
            self.client.close()
            self.connection_state = CONNECTION_STATE_FAILED
            self.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            raise

        status = self._normalize_status_for_display(self._apply_recent_stop_status_hint(status))
        self._store_successful_status(status)
        self.async_set_updated_data(status)
        return status

    def _read_motion_status(self) -> NiceBidiStatus:
        """Read status through the active polling strategy."""
        if self._use_nhk_status:
            return self.client.read_nhk_status()
        return self.client.read_status()

    def _calibrated_stop_percent(self, target: float, action: str) -> float | None:
        """Return an interpolated calibrated stop percentage for a target."""
        profile = self.calibration_profile
        if profile is None:
            return None

        profile_samples = profile.get("samples")
        if not isinstance(profile_samples, dict):
            return None
        samples = profile_samples.get(action)
        if not isinstance(samples, list):
            return None

        points: list[tuple[float, float]] = [(0.0, 0.0), (100.0, 100.0)]
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            if sample.get("valid", True) is False or sample.get("selection_strategy") == "no_valid_attempt":
                continue
            target_percent = sample.get("target_percent")
            corrected_stop_percent = sample.get("corrected_stop_percent")
            selected_abs_error = sample.get(
                "selected_abs_error_percent",
                sample.get("error_percent"),
            )
            if not isinstance(selected_abs_error, (int, float)):
                continue
            if (
                abs(float(selected_abs_error)) > CALIBRATION_OUTLIER_ERROR_PERCENT
            ):
                continue
            if isinstance(target_percent, (int, float)) and isinstance(
                corrected_stop_percent,
                (int, float),
            ):
                points.append((float(target_percent), float(corrected_stop_percent)))
        if len(points) <= 2:
            return None

        points.sort(key=lambda item: item[0])
        return self._interpolate_stop_percent(float(target), points)

    def _calibrated_stop_raw(self, target: int, action: str, status: NiceBidiStatus) -> int | None:
        """Return an interpolated calibrated raw stop threshold for a target."""
        stop_percent = self._calibrated_stop_percent(float(target), action)
        if stop_percent is None:
            return None
        return self._raw_for_percent(status, stop_percent)

    def _calibrated_stop_delay_seconds(self, current: float, target: float, action: str) -> float | None:
        """Return a calibrated stop delay for a target percentage."""
        profile = self.calibration_profile
        if profile is None:
            return None
        speed = self._calibrated_travel_speed_percent_per_second(action)
        if speed is None or speed <= 0:
            return None
        return abs(float(target) - float(current)) / speed

    def _position_simulation_speed(self, action: str) -> float:
        """Return display animation speed in percent per second."""
        calibrated_speed = self._calibrated_travel_speed_percent_per_second(action)
        if calibrated_speed is None:
            return POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND
        return calibrated_speed * POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR

    def _calibrated_travel_speed_percent_per_second(self, action: str) -> float | None:
        """Return measured full-travel speed from calibration, with legacy fallback."""
        profile = self.calibration_profile
        if profile is None:
            return None

        travel_speed = profile.get("travel_speed")
        if isinstance(travel_speed, dict):
            action_speed = travel_speed.get(action)
            if isinstance(action_speed, dict):
                speed = action_speed.get("speed_percent_per_second")
                if isinstance(speed, (int, float)) and speed > 0:
                    return float(speed)

        return self._sampled_calibration_speed_percent_per_second(action)

    def _sampled_calibration_speed_percent_per_second(self, action: str) -> float | None:
        """Estimate direction speed from older calibration samples."""
        profile = self.calibration_profile
        if profile is None:
            return None
        profile_samples = profile.get("samples")
        if not isinstance(profile_samples, dict):
            return None
        samples = profile_samples.get(action)
        if not isinstance(samples, list):
            return None

        speeds: list[float] = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            if sample.get("valid", True) is False or sample.get("selection_strategy") == "no_valid_attempt":
                continue
            start_percent = sample.get("start_percent")
            final_percent = sample.get("final_percent")
            duration_ms = sample.get("move_duration_ms")
            if not isinstance(start_percent, (int, float)):
                continue
            if not isinstance(final_percent, (int, float)):
                continue
            if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
                continue
            speed = abs(float(final_percent) - float(start_percent)) / (float(duration_ms) / 1000)
            if speed > 0:
                speeds.append(speed)

        if not speeds:
            return None
        return sum(speeds) / len(speeds)

    @staticmethod
    def _interpolate_stop_percent(target: float, points: list[tuple[float, float]]) -> float:
        """Interpolate a corrected stop percentage from calibration points."""
        previous_target, previous_stop = points[0]
        for next_target, next_stop in points[1:]:
            if target <= next_target:
                if next_target == previous_target:
                    return max(0.0, min(100.0, next_stop))
                ratio = (target - previous_target) / (next_target - previous_target)
                stop = previous_stop + ((next_stop - previous_stop) * ratio)
                return max(0.0, min(100.0, stop))
            previous_target, previous_stop = next_target, next_stop
        return max(0.0, min(100.0, points[-1][1]))

    def _calibration_position_source_for_status(
        self,
        status: NiceBidiStatus,
    ) -> _CalibrationPositionSource:
        """Return the best calibration position source visible in one status."""
        source = _CalibrationPositionSource(CALIBRATION_MODE_TIME)
        self._update_calibration_position_source(source, status)
        return source

    def _update_calibration_position_source(
        self,
        source: _CalibrationPositionSource,
        status: NiceBidiStatus,
    ) -> None:
        """Update source selection and learned scalar bounds from a status."""
        if self._has_encoder_calibration_data(status):
            source.mode = CALIBRATION_MODE_ENCODER
            return

        scale = status.registers.get("NHK/T4InstantPositionScale")
        raw = self._live_scalar_raw_from_status(status)
        if scale is not None and scale.startswith("raw") and raw is not None:
            if source.mode != CALIBRATION_MODE_LIVE_SCALAR:
                source.mode = CALIBRATION_MODE_LIVE_SCALAR
                source.scalar_closed_raw = None
                source.scalar_open_raw = None
                source.scalar_min_raw = None
                source.scalar_max_raw = None
            source.scalar_min_raw = raw if source.scalar_min_raw is None else min(source.scalar_min_raw, raw)
            source.scalar_max_raw = raw if source.scalar_max_raw is None else max(source.scalar_max_raw, raw)
            if status.state == STATE_CLOSED:
                source.scalar_closed_raw = raw
            elif status.state == STATE_OPEN:
                source.scalar_open_raw = raw
            return

        if (
            source.mode == CALIBRATION_MODE_TIME
            and scale == "percent"
            and status.position is not None
        ):
            source.mode = CALIBRATION_MODE_LIVE_PERCENT

    def _finalize_live_scalar_bounds(self, source: _CalibrationPositionSource) -> None:
        """Fill missing live-scalar endpoint bounds from observed range."""
        if source.mode != CALIBRATION_MODE_LIVE_SCALAR:
            return
        if (
            source.scalar_closed_raw is not None
            and source.scalar_open_raw is not None
            and source.scalar_closed_raw != source.scalar_open_raw
        ):
            return
        if source.scalar_min_raw is None or source.scalar_max_raw is None:
            return
        if source.scalar_min_raw == source.scalar_max_raw:
            return

        if source.scalar_closed_raw is None and source.scalar_open_raw is None:
            source.scalar_closed_raw = source.scalar_min_raw
            source.scalar_open_raw = source.scalar_max_raw
            return
        if source.scalar_closed_raw is None:
            source.scalar_closed_raw = (
                source.scalar_max_raw
                if source.scalar_open_raw == source.scalar_min_raw
                else source.scalar_min_raw
            )
        if source.scalar_open_raw is None:
            source.scalar_open_raw = (
                source.scalar_max_raw
                if source.scalar_closed_raw == source.scalar_min_raw
                else source.scalar_min_raw
            )

    @staticmethod
    def _learn_live_scalar_bounds_from_travel(
        source: _CalibrationPositionSource,
        action: str,
        start_raw: int | None,
        end_raw: int | None,
    ) -> None:
        """Learn live-scalar endpoint bounds from a full-travel direction."""
        if source.mode != CALIBRATION_MODE_LIVE_SCALAR:
            return
        if action == "open":
            if source.scalar_closed_raw is None and start_raw is not None:
                source.scalar_closed_raw = start_raw
            if source.scalar_open_raw is None and end_raw is not None:
                source.scalar_open_raw = end_raw
            return
        if source.scalar_open_raw is None and start_raw is not None:
            source.scalar_open_raw = start_raw
        if source.scalar_closed_raw is None and end_raw is not None:
            source.scalar_closed_raw = end_raw

    @staticmethod
    def _calibration_source_can_measure_targets(source: _CalibrationPositionSource) -> bool:
        """Return true when the source can measure partial-stop error."""
        if source.mode in {CALIBRATION_MODE_ENCODER, CALIBRATION_MODE_LIVE_PERCENT}:
            return True
        return (
            source.mode == CALIBRATION_MODE_LIVE_SCALAR
            and source.scalar_closed_raw is not None
            and source.scalar_open_raw is not None
            and source.scalar_closed_raw != source.scalar_open_raw
        )

    @staticmethod
    def _calibration_source_bounds(source: _CalibrationPositionSource) -> dict[str, Any]:
        """Return stored bounds for the active calibration source."""
        if source.mode != CALIBRATION_MODE_LIVE_SCALAR:
            return {}
        return {
            "live_scalar_closed_raw": source.scalar_closed_raw,
            "live_scalar_open_raw": source.scalar_open_raw,
            "live_scalar_min_raw": source.scalar_min_raw,
            "live_scalar_max_raw": source.scalar_max_raw,
        }

    @staticmethod
    def _live_scalar_raw_from_status(status: NiceBidiStatus) -> int | None:
        """Return a live T4 raw position from status registers."""
        raw = status.registers.get("NHK/T4InstantPositionRaw")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _calibration_raw_for_status(
        self,
        status: NiceBidiStatus,
        source: _CalibrationPositionSource,
    ) -> int | None:
        """Return the raw value for the active calibration source."""
        if source.mode == CALIBRATION_MODE_ENCODER:
            return status.current_position
        if source.mode == CALIBRATION_MODE_LIVE_SCALAR:
            return self._live_scalar_raw_from_status(status)
        return None

    def _calibration_percent_for_status(
        self,
        status: NiceBidiStatus,
        source: _CalibrationPositionSource,
    ) -> float | None:
        """Return a normalized percentage for the active calibration source."""
        if source.mode == CALIBRATION_MODE_ENCODER:
            if status.position is not None:
                return float(status.position)
            return self._endpoint_percent_from_state(status.state)

        if source.mode == CALIBRATION_MODE_LIVE_SCALAR:
            raw = self._live_scalar_raw_from_status(status)
            if (
                raw is not None
                and source.scalar_closed_raw is not None
                and source.scalar_open_raw is not None
                and source.scalar_closed_raw != source.scalar_open_raw
            ):
                span = source.scalar_open_raw - source.scalar_closed_raw
                return self._clamp_calibration_percent(
                    ((raw - source.scalar_closed_raw) / span) * 100.0
                )
            return self._endpoint_percent_from_state(status.state)

        if source.mode == CALIBRATION_MODE_LIVE_PERCENT:
            if status.position is not None:
                return float(status.position)
            return self._endpoint_percent_from_state(status.state)

        return self._endpoint_percent_from_state(status.state)

    @staticmethod
    def _endpoint_percent_from_state(state: str | None) -> float | None:
        """Return a known endpoint percentage from state."""
        if state == STATE_CLOSED:
            return 0.0
        if state == STATE_OPEN:
            return 100.0
        return None

    @staticmethod
    def _position_reached_for_calibration(action: str, position: float, target: float) -> bool:
        """Return true when a normalized position crossed a target."""
        if action == "open":
            return position >= target
        return position <= target

    @staticmethod
    def _clamp_calibration_percent(percent: float) -> float:
        """Clamp a calibration percentage."""
        return max(0.0, min(100.0, float(percent)))

    @staticmethod
    def _raw_for_percent_from_source(
        source: _CalibrationPositionSource,
        percent: float,
    ) -> int | None:
        """Convert a percent to raw live-scalar value when possible."""
        if (
            source.mode != CALIBRATION_MODE_LIVE_SCALAR
            or source.scalar_closed_raw is None
            or source.scalar_open_raw is None
            or source.scalar_closed_raw == source.scalar_open_raw
        ):
            return None
        span = source.scalar_open_raw - source.scalar_closed_raw
        return round(source.scalar_closed_raw + (span * (percent / 100.0)))

    @staticmethod
    def _has_encoder_calibration_data(status: NiceBidiStatus) -> bool:
        """Return true when a status can support encoder-based calibration."""
        return (
            status.current_position is not None
            and status.position is not None
            and status.closed_position is not None
            and status.open_position is not None
            and status.closed_position != status.open_position
        )

    @staticmethod
    def _is_at_endpoint(status: NiceBidiStatus, action: str) -> bool:
        """Return true when status is at the requested endpoint."""
        if action == "open":
            open_by_position = (
                not status.is_moving
                and status.position is not None
                and status.position >= 100.0 - CALIBRATION_ENDPOINT_TOLERANCE
            )
            return status.state == STATE_OPEN or open_by_position
        closed_by_position = (
            not status.is_moving
            and status.position is not None
            and status.position <= CALIBRATION_ENDPOINT_TOLERANCE
        )
        return status.state == STATE_CLOSED or closed_by_position

    @property
    def calibration_quality(self) -> str | None:
        """Return the latest calibration quality grade."""
        return report_calibration_quality(self.calibration_report)

    @property
    def calibration_report_summary(self) -> str | None:
        """Return a compact calibration report summary."""
        return report_calibration_report_summary(self.calibration_report)

    @property
    def calibration_report_attributes(self) -> dict[str, Any]:
        """Return recorder-safe calibration report attributes."""
        return report_calibration_report_attributes(self.calibration_report)

    def _add_calibration_event(self, stage: str, message: str, **details: Any) -> None:
        """Record one detailed calibration event and write it to HA logs."""
        event = {
            "index": len(self._calibration_events) + 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "stage": stage,
            "message": message,
            "details": details,
        }
        self._calibration_events.append(event)
        if details:
            _LOGGER.info("Nice calibration: %s | %s", message, json.dumps(details, sort_keys=True))
        else:
            _LOGGER.info("Nice calibration: %s", message)

        if self.calibration_state == CALIBRATION_STATE_RUNNING:
            self.calibration_report = self._build_live_calibration_report(message)
            self.async_update_listeners()

    def _log_calibration_report(self, report: CalibrationReport, reason: str) -> None:
        """Write the full calibration report to HA logs in bounded chunks."""
        report_text = self._format_calibration_report(report)
        total_chunks = max(
            1,
            (len(report_text) + CALIBRATION_REPORT_LOG_CHUNK_SIZE - 1) // CALIBRATION_REPORT_LOG_CHUNK_SIZE,
        )
        _LOGGER.warning(
            "Nice calibration report %s: writing %s bytes in %s chunks",
            reason,
            len(report_text),
            total_chunks,
        )
        for chunk_index in range(total_chunks):
            start = chunk_index * CALIBRATION_REPORT_LOG_CHUNK_SIZE
            chunk = report_text[start : start + CALIBRATION_REPORT_LOG_CHUNK_SIZE]
            _LOGGER.warning(
                "Nice calibration report %s chunk %s/%s:\n%s",
                reason,
                chunk_index + 1,
                total_chunks,
                chunk,
            )

    def _build_live_calibration_report(self, summary: str) -> CalibrationReport:
        """Build a report while calibration is still running or failed."""
        return build_live_calibration_report(
            state=self.calibration_state,
            summary=summary,
            events=self._calibration_events,
            tolerance_percent=CALIBRATION_TARGET_TOLERANCE_PERCENT,
            poll_seconds=POSITION_TARGET_POLL_SECONDS,
            settle_seconds=CALIBRATION_SETTLE_SECONDS,
            command_pause_seconds=CALIBRATION_COMMAND_PAUSE_SECONDS,
            max_attempts=CALIBRATION_MAX_ATTEMPTS,
            stability_attempts=CALIBRATION_STABILITY_ATTEMPTS,
        )

    def _build_calibration_report(self, profile: CalibrationProfile) -> CalibrationReport:
        """Build quality metrics and a copyable report from a calibration profile."""
        return build_calibration_report(profile, self.calibration_state)

    @staticmethod
    def _format_calibration_report(report: CalibrationReport) -> str:
        """Format a calibration report as copyable plain text."""
        return format_calibration_report(report)
