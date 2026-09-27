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
"""

from __future__ import annotations

from dataclasses import dataclass

from app.envelope.evaluator import Limits, Severity

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
