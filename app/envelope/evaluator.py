"""
Envelope evaluator (T9-1) - pure classification, no plant dependency.

Classifies a value against up to six named limits - `trip_lo`, `alarm_lo`,
`warning_lo`, `warning_hi`, `alarm_hi`, `trip_hi` - producing one of four
ordered severities: NORMAL, WARNING, ALARM, TRIP. Any subset of the six may
be left unset; an unset limit can never be reached.

A limit is inclusive to the band it guards: `value <= trip_lo` is TRIP, not
`value < trip_lo`. Landing exactly on a boundary is a defined outcome, not an
accident of float comparison, and the same `<=`/`>=` convention applies
consistently on both sides and at every severity.

**Deadband guards de-escalation only.** A raw classification worse than the
held severity always applies (subject to on-delay below); a raw
classification better than the held severity applies only once the value has
cleared the held band's own limit by more than `deadband`, on the safe side.
A value sitting exactly at a limit therefore holds its worse classification
rather than chattering between two severities on float noise.

**On-delay guards escalation only.** A raw classification worse than the held
severity has to persist - the same band, accumulated across possibly many
`evaluate()` calls - for at least `on_delay` before it is committed; a
transient shorter than that never registers. De-escalation is immediate once
deadband clears: nothing here is meant to hold back a real recovery.

Like `PID.compute()` (T8-1), `evaluate()` is a deterministic function of its
arguments and the state left by the previous call - no wall clock, no plant,
no randomness - so a caller supplies elapsed time as `dt` the same way the
solver's own devices do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

_Side = Literal["lo", "hi"]

_LIMIT_ORDER: tuple[str, ...] = (
    "trip_lo",
    "alarm_lo",
    "warning_lo",
    "warning_hi",
    "alarm_hi",
    "trip_hi",
)


class Severity(IntEnum):
    NORMAL = 0
    WARNING = 1
    ALARM = 2
    TRIP = 3


@dataclass(frozen=True)
class Limits:
    trip_lo: float | None = None
    alarm_lo: float | None = None
    warning_lo: float | None = None
    warning_hi: float | None = None
    alarm_hi: float | None = None
    trip_hi: float | None = None

    def __post_init__(self) -> None:
        present = [
            (name, value)
            for name in _LIMIT_ORDER
            if (value := getattr(self, name)) is not None
        ]
        for (name_a, value_a), (name_b, value_b) in zip(present, present[1:]):
            if value_a > value_b:
                raise ValueError(
                    f"{name_a} ({value_a}) must not exceed {name_b} ({value_b})"
                )


@dataclass(frozen=True)
class _Band:
    severity: Severity
    side: _Side
    threshold: float


def _classify(value: float, limits: Limits) -> _Band | None:
    if limits.trip_lo is not None and value <= limits.trip_lo:
        return _Band(Severity.TRIP, "lo", limits.trip_lo)
    if limits.trip_hi is not None and value >= limits.trip_hi:
        return _Band(Severity.TRIP, "hi", limits.trip_hi)
    if limits.alarm_lo is not None and value <= limits.alarm_lo:
        return _Band(Severity.ALARM, "lo", limits.alarm_lo)
    if limits.alarm_hi is not None and value >= limits.alarm_hi:
        return _Band(Severity.ALARM, "hi", limits.alarm_hi)
    if limits.warning_lo is not None and value <= limits.warning_lo:
        return _Band(Severity.WARNING, "lo", limits.warning_lo)
    if limits.warning_hi is not None and value >= limits.warning_hi:
        return _Band(Severity.WARNING, "hi", limits.warning_hi)
    return None


class Evaluator:
    def __init__(self, limits: Limits, deadband: float = 0.0, on_delay: float = 0.0) -> None:
        if deadband < 0.0:
            raise ValueError(f"deadband must be non-negative, got {deadband}")
        if on_delay < 0.0:
            raise ValueError(f"on_delay must be non-negative, got {on_delay}")

        self.limits = limits
        self.deadband = deadband
        self.on_delay = on_delay

        self._band: _Band | None = None
        self._pending: _Band | None = None
        self._pending_elapsed = 0.0

    @property
    def severity(self) -> Severity:
        return self._band.severity if self._band is not None else Severity.NORMAL

    def evaluate(self, value: float, dt: float) -> Severity:
        if dt < 0.0:
            raise ValueError(f"dt must be non-negative, got {dt}")

        raw = _classify(value, self.limits)
        raw_severity = raw.severity if raw is not None else Severity.NORMAL
        current = self.severity

        if raw_severity > current:
            if (
                raw is not None
                and self._pending is not None
                and raw.severity == self._pending.severity
                and raw.side == self._pending.side
            ):
                self._pending_elapsed += dt
            else:
                self._pending = raw
                self._pending_elapsed = dt
            if self._pending_elapsed >= self.on_delay:
                self._band = raw
                self._clear_pending()
            return self.severity

        self._clear_pending()

        if raw_severity == current:
            self._band = raw
            return current

        # raw_severity < current: de-escalating out of the held band. current
        # > NORMAL here, so self.severity's own definition guarantees a band.
        held = self._band
        assert held is not None
        if held.side == "lo":
            cleared = value >= held.threshold + self.deadband
        else:
            cleared = value <= held.threshold - self.deadband
        if cleared:
            self._band = raw
        return self.severity

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_elapsed = 0.0
