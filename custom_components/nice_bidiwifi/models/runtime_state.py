"""Explicit state owners for connection, estimation, animation and commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from ..movement_profile import MovementProfile

if TYPE_CHECKING:
    from homeassistant.helpers.storage import Store
    from .commands import NiceCommandResult
    from ..connection import NiceConnectionHealth


@dataclass(slots=True)
class AnimationState:
    task: asyncio.Task | None = None
    action: str | None = None
    anchor_position: float | None = None
    anchor_monotonic: float | None = None
    started_monotonic: float | None = None
    deadline_monotonic: float | None = None
    confirmed_moving: bool = False
    target_position: float | None = None
    speed_percent_per_second: float | None = None
    speed_curve: tuple[float, ...] | None = None


@dataclass(slots=True)
class EstimatorState:
    last_known_position: float | None = None
    reporting_observed: bool = False
    observation: tuple[str, float, float] | None = None
    observed_speeds: dict[str, float] = field(default_factory=dict)
    profile: MovementProfile = field(default_factory=MovementProfile)
    store: Store | None = None
    dirty: bool = False


@dataclass(slots=True)
class PositionControlState:
    target_task: asyncio.Task | None = None
    refresh_task: asyncio.Task | None = None
    fast_poll_until: float | None = None
    recent_stop: float | None = None
    stop_started_moving: bool = False


@dataclass(slots=True)
class ConnectionRuntimeState:
    health: NiceConnectionHealth | None = None
    state: str = "unknown"
    last_error: str | None = None
    last_successful_update: datetime | None = None


@dataclass(slots=True)
class CommandRuntimeState:
    last_result: NiceCommandResult | None = None
    last_command: str | None = None
    last_command_monotonic: float | None = None
    last_command_latency_ms: int | None = None


@dataclass(slots=True)
class DiagnosticRuntimeState:
    maneuver_baseline: tuple | None = None
    nhk_fields: dict[str, str] = field(default_factory=dict)
    nhk_read_at: datetime | None = None
    obstacle_seed: str | None = None
    obstacle_probed: bool = False
    limits_changed: dict | None = None
