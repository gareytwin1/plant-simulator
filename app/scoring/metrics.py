"""
Result metrics calculator (T15-2) - what happened in a run, as numbers.

A pure function of recorded inputs and nothing else: the operator
`ActionLog` (T15-1), the `AlarmHistory` (T10-3), the `ExcursionTracker`s
(T9-3) and the `ThroughputTracker`s (this task). No wall clock, no live plant, no `random`: the same inputs always give
the same `RunMetrics`. T15-3's weighted scoring consumes this; nothing here
weighs or grades.

**Time basis.** Action and alarm timestamps are both sim time, so
`time_to_recognise_s` subtracts like from like. The trackers carry durations
(sums of `dt`), never timestamps, so they never mix into a subtraction.

**What each metric reads, and its definition**

    alarm_count            AlarmHistory: every `Event` retained. Escalations
                           and re-activations each count, because each one
                           demanded the operator's attention again.
                           Acknowledgements are not alarms.
    trip_count             TripRecords: one per interlock latch, recorded by
                           `PlantRuntime`. A trip-rated band with no
                           interlock behind it is not a trip: it still
                           feeds alarm_count, peak_excursions and
                           time_outside_envelope_s, but never this.
    outcome                The run's outcome as a plain string
                           (`succeeded`, `failed` or `timed_out`), passed in
                           by the caller: app/scoring never imports
                           app/scenarios.
    time_to_stabilise_s    AlarmHistory: first alarm to the last `ClearRecord`,
                           once every point that alarmed has returned to
                           NORMAL and stayed there. None if there was no
                           alarm or any point is still outside NORMAL (an
                           unstabilised run has no time to stabilise).
    time_to_recognise_s    ActionLog + AlarmHistory: first relevant action at
                           or after the first alarm, minus the first alarm.
                           None if there was no alarm or no such action.
    peak_excursions        Trackers: each one's `peak`, keyed by the caller's
                           label (None for a point that never left NORMAL).
    time_outside_envelope_s  Trackers: summed WARNING, ALARM and TRIP time
                           across all of them. Two points out at once count
                           twice - it is point-seconds.
    production_lost        ThroughputTrackers: each one's `lost` (shortfall
                           below target integrated over time), keyed by the
                           caller's label, in that variable's own unit times
                           seconds. Never summed across variables.
    unnecessary_actions    ActionLog: actions the caller's `is_relevant`
                           rejects. Relevance ("moved a relevant variable
                           toward target") needs the scenario's targets and
                           a device's direction of effect, which none of the
                           three inputs carry, so the scenario supplies it.

**In a live run (T20-15)** `PlantRuntime` records the alarm history (clear
records included) and a `TripRecord` for each interlock that latches. It feeds the
trackers that `config/scoring.yaml` names for the plant from each snapshot it
publishes; `ScenarioRunner` computes the metrics once, when the run completes.

**Eviction.** `AlarmHistory` is bounded, so a run longer than its capacity
undercounts alarms and may lose the first alarm. Size the history
for the run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.alarms.history import AlarmHistory, ClearRecord
from app.alarms.manager import Event
from app.envelope.evaluator import Severity
from app.envelope.tracker import Excursion, ExcursionTracker
from app.scoring.actionlog import ActionEvent, ActionLog
from app.scoring.throughput import ThroughputTracker

_NON_NORMAL: tuple[Severity, ...] = (Severity.WARNING, Severity.ALARM, Severity.TRIP)


@dataclass(frozen=True)
class TripRecord:
    """An interlock latched: the sim time of the snapshot it was evaluated
    on, and its tag."""

    sim_time: float
    tag: str

    def as_dict(self) -> dict[str, Any]:
        return {"sim_time": self.sim_time, "tag": self.tag}


@dataclass(frozen=True)
class RunMetrics:
    """Immutable, comparable by value, not hashable (it holds a mapping)."""

    alarm_count: int
    trip_count: int
    time_to_recognise_s: float | None
    time_to_stabilise_s: float | None
    peak_excursions: Mapping[str, Excursion | None]
    time_outside_envelope_s: float
    production_lost: Mapping[str, float]
    unnecessary_actions: int
    outcome: str

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe: a peak as its severity name, magnitude and timestamp.
        Any: values of several shapes, all of them JSON."""
        return {
            "alarm_count": self.alarm_count,
            "trip_count": self.trip_count,
            "time_to_recognise_s": self.time_to_recognise_s,
            "time_to_stabilise_s": self.time_to_stabilise_s,
            "peak_excursions": {
                label: None
                if excursion is None
                else {
                    "severity": excursion.severity.name,
                    "magnitude": excursion.magnitude,
                    "timestamp": excursion.timestamp,
                }
                for label, excursion in self.peak_excursions.items()
            },
            "time_outside_envelope_s": self.time_outside_envelope_s,
            "production_lost": dict(self.production_lost),
            "unnecessary_actions": self.unnecessary_actions,
            "outcome": self.outcome,
        }


def compute_metrics(
    actions: ActionLog,
    alarms: AlarmHistory,
    trackers: Mapping[str, ExcursionTracker],
    is_relevant: Callable[[ActionEvent], bool],
    throughput: Mapping[str, ThroughputTracker] | None = None,
    *,
    trips: Sequence[TripRecord],
    outcome: str,
) -> RunMetrics:
    events = [entry for entry in alarms.entries() if isinstance(entry, Event)]
    first_alarm = min((event.sim_time for event in events), default=None)

    return RunMetrics(
        alarm_count=len(events),
        trip_count=len(trips),
        time_to_recognise_s=_time_to_recognise(actions, first_alarm, is_relevant),
        time_to_stabilise_s=_time_to_stabilise(alarms),
        peak_excursions=MappingProxyType(
            {label: tracker.peak for label, tracker in trackers.items()}
        ),
        time_outside_envelope_s=sum(
            tracker.time_in(severity) for tracker in trackers.values() for severity in _NON_NORMAL
        ),
        production_lost=MappingProxyType(
            {label: tracker.lost for label, tracker in (throughput or {}).items()}
        ),
        unnecessary_actions=sum(1 for action in actions if not is_relevant(action)),
        outcome=outcome,
    )


def _time_to_recognise(
    actions: ActionLog,
    first_alarm: float | None,
    is_relevant: Callable[[ActionEvent], bool],
) -> float | None:
    if first_alarm is None:
        return None

    responses = [
        action.sim_time
        for action in actions
        if action.sim_time >= first_alarm and is_relevant(action)
    ]

    return min(responses) - first_alarm if responses else None


def _time_to_stabilise(alarms: AlarmHistory) -> float | None:
    first_alarm: float | None = None
    last_clear = 0.0
    outstanding: set[str] = set()

    for entry in alarms.entries():
        if isinstance(entry, Event):
            if first_alarm is None:
                first_alarm = entry.sim_time
            outstanding.add(entry.id)
        elif isinstance(entry, ClearRecord):
            outstanding.discard(entry.alarm_id)
            last_clear = entry.sim_time

    if first_alarm is None or outstanding:
        return None

    return last_clear - first_alarm
