"""
Plant runtime (T16-6) - trips before each step, alarms after it.

Trips (T11-2) and alarms (T10-2) are built and tested, but nothing outside the
tests runs them against a running plant. `PlantRuntime` wraps one `Engine` and
runs both around its step, so free play and a scenario run get the same
protection and the same alarm record. It is `Steppable` (`app.engine.scheduler`),
so a `Scheduler` drives it exactly as it drives an engine.

`step(dt)` runs three things in order:

1. `TripSystem.update` on the plant as it stands now - the snapshot the last
   step published, or a fresh one when an operator action has landed since -
   immediately before the step, as `app.safety.actions` requires.
2. `Engine.step(dt)`.
3. The alarm pipeline on the snapshot that step returned.

**The snapshot it returns annunciates every interlock (T20-3).** `step` and
`snapshot` fill the `interlocks` section the engine leaves empty from
`TripSystem.status`, so a reset shows at once and a trip latched in step 1
shows in the snapshot that step publishes.

**The alarm pipeline is fed one `EnvelopeEvent` per configured limit on every
step.** The snapshot's `envelope` section lists only the points currently
outside their limits, so a point that is absent reads NORMAL, which is how
`AlarmManager` learns of a return to normal. `AlarmManager` already ignores an
unchanged band, so feeding every key each step is correct and records nothing
new. The band label in the snapshot (`hi`, `hihi`, ...) is turned back into a
severity and side with the inverse of `app.envelope.evaluator.isa_band`; the
engine's private state is never read. The pipeline also runs once at
construction, so a plant that starts outside a limit raises its alarm before
the first step.

**An operator's input is one call, `act`.** A device action goes through
`app.api.action.apply_action` and is logged. An interlock tag accepts only
`reset`, which clears a tripped interlock whose condition has cleared. A reset
that would take no effect is refused with a `ValueError` and not logged, like
any refused action, so the console can say why. The trip system releases the
interlock's demands on the next step.

**Every step is also recorded for the trend API (T17-3).** `trends` is one
`Historian`, and `_observe` records every point of the operator view
(`app.historian.points`) at the snapshot's `sim_time`, the construction
snapshot included. Recording lives here, never in `Engine`, and changes no
plant state. A rebuilt runtime (a scenario abort) starts a fresh history.

**Scoring trackers are fed here too (T20-15).** Given a `PlantScoring`, the
runtime keeps one `ExcursionTracker` per configured excursion and one
`ThroughputTracker` per throughput point, and `_observe` feeds each from the
snapshot it publishes - the construction snapshot included, with no elapsed
time - by the sim time since the last one. An excursion's severity is the
snapshot's envelope band, as the alarm pipeline reads it, and its value and a
throughput's are the snapshot's equipment row. A label that names no limit of
this plant, or no number on an equipment row, is refused at construction.
`metrics` reads the action log, the alarm history, the trip records (an
interlock latching, noted around each trip update) and the trackers as they
stand. Like recording, feeding changes no plant state and never runs in
`Engine`.

**One lock covers step, act, acknowledge and the history read**, so an
acknowledge can never land between a step's events and their record.

Not here: interlock state in `capture_state`, restart permissives (no C3
key), and filling the snapshot's `alarms` field, which is a C4 change.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import fields, replace

from app import config
from app.alarms.acknowledge import Acknowledged, acknowledge_alarm
from app.alarms.history import AlarmHistory, HistoryEntry
from app.alarms.manager import AlarmManager, EnvelopeEvent
from app.api.action import UnknownAction, apply_action
from app.api.plant import describe
from app.api.visibility import descriptor, operator_view
from app.engine.engine import Engine
from app.engine.snapshot import Snapshot, interlock_section
from app.envelope.evaluator import Evaluator, Severity, Side, isa_band
from app.envelope.tracker import ExcursionTracker
from app.historian.buffer import Historian, Sample
from app.historian.points import trend_values
from app.plant.loader import Plant
from app.plant.units import point_units
from app.safety.actions import TripSystem
from app.scoring.actionlog import ActionEvent, ActionLog
from app.scoring.metrics import RunMetrics, TripRecord, compute_metrics
from app.scoring.score import PlantScoring
from app.scoring.throughput import ThroughputTracker
from app.statetypes import JSONValue

RESET = "reset"

_BANDS: dict[str, tuple[Severity, Side]] = {
    isa_band(severity, side): (severity, side)
    for severity in (Severity.WARNING, Severity.ALARM, Severity.TRIP)
    for side in ("lo", "hi")
}


def _band_of(label: object, point: str) -> tuple[Severity, Side]:
    band = _BANDS.get(label) if isinstance(label, str) else None

    if band is None:
        raise ValueError(f"envelope point {point} carries an unknown band {label!r}")

    return band


def _reading(snapshot: Snapshot, label: str) -> float:
    """The number `label` (`<tag>.<field>`) names on an equipment row."""
    tag, _, name = label.partition(".")
    value = snapshot.equipment.get(tag, {}).get(name)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"scoring point {label} names no number on an equipment row of this plant")

    return float(value)


class PlantRuntime:
    def __init__(
        self,
        engine: Engine,
        plant: Plant,
        alarm_history_capacity: int = config.ALARM_HISTORY_CAPACITY,
        alarms: AlarmManager | None = None,
        scoring: PlantScoring | None = None,
    ) -> None:
        self.engine = engine
        self.trips = TripSystem.from_plant(plant, engine.equipment, engine.arbiter, engine.snapshot())
        self.alarms = AlarmManager() if alarms is None else alarms
        self.history = AlarmHistory(alarm_history_capacity)
        self.actions = ActionLog()
        self._trip_records: list[TripRecord] = []
        self.trends = Historian(config.TREND_CAPACITY, config.TREND_SAMPLE_PERIOD_SECONDS)
        self._trend_points = tuple(sorted(self._trend_values(engine.snapshot())))
        self._trend_descriptors = {
            point: descriptor(engine.equipment.get(row_id), field)
            for point in self._trend_points
            for row_id, _, field in [point.rpartition(".")]
        }
        self._trend_units = point_units(
            operator_view(engine.snapshot(), engine.equipment),
            engine.equipment,
            engine.loops,
            engine.topologies,
            engine.couplings,
        )

        self._description = describe(plant, engine, self.trips.actions)

        self._lock = threading.RLock()
        self._published: Snapshot | None = None

        scoring = PlantScoring() if scoring is None else scoring
        self._excursions = {label: ExcursionTracker(self._limit(label).limits) for label in scoring.excursions}
        self._throughput = {label: ThroughputTracker(target) for label, target in scoring.throughput.items()}
        self._tracked_at: float | None = None
        first = engine.snapshot()

        for label in (*self._excursions, *self._throughput):
            _reading(first, label)

        self._observe(first)

    @classmethod
    def from_plant(cls, plant: Plant) -> PlantRuntime:
        return cls(Engine.from_plant(plant), plant)

    def step(self, dt: float) -> Snapshot:
        with self._lock:
            before = self._published if self._published is not None else self.engine.snapshot()
            latched_before = set(self.trips.tripped)
            self.trips.update(before)
            self._trip_records.extend(
                TripRecord(before.sim_time, tag)
                for tag in self.trips.tripped
                if tag not in latched_before
            )

            snapshot = self._annunciated(self.engine.step(dt))
            self._published = snapshot
            self._observe(snapshot)

            return snapshot

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._annunciated(self.engine.snapshot())

    def _annunciated(self, snapshot: Snapshot) -> Snapshot:
        """`snapshot` with the trip system's interlock states filled in. The
        engine builds the section empty; only the runtime knows of trips."""
        return replace(snapshot, interlocks=interlock_section(self.trips.status()))

    def act(self, target: str, action: str, value: float | None) -> None:
        """Apply one operator action and log it. Raises `KeyError`,
        `UnknownAction` or `ValueError`, exactly as `apply_action` does."""
        with self._lock:
            sim_time = self.engine.clock.sim_time

            if target in self.trips.interlocks:
                if action != RESET:
                    raise UnknownAction(f"{target} accepts only {RESET!r}, got {action!r}")

                if value is not None:
                    raise ValueError(f"{target}.{RESET} takes no value, got {value!r}")

                interlock = self.trips.interlocks[target]

                if not interlock.tripped:
                    raise ValueError(f"{target} was not reset: it is not tripped")

                if interlock.definition.reset != "manual":
                    raise ValueError(f"{target} was not reset: it resets itself once its condition clears")

                if not interlock.reset():
                    raise ValueError(f"{target} was not reset: its condition still holds")

                self.actions.record(tag=target, action=RESET, value=None, sim_time=sim_time)
            else:
                apply_action(
                    self.engine.equipment, self.actions, sim_time, target, action, value,
                    loops=self.engine.loops,
                )

            self._published = None

    def trip_records(self) -> tuple[TripRecord, ...]:
        """Each interlock latch so far, in order. A tag that is reset and
        latches again appears twice; one that stays latched appears once."""
        with self._lock:
            return tuple(self._trip_records)

    def metrics(self, is_relevant: Callable[[ActionEvent], bool], outcome: str) -> RunMetrics:
        """`compute_metrics` over this runtime's action log, alarm history,
        trip records and scoring trackers as they stand now, labelled as
        configured. `outcome` is how the run ended, as a plain string."""
        with self._lock:
            return compute_metrics(
                self.actions,
                self.history,
                self._excursions,
                is_relevant,
                self._throughput,
                trips=self._trip_records,
                outcome=outcome,
            )

    def acknowledge(self, alarm_id: str) -> Acknowledged:
        with self._lock:
            return acknowledge_alarm(
                self.alarms, self.history, alarm_id, self.engine.clock.sim_time,
            )

    def alarm_entries(self) -> tuple[HistoryEntry, ...]:
        with self._lock:
            return self.history.entries()

    def trend_points(self) -> tuple[str, ...]:
        """Every point `trend_history` can answer for, sorted. Fixed at
        construction."""
        return self._trend_points

    def trend_descriptors(self) -> dict[str, str]:
        """The operator's word for each trend point, keyed by point. An
        equipment point takes its class's descriptor; any other (node, stream,
        controller) its field with underscores as spaces. Fixed at
        construction."""
        return self._trend_descriptors

    def trend_units(self) -> dict[str, str]:
        """The unit of each trend point, keyed by point (`app.plant.units`):
        '' where nothing names one, and `fraction` for a 0..1 value a display
        shows as a percent. Fixed at construction."""
        return self._trend_units

    def plant_description(self) -> dict[str, JSONValue]:
        """The `equipment`, `controllers` and `interlocks` sections of
        `GET /api/plant` (`app.api.plant.describe`). Fixed at construction."""
        return self._description

    def trend_limits(self) -> dict[str, dict[str, float]]:
        """The configured bounds of each trend point the engine evaluates
        (`Engine.limits`), keyed by point and holding only the bounds that are
        set, under the `Limits` field names. A limit the engine cannot resolve
        is not in `Engine.limits`, so it draws no band."""
        known = set(self._trend_points)
        limits: dict[str, dict[str, float]] = {}

        for (tag, variable), evaluator in self.engine.limits.items():
            point = f"{tag}.{variable}"

            if point not in known:
                continue

            limits[point] = {
                field.name: bound
                for field in fields(evaluator.limits)
                if (bound := getattr(evaluator.limits, field.name)) is not None
            }

        return limits

    def trend_history(self, points: Sequence[str]) -> dict[str, tuple[Sample, ...]]:
        """The retained samples of each of `points`, oldest first. Raises
        `KeyError` naming the first point this plant does not publish."""
        known = set(self._trend_points)

        for point in points:
            if point not in known:
                raise KeyError(point)

        with self._lock:
            return {point: self.trends.history(point) for point in points}

    def _limit(self, label: str) -> Evaluator:
        tag, _, variable = label.partition(".")
        evaluator = self.engine.limits.get((tag, variable))

        if evaluator is None:
            raise ValueError(f"scoring excursion {label} names no configured limit of this plant")

        return evaluator

    def _track(self, snapshot: Snapshot) -> None:
        dt = 0.0 if self._tracked_at is None else snapshot.sim_time - self._tracked_at
        self._tracked_at = snapshot.sim_time

        for label, excursion in self._excursions.items():
            row = snapshot.envelope.get(label)
            severity = Severity.NORMAL if row is None else _band_of(row.get("band"), label)[0]
            excursion.update(_reading(snapshot, label), severity, dt)

        for label, throughput in self._throughput.items():
            throughput.update(_reading(snapshot, label), dt)

    def _trend_values(self, snapshot: Snapshot) -> dict[str, float]:
        return trend_values(operator_view(snapshot, self.engine.equipment))

    def _observe(self, snapshot: Snapshot) -> None:
        for point, value in self._trend_values(snapshot).items():
            self.trends.record(point, snapshot.sim_time, value)

        events: list[EnvelopeEvent] = []

        for tag, variable in self.engine.limits:
            point = f"{tag}.{variable}"
            row = snapshot.envelope.get(point)
            pv = descriptor(self.engine.equipment.get(tag), variable)

            if row is None:
                events.append(EnvelopeEvent(tag=tag, pv=pv, severity=Severity.NORMAL, point=point))
                continue

            severity, side = _band_of(row.get("band"), point)
            events.append(EnvelopeEvent(tag=tag, pv=pv, severity=severity, side=side, point=point))

        raised = self.alarms.evaluate(events, snapshot.sim_time)
        self.history.record_events(raised)
        self.history.record_clears(self.alarms.last_cleared(), snapshot.sim_time)

        self._track(snapshot)
