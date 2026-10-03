"""
Excursion tracker (T9-3) - accumulates time-in-band and peak excursion for
one monitored point, from severities T9-1's `Evaluator` already classified.

Raw material for two things neither of which exists yet: the trip delay
window (a TRIP that must persist before it acts) and scoring (how long and
how far a variable strayed). Like `Evaluator.evaluate()`, this is a pure
function of its inputs and the state left by the previous call - no wall
clock, no plant - so a caller supplies elapsed time as `dt` the same way.

This module never reclassifies a value itself - it takes the `Severity` the
caller's own `Evaluator` already produced, so `Evaluator`'s deadband and
on-delay are the only place hysteresis lives. Magnitude is measured against
the severity's own limits: WARNING with a value under `warning_lo` reports
how far under `warning_lo` it fell, and correspondingly for the other two
severities and the hi side - so a tracker and its `Evaluator` are handed the
same `Limits`.

**A held severity and a raw threshold miss can disagree, deliberately.**
`Evaluator`'s deadband holds a severity after the raw value has already
recovered past its own threshold (the value is inside the band, just not by
more than `deadband`). `update()` still receives that held severity and
correctly keeps accumulating its time-in-band, but `_magnitude()` finds the
value on neither side of the threshold and reports `0.0` for that call. This
never corrupts `peak`, which only keeps the largest magnitude seen and was
already set to the real one while the value was genuinely beyond the
threshold - it just means a magnitude of `0.0` does not, on its own, imply
the value was in NORMAL.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.envelope.evaluator import Limits, Severity
from app.statetypes import StateError

_NON_NORMAL: tuple[Severity, ...] = (Severity.WARNING, Severity.ALARM, Severity.TRIP)

_THRESHOLDS: dict[Severity, tuple[str, str]] = {
    Severity.WARNING: ("warning_lo", "warning_hi"),
    Severity.ALARM: ("alarm_lo", "alarm_hi"),
    Severity.TRIP: ("trip_lo", "trip_hi"),
}


def _magnitude(value: float, limits: Limits, severity: Severity) -> float:
    lo_name, hi_name = _THRESHOLDS[severity]
    lo: float | None = getattr(limits, lo_name)
    hi: float | None = getattr(limits, hi_name)
    if lo is not None and value <= lo:
        return lo - value
    if hi is not None and value >= hi:
        return value - hi
    return 0.0


@dataclass(frozen=True)
class Excursion:
    severity: Severity
    magnitude: float
    timestamp: float


@dataclass(frozen=True)
class TrackerCheckpoint:
    elapsed: float
    time_in_band: dict[Severity, float]
    peak: Excursion | None


class ExcursionTracker:
    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self._elapsed = 0.0
        self._time_in_band: dict[Severity, float] = dict.fromkeys(_NON_NORMAL, 0.0)
        self._peak: Excursion | None = None

    @property
    def peak(self) -> Excursion | None:
        return self._peak

    def time_in(self, severity: Severity) -> float:
        return self._time_in_band.get(severity, 0.0)

    def update(self, value: float, severity: Severity, dt: float) -> None:
        if dt < 0.0:
            raise ValueError(f"dt must be non-negative, got {dt}")

        self._elapsed += dt

        if severity is Severity.NORMAL:
            return

        self._time_in_band[severity] += dt

        magnitude = _magnitude(value, self.limits, severity)
        if self._peak is None or magnitude > self._peak.magnitude:
            self._peak = Excursion(severity, magnitude, self._elapsed)

    def reset(self) -> None:
        self._elapsed = 0.0
        self._time_in_band = dict.fromkeys(_NON_NORMAL, 0.0)
        self._peak = None

    def checkpoint(self) -> TrackerCheckpoint:
        return TrackerCheckpoint(self._elapsed, dict(self._time_in_band), self._peak)

    def validate_checkpoint(self, checkpoint: TrackerCheckpoint) -> None:
        if checkpoint.elapsed < 0.0:
            raise StateError("elapsed", f"{checkpoint.elapsed!r} is negative")

        if set(checkpoint.time_in_band) != set(_NON_NORMAL):
            raise StateError(
                "time_in_band",
                f"expected exactly {[band.name for band in _NON_NORMAL]}, "
                f"got {[band.name for band in checkpoint.time_in_band]}",
            )

        for band, seconds in checkpoint.time_in_band.items():
            if seconds < 0.0:
                raise StateError(f"time_in_band.{band.name}", f"{seconds!r} is negative")

        peak = checkpoint.peak

        if peak is not None:
            if peak.magnitude < 0.0:
                raise StateError("peak.magnitude", f"{peak.magnitude!r} is negative")

            if peak.timestamp < 0.0:
                raise StateError("peak.timestamp", f"{peak.timestamp!r} is negative")

    def restore_checkpoint(self, checkpoint: TrackerCheckpoint) -> None:
        self.validate_checkpoint(checkpoint)
        self._elapsed = checkpoint.elapsed
        self._time_in_band = dict(checkpoint.time_in_band)
        self._peak = checkpoint.peak
