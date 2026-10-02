"""
Result metrics calculator (T15-2) - what happened in a run, as numbers.

A pure function of three recorded inputs and nothing else: the operator
`ActionLog` (T15-1), the `AlarmHistory` (T10-3) and the `ExcursionTracker`s
(T9-3). No wall clock, no live plant, no `random`: the same inputs always give
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
    trip_count             AlarmHistory: `Event`s whose data severity is
                           TRIP. Trips are not live in a session (nothing
                           calls `TripSystem.update`), so a trip here means
                           a TRIP-severity envelope band was entered, which
                           is the only trip record the contracts hold.
    time_to_recognise_s    ActionLog + AlarmHistory: first relevant action at
                           or after the first alarm, minus the first alarm.
                           None if there was no alarm or no such action.
    peak_excursions        Trackers: each one's `peak`, keyed by the caller's
                           label (None for a point that never left NORMAL).
    time_outside_envelope_s  Trackers: summed WARNING, ALARM and TRIP time
                           across all of them. Two points out at once count
                           twice - it is point-seconds.
    unnecessary_actions    ActionLog: actions the caller's `is_relevant`
                           rejects. Relevance ("moved a relevant variable
                           toward target") needs the scenario's targets and
                           a device's direction of effect, which none of the
                           three inputs carry, so the scenario supplies it.

**Not computed here.** `time to stabilise` and `production lost` have no
source in these three inputs: `AlarmManager` emits nothing when a point
returns to NORMAL, and no tracked quantity is a throughput. Each needs a
decision on its own task, not an invented record.

**Eviction.** `AlarmHistory` is bounded, so a run longer than its capacity
undercounts alarms and trips and may lose the first alarm. Size the history
for the run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.alarms.history import AlarmHistory
from app.alarms.manager import Event
from app.envelope.evaluator import Severity
from app.envelope.tracker import Excursion, ExcursionTracker
from app.scoring.actionlog import ActionEvent, ActionLog

_NON_NORMAL: tuple[Severity, ...] = (Severity.WARNING, Severity.ALARM, Severity.TRIP)


@dataclass(frozen=True)
class RunMetrics:
    alarm_count: int
    trip_count: int
    time_to_recognise_s: float | None
    peak_excursions: Mapping[str, Excursion | None]
    time_outside_envelope_s: float
    unnecessary_actions: int


def compute_metrics(
    actions: ActionLog,
    alarms: AlarmHistory,
    trackers: Mapping[str, ExcursionTracker],
    is_relevant: Callable[[ActionEvent], bool],
) -> RunMetrics:
    events = [entry for entry in alarms.entries() if isinstance(entry, Event)]
    first_alarm = min((event.sim_time for event in events), default=None)

    return RunMetrics(
        alarm_count=len(events),
        trip_count=sum(1 for event in events if event.data.get("severity") == Severity.TRIP.name),
        time_to_recognise_s=_time_to_recognise(actions, first_alarm, is_relevant),
        peak_excursions=MappingProxyType(
            {label: tracker.peak for label, tracker in trackers.items()}
        ),
        time_outside_envelope_s=sum(
            tracker.time_in(severity) for tracker in trackers.values() for severity in _NON_NORMAL
        ),
        unnecessary_actions=sum(1 for action in actions if not is_relevant(action)),
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
