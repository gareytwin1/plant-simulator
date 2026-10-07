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
`reset`, which clears a tripped interlock whose condition has cleared, and is
logged either way: a reset that took no effect is still something the operator
did. The trip system releases the interlock's demands on the next step.

**One lock covers step, act, acknowledge and the history read**, so an
acknowledge can never land between a step's events and their record.

Not here: interlock state in `capture_state`, trips on a solved node value
(`PSHH-101` is still warned about and skipped, the tag-to-point resolver gap),
restart permissives (no C3 key), and filling the snapshot's `alarms` field,
which is a C4 change.
"""

from __future__ import annotations

import threading

from app import config
from app.alarms.acknowledge import Acknowledged, acknowledge_alarm
from app.alarms.history import AlarmHistory, HistoryEntry
from app.alarms.manager import AlarmManager, EnvelopeEvent
from app.api.action import UnknownAction, apply_action
from app.engine.engine import Engine
from app.engine.snapshot import Snapshot
from app.envelope.evaluator import Severity, Side, isa_band
from app.plant.loader import Plant
from app.safety.actions import TripSystem
from app.scoring.actionlog import ActionLog

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


class PlantRuntime:
    def __init__(
        self,
        engine: Engine,
        plant: Plant,
        alarm_history_capacity: int = config.ALARM_HISTORY_CAPACITY,
        alarms: AlarmManager | None = None,
    ) -> None:
        self.engine = engine
        self.trips = TripSystem.from_plant(plant, engine.equipment, engine.arbiter, engine.snapshot())
        self.alarms = AlarmManager() if alarms is None else alarms
        self.history = AlarmHistory(alarm_history_capacity)
        self.actions = ActionLog()

        self._lock = threading.RLock()
        self._published: Snapshot | None = None

        self._observe(engine.snapshot())

    @classmethod
    def from_plant(cls, plant: Plant) -> PlantRuntime:
        return cls(Engine.from_plant(plant), plant)

    def step(self, dt: float) -> Snapshot:
        with self._lock:
            before = self._published if self._published is not None else self.engine.snapshot()
            self.trips.update(before)

            snapshot = self.engine.step(dt)
            self._published = snapshot
            self._observe(snapshot)

            return snapshot

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self.engine.snapshot()

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

                self.trips.interlocks[target].reset()
                self.actions.record(tag=target, action=RESET, value=None, sim_time=sim_time)
            else:
                apply_action(
                    self.engine.equipment, self.actions, sim_time, target, action, value,
                    loops=self.engine.loops,
                )

            self._published = None

    def acknowledge(self, alarm_id: str) -> Acknowledged:
        with self._lock:
            return acknowledge_alarm(
                self.alarms, self.history, alarm_id, self.engine.clock.sim_time,
            )

    def alarm_entries(self) -> tuple[HistoryEntry, ...]:
        with self._lock:
            return self.history.entries()

    def _observe(self, snapshot: Snapshot) -> None:
        events: list[EnvelopeEvent] = []

        for tag, variable in self.engine.limits:
            point = f"{tag}.{variable}"
            row = snapshot.envelope.get(point)
            pv = variable.replace("_", " ")

            if row is None:
                events.append(EnvelopeEvent(tag=tag, pv=pv, severity=Severity.NORMAL))
                continue

            severity, side = _band_of(row.get("band"), point)
            events.append(EnvelopeEvent(tag=tag, pv=pv, severity=severity, side=side))

        raised = self.alarms.evaluate(events, snapshot.sim_time)
        self.history.record_events(raised)
        self.history.record_clears(self.alarms.last_cleared(), snapshot.sim_time)
