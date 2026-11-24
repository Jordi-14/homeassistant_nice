"""Field-aware observation reducer for ordered polls, events and readbacks."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
import time
from typing import Any

from .models.status import NiceStatus

BOOKKEEPING = frozenset(
    {"registers", "observed_fields", "fresh_registers", "observation_times"}
)
CORE_REGISTERS = {
    "state": "04/01",
    "current_position": "04/11",
    "open_position": "04/18",
    "closed_position": "04/19",
}


@dataclass(frozen=True, slots=True)
class PollToken:
    revision: int
    generation: int
    started: float


@dataclass(frozen=True, slots=True)
class FieldObservation:
    value: Any
    source: str
    revision: int
    monotonic: float
    received_at: str


def declared_fields(status: NiceStatus) -> frozenset[str]:
    if status.observed_fields is not None:
        return status.observed_fields
    # Compatibility for external protocol adapters that return full snapshots.
    core = {"state", "position", "current_position", "open_position", "closed_position"}
    return frozenset(
        f.name
        for f in fields(status)
        if f.name not in BOOKKEEPING
        and (f.name in core or getattr(status, f.name) is not None)
    )


class ObservationStore:
    """Own reported measurements; display/animation changes never feed this store."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self.revision = 0
        self.generation = 0
        self.closed = False
        self._values: dict[str, FieldObservation] = {}
        self._registers: dict[str, FieldObservation] = {}

    def seed(self, status: NiceStatus | None) -> None:
        if self.closed or status is None or self.revision or self._values:
            return
        for name in declared_fields(status):
            self._values[name] = FieldObservation(
                getattr(status, name), "baseline", 0, 0, ""
            )
        for key, value in status.registers.items():
            self._registers[key] = FieldObservation(value, "baseline", 0, 0, "")

    def begin_poll(self, baseline: NiceStatus | None = None) -> PollToken:
        self.seed(baseline)
        return PollToken(self.revision, self.generation, self._clock())

    def invalidate(self, *, close=False) -> None:
        self.generation += 1
        self.closed = close

    def observation(self, name: str) -> FieldObservation | None:
        return self._values.get(name)

    def _snapshot(self, fresh: set[str], fresh_registers: set[str]) -> NiceStatus:
        values = {
            f.name: self._values[f.name].value if f.name in self._values else None
            for f in fields(NiceStatus)
            if f.name not in BOOKKEEPING
        }
        values["registers"] = {k: v.value for k, v in self._registers.items()}
        values["observed_fields"] = frozenset(fresh)
        values["fresh_registers"] = frozenset(fresh_registers)
        values["observation_times"] = {
            k: v.monotonic for k, v in self._values.items() if k in fresh
        }
        return NiceStatus(**values)

    def snapshot(self) -> NiceStatus:
        return self._snapshot(set(), set())

    @staticmethod
    def _source(name: str, status: NiceStatus, origin: str) -> str:
        if origin != "poll":
            return origin
        keys = (
            status.fresh_registers
            if status.fresh_registers is not None
            else status.registers
        )
        if name == "state":
            if (
                "NHK/T4Status" in keys
                and status.registers.get("NHK/T4Status") == status.state
            ):
                return (
                    "t4_04_02"
                    if status.registers.get("NHK/T4PayloadKind") == "04/02"
                    else "t4_04_40_motion"
                )
            return "nhk_door_status" if "NHK/DoorStatus" in keys else "dmp_04_01"
        if name == "position":
            if "NHK/T4CalibratedPosition" in status.registers:
                return "t4_live_scalar_calibrated"
            if "NHK/T4InstantPosition" in status.registers:
                return "t4_04_40"
            return "dmp_encoder"
        return "dmp_get"

    def apply_poll(self, status: NiceStatus, token: PollToken) -> NiceStatus:
        if self.closed or token.generation != self.generation:
            return self.snapshot()
        fresh = set()
        raw_fresh = set()
        self.revision += 1
        stamp = datetime.now(UTC).isoformat()
        for name in declared_fields(status):
            current = self._values.get(name)
            if current is not None and current.revision > token.revision:
                if name == "position":
                    self._registers["NHK/PollPositionIgnored"] = FieldObservation(
                        "newer_live_update",
                        "reducer",
                        self.revision,
                        self._clock(),
                        stamp,
                    )
                continue
            captured = (status.observation_times or {}).get(name, self._clock())
            if current is not None and current.monotonic > captured:
                continue
            self._values[name] = FieldObservation(
                getattr(status, name),
                self._source(name, status, "poll"),
                self.revision,
                captured,
                stamp,
            )
            fresh.add(name)
            if name == "position" and self._values[name].source == "dmp_encoder":
                for marker in (
                    "NHK/T4CalibratedPosition",
                    "NHK/T4EncoderPosition",
                    "NHK/T4InstantPosition",
                    "NHK/T4InstantPositionRaw",
                    "NHK/T4InstantPositionScale",
                    "NHK/T4InstantPositionPayload",
                    "NHK/ConfirmedEndpointPosition",
                ):
                    self._registers.pop(marker, None)
        keys = (
            status.fresh_registers
            if status.fresh_registers is not None
            else status.registers.keys()
        )
        for key in keys:
            current = self._registers.get(key)
            if (
                key not in status.registers
                or current is not None
                and current.revision > token.revision
            ):
                continue
            self._registers[key] = FieldObservation(
                status.registers[key], "poll", self.revision, self._clock(), stamp
            )
            raw_fresh.add(key)
        return self._snapshot(fresh, raw_fresh)

    def apply_update(
        self,
        values: dict[str, Any],
        registers: dict[str, str],
        *,
        source: str,
        baseline: NiceStatus | None = None,
        sources: dict[str, str] | None = None,
        removed_registers: set[str] | None = None,
    ) -> NiceStatus:
        self.seed(baseline)
        if self.closed:
            return self.snapshot()
        self.revision += 1
        now = self._clock()
        stamp = datetime.now(UTC).isoformat()
        for name, value in values.items():
            if name not in BOOKKEEPING:
                self._values[name] = FieldObservation(
                    value, (sources or {}).get(name, source), self.revision, now, stamp
                )
        for key in removed_registers or ():
            self._registers.pop(key, None)
        for key, value in registers.items():
            self._registers[key] = FieldObservation(
                value, source, self.revision, now, stamp
            )
        return self._snapshot(set(values) - BOOKKEEPING, set(registers))

    def diagnostics(self) -> dict:
        return {
            name: {
                "source": value.source,
                "age_seconds": max(0, round(self._clock() - value.monotonic, 3)),
                "received_at": value.received_at,
                "revision": value.revision,
                "available": value.value is not None,
            }
            for name, value in self._values.items()
        }
