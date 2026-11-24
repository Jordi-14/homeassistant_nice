"""Motion trace: record motor load and inputs while the gate moves, only with debug logging on.

While the gate moves (and shortly after a command or the end of a move) the normal status poll also reads
04/D2 (torque, speed, work time) and 04/D1 (inputs, force limiter level). Each poll then gives one sample
with the state and encoder position of the same read: about one sample per 1.2 s on a ROBUSR10 (1 s poll,
~0.18 s per register). Each sample goes to the debug log; the last traces are kept for diagnostics, so a stop
near the end of travel (e.g. a force_limiter_intervent) can be compared with the load just before it.
Read-only: no register is written.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import logging
import time
from typing import TYPE_CHECKING, Any

from ..protocol.t4.io import decode_hardware_diagnostic, decode_io_diagnostic

if TYPE_CHECKING:
    from ..coordinator import NiceBidiDataUpdateCoordinator
    from ..models.status import NiceStatus

_LOGGER = logging.getLogger(__name__)
# the package logger, which "Enable debug logging" in Home Assistant switches
_PACKAGE_LOGGER = logging.getLogger(__name__.rsplit(".", 2)[0])

TRACE_REGISTERS = ((0x04, 0xD2), (0x04, 0xD1))
AFTER_COMMAND_SECONDS = 15.0
AFTER_STOP_SECONDS = 5.0
NEW_TRACE_GAP_SECONDS = 10.0  # a sample after a longer pause starts a new trace
MAX_SAMPLES = 300  # more than the 240 s controller work time; a longer trace continues in a new one
MAX_TRACES = 5


def _decoded(registers: dict[str, str], key: str, decode, info) -> dict[str, Any]:
    raw = registers.get(key)
    if not raw:
        return {}
    try:
        return decode(bytes.fromhex(raw), info) or {}
    except ValueError:
        return {}


class MotionTraceController:
    """Decide when the poll adds the trace registers, and keep the last traces of one config entry."""

    def __init__(self, coordinator: NiceBidiDataUpdateCoordinator) -> None:
        self._coordinator = coordinator
        self._until = 0.0
        self._trigger: str | None = None
        self._trace: dict[str, Any] | None = None
        self._trace_start = 0.0
        self._last_sample = 0.0
        self._moved = False  # the current trace has a moving sample
        self._last_state: str | None = None
        self._command_state: str | None = None  # state when the command came; may still show the old move
        self.traces: deque[dict[str, Any]] = deque(maxlen=MAX_TRACES)

    @staticmethod
    def enabled() -> bool:
        """Return true while debug logging is on for the integration."""
        return _PACKAGE_LOGGER.isEnabledFor(logging.DEBUG)

    def handle_command(self, command: str) -> None:
        """Keep tracing for a while after an accepted command."""
        if self.enabled():
            self._until = max(self._until, time.monotonic() + AFTER_COMMAND_SECONDS)
            self._trigger = f"command:{command}"

    def extra_registers(self) -> tuple[tuple[int, int], ...]:
        """Return the registers the next status poll should add, or () when no trace is due."""
        if not self.enabled():
            return ()
        now = time.monotonic()
        data = self._coordinator.data
        if data is not None and data.is_moving:  # also remote, wall button and auto-close moves
            self._until = max(self._until, now + AFTER_STOP_SECONDS)
        return TRACE_REGISTERS if now < self._until else ()

    def record(self, status: NiceStatus) -> None:
        """Add one sample from a status poll that read the trace registers."""
        now = time.monotonic()
        # a new move after a stop, or a reversal, gets its own trace like a new command
        new_move = status.is_moving and self._moved and status.state != self._last_state
        gap = self._trace is None or now - self._last_sample > NEW_TRACE_GAP_SECONDS
        if self._trigger is not None or new_move or gap:
            trigger = self._trigger or "movement"
        elif len(self._trace["samples"]) >= MAX_SAMPLES:
            trigger = "continued"
        else:
            trigger = None
        if trigger is not None:
            self._trace = {"started_at": datetime.now(UTC).isoformat(), "trigger": trigger, "samples": []}
            self._trace_start = now
            self._command_state = self._last_state if self._trigger is not None else None
            self._trigger = None
            self._moved = False
            self.traces.append(self._trace)
        self._last_sample = now
        self._last_state = status.state
        self._moved = self._moved or (status.is_moving and status.state != self._command_state)
        sample = self._sample(status, now - self._trace_start)
        self._trace["samples"].append(sample)
        self._coordinator.incidents.add("load", "motion_trace", sample)
        _LOGGER.debug("Nice motion trace %s", sample)

    def _sample(self, status: NiceStatus, elapsed: float) -> dict[str, Any]:
        info = self._coordinator.register_info
        registers = status.registers
        hardware = _decoded(registers, "04/D2", decode_hardware_diagnostic, info.get("04/D2"))
        io = _decoded(registers, "04/D1", decode_io_diagnostic, info.get("04/D1"))
        return {
            "t": round(elapsed, 2),
            "state": status.state,
            "encoder": status.current_position,
            **{key: hardware[key] for key in ("torque_m1", "torque_m2", "speed_m1", "speed_m2", "voltage_m1", "voltage_m2", "work_time_left") if key in hardware},
            "force_limiter_state": io.get("force_limiter_state"),
            "d2": registers.get("04/D2"),
        }
