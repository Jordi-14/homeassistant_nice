"""Learn directional travel speeds by position without using simulated readings."""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
import math
from statistics import median

BANDS = (0.0, 10.0, 20.0, 50.0, 80.0, 90.0, 100.0)
CENTERS = tuple((a + b) / 2 for a, b in zip(BANDS, BANDS[1:], strict=False))
SAMPLE_LIMIT = 7
MIN_SPEED, MAX_SPEED = 0.05, 25.0


def valid_speed(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and MIN_SPEED <= value <= MAX_SPEED


def speed_at(position: float, curve: tuple[float, ...]) -> float:
    """Linearly blend speed estimates between band centers."""
    i = bisect_right(CENTERS, position)
    if i == 0:
        return curve[0]
    if i == len(CENTERS):
        return curve[-1]
    weight = (position - CENTERS[i - 1]) / (CENTERS[i] - CENTERS[i - 1])
    return curve[i - 1] * (1 - weight) + curve[i] * weight


def advance(position: float, seconds: float, action: str, curve: tuple[float, ...]) -> float:
    """Integrate a positive, piecewise linear speed curve exactly across bands."""
    direction = 1 if action == "open" else -1
    stops = [p for p in CENTERS if direction * (p - position) > 1e-9]
    stops.sort(reverse=direction < 0)
    stops.append(100.0 if direction > 0 else 0.0)
    remaining = max(0.0, seconds)
    for end in stops:
        distance = abs(end - position)
        if distance <= 1e-9:
            continue
        first, last = speed_at(position, curve), speed_at(end, curve)
        gradient = (last - first) / distance
        duration = distance / first if abs(gradient) < 1e-9 else math.log(last / first) / gradient
        if remaining < duration:
            travel = first * remaining if abs(gradient) < 1e-9 else first * math.expm1(gradient * remaining) / gradient
            return position + direction * travel
        remaining -= duration
        position = end
    return position


class MovementProfile:
    """One robust sample per band per movement, plus provisional current samples."""

    def __init__(self) -> None:
        self.samples = {a: [deque(maxlen=SAMPLE_LIMIT) for _ in CENTERS] for a in ("open", "close")}
        self.settings: dict[str, int] = {}
        self.action: str | None = None
        self.previous: tuple[float, float] | None = None
        self.totals = [[0.0, 0.0, 0] for _ in CENTERS]

    def reset(self, action: str) -> None:
        self.samples[action] = [deque(maxlen=SAMPLE_LIMIT) for _ in CENTERS]
        if self.action == action:
            self.action = None
            self.previous = None
            self.totals = [[0.0, 0.0, 0] for _ in CENTERS]

    def setting_changed(self, action: str, value: int | None) -> bool:
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        changed = action in self.settings and self.settings[action] != value
        if changed:
            self.reset(action)
        self.settings[action] = value
        return changed

    def _estimates(self) -> list[float | None]:
        result = []
        for i, (distance, seconds, intervals) in enumerate(self.totals):
            enough = distance >= max(1.0, (BANDS[i + 1] - BANDS[i]) * 0.2) and seconds >= 0.5 and intervals >= 2
            speed = distance / seconds if enough else None
            result.append(speed if valid_speed(speed) else None)
        return result

    def finish(self) -> bool:
        changed = False
        if self.action is not None:
            for i, value in enumerate(self._estimates()):
                if value is not None:
                    values = self.samples[self.action][i]
                    # Do not let a single delayed/batched pass replace an established band.
                    if len(values) < 3 or median(values) / 3 <= value <= median(values) * 3:
                        values.append(value)
                        changed = True
        self.action = None
        self.previous = None
        self.totals = [[0.0, 0.0, 0] for _ in CENTERS]
        return changed

    def observe(self, action: str, position: float, now: float) -> bool:
        """Collect unique forward readings; ignore duplicates and short reply bursts."""
        if not math.isfinite(position) or not 0 <= position <= 100 or not math.isfinite(now):
            return False
        changed = False
        if self.action != action:
            changed = self.finish()
            self.action = action
        if self.previous is None:
            self.previous = (position, now)
            return changed
        before, started = self.previous
        distance = (position - before) * (1 if action == "open" else -1)
        elapsed = now - started
        if elapsed > 30:
            # A long loss of telemetry cannot be assigned to particular bands.
            self.totals = [[0.0, 0.0, 0] for _ in CENTERS]
            self.previous = (position, now)
            return changed
        if distance < 0.5 or elapsed < 0.25 or distance / elapsed > MAX_SPEED:
            return changed
        self.previous = (position, now)
        low, high = sorted((before, position))
        # A huge gap cannot tell us which part was fast or slow.
        if sum(min(high, b) > max(low, a) for a, b in zip(BANDS, BANDS[1:], strict=False)) > 2:
            return changed
        for i, (a, b) in enumerate(zip(BANDS, BANDS[1:], strict=False)):
            overlap = max(0.0, min(high, b) - max(low, a))
            if overlap:
                self.totals[i][0] += overlap
                self.totals[i][1] += elapsed * overlap / distance
                self.totals[i][2] += 1
        return changed

    def curve(self, action: str, fallback: float, factor: float = 0.9) -> tuple[float, ...]:
        fallback = fallback if valid_speed(fallback) else 1.0
        provisional = self._estimates() if self.action == action else [None] * len(CENTERS)
        speeds = []
        for i, values in enumerate(self.samples[action]):
            candidates = list(values)
            if not candidates and provisional[i] is not None:
                candidates.append(provisional[i])
            speed = median(candidates) if candidates else fallback
            speeds.append(max(MIN_SPEED, min(MAX_SPEED, speed)) * factor)
        return tuple(speeds)

    def export(self) -> dict:
        return {"version": 1, "bands": list(BANDS), "settings": dict(self.settings),
                "samples": {a: [list(v) for v in bands] for a, bands in self.samples.items()}}

    def load(self, data: object) -> None:
        """Ignore incompatible/corrupt data without affecting gate calibration."""
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("bands") != list(BANDS):
            return
        samples = data.get("samples")
        if not isinstance(samples, dict):
            return
        for action in self.samples:
            bands = samples.get(action)
            if not isinstance(bands, list) or len(bands) != len(CENTERS):
                continue
            for i, values in enumerate(bands):
                if isinstance(values, list):
                    self.samples[action][i] = deque((v for v in values if valid_speed(v)), maxlen=SAMPLE_LIMIT)
        settings = data.get("settings")
        if isinstance(settings, dict):
            self.settings = {a: v for a, v in settings.items() if a in self.samples and isinstance(v, int) and not isinstance(v, bool)}

    def summary(self) -> dict:
        return {action: [
            {"from": BANDS[i], "to": BANDS[i + 1], "speed_percent_per_second": round(median(values), 3) if values else None,
             "samples": len(values)} for i, values in enumerate(bands)
        ] for action, bands in self.samples.items()}
