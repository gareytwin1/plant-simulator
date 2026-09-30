"""
Scenario lifecycle - contract C8's scenario half, run end to end (T14-4).

A scenario is loaded, armed, run and completed, and every step of that is one
`ScenarioRunner`:

    IDLE -> LOADED -> RUNNING -> COMPLETE
                 \\        \\
                  +--------+--> ABORTED

    load    reads the scenario file, builds a fresh engine of its plant,
            restores the named initial condition onto it (with the
            scenario's overrides), registers its malfunctions and compiles
            its triggers and objectives against that plant. Every condition
            is resolved against the restored snapshot now, so a scenario that
            names a tag the plant lacks is refused here, not partway through
            a run. **Loading is arming, and arming moves nothing:** no step
            is taken, no malfunction is written, no time passes. It is
            refused whole - a failed load leaves the previous run untouched.
    start   begins the run: malfunctions whose onset is time zero take
            effect, and from here `step` advances the plant.
    step    one engine step, then - on the snapshot it published, never
            inside the step - malfunctions, triggers and objectives, in that
            order, exactly as `MalfunctionRegistry.update` is meant to run.
            Once every objective has ended the run is COMPLETE and the plant
            stops advancing.
    abort   reverts every malfunction and rebuilds the plant from the same
            initial condition, so the plant stands exactly as it did when
            armed. Refused once the run has completed: a result is not
            discarded by accident.

**Scenario time starts at zero, wherever the initial condition's clock stood.**
A saved condition carries the sim time it was captured at, but a scenario's
`time_limit_s`, its trigger times and its malfunction onsets all mean seconds
since the scenario began. Conditions, objectives and malfunctions therefore
read a copy of each snapshot with `sim_time` rebased to the scenario's start;
the plant itself, its clock and its envelope history are never rewritten, and
the snapshot `step` returns to a consumer is the engine's own.

`ScenarioRunner` is `Steppable` (`app.engine.scheduler`): a `Scheduler` can
drive it like an engine. Before `start` and after completion `step` publishes
the current snapshot without advancing anything, the way a paused engine does.

The game layer reaches the physics only as `Malfunction` allows: through its
allowlist of engineer-changeable parameters, and through the same operator
actions a person has (`app.api.action`, against `runner.engine` and
`runner.actions`). A scenario's "expected trip" is an objective's `failure`
condition, since trips are not evaluated in a live session yet (see
project_state.md).

**Every input that moves a run is journaled** (`inputs()`), so
`app.scenarios.replay` can play it back: a `Tick` for each `start`, advancing
`step` and `abort` that completed, carrying how many logged actions preceded it, and for a
step the dt and the clock's speed and pause it ran with. The actions
themselves are the `ActionLog`'s, not copied here - the log stays the single
record of intent, and the count is what orders each action against the
ticks around it. A step that advances nothing (before `start`, after
completion) is not an input and is not journaled.

Not here: `seed` is carried into the result and drives nothing - the plant
has no random source yet. Objective and trigger results are as
`ObjectiveEvaluator` and `TriggerEvaluator` report them; scoring and the
debrief text are M15.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import json
import math
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from app.api.action import apply_action
from app.disturbances.malfunction import AtTime, Malfunction, MalfunctionRegistry, Step
from app.engine.engine import Engine
from app.engine.persistence import restore_state
from app.engine.snapshot import Snapshot
from app.equipment.registry import EquipmentRegistry
from app.plant.loader import CONFIG_SUFFIXES, load_plant_file
from app.plant.validate import validate
from app.scenarios.objectives import ObjectiveEvaluator, ObjectiveResult, ObjectiveStatus
from app.scenarios.triggers import TriggerEvaluator
from app.scoring.actionlog import ActionLog
from app.statetypes import JSONValue


ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config"
SCENARIO_SCHEMA = CONFIG / "schema" / "scenario.schema.json"

# A name from a request or a scenario file becomes a file name. Restricting it
# to this alphabet is what keeps "../" and an absolute path out of the lookup.
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class ScenarioNotFound(LookupError):
    """No scenario, plant or initial condition of that name exists."""


class ScenarioConfigError(ValueError):
    """A scenario file that cannot be loaded, named for what is wrong."""


class ScenarioStateError(ValueError):
    """A lifecycle call the runner's current phase does not allow.

    A `ValueError` so the C5 action route, which answers one for any action
    it refuses, answers 400 for an action with no scenario loaded rather than
    failing with a 500."""


class Phase(str, Enum):
    IDLE = "idle"
    LOADED = "loaded"
    RUNNING = "running"
    COMPLETE = "complete"
    ABORTED = "aborted"


class Outcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


@dataclass(frozen=True)
class ScenarioLibrary:
    """Where a scenario, its plant and its initial condition are read from."""

    scenarios: Path = CONFIG / "scenarios"
    plants: Path = CONFIG / "plants"
    conditions: Path = CONFIG / "initial_conditions"

    def scenario(self, name: str) -> Any:
        # Any: decoded YAML or JSON of a shape only the scenario schema knows.
        return _read_document(_find(self.scenarios, name, CONFIG_SUFFIXES, "scenario"))

    def plant_path(self, name: str) -> Path:
        return _find(self.plants, name, CONFIG_SUFFIXES, "plant")

    def condition(self, name: str) -> dict[str, JSONValue]:
        """The named initial condition, as `capture_state` saved it."""
        document = _read_document(_find(self.conditions, name, (".json",), "initial condition"))

        if not isinstance(document, dict):
            raise ScenarioConfigError(f"initial condition {name!r} is not a JSON object")

        return document


def _find(directory: Path, name: str, suffixes: tuple[str, ...], kind: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ScenarioNotFound(f"{kind} name {name!r} is not a plain name (letters, digits, - and _)")

    for suffix in suffixes:
        path = directory / f"{name}{suffix}"

        if path.is_file():
            return path

    raise ScenarioNotFound(f"no {kind} named {name!r} in {directory}")


def _read_document(path: Path) -> Any:
    try:
        with open(path) as f:
            if path.suffix.lower() == ".json":
                return json.load(f)

            return yaml.safe_load(f)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise ScenarioConfigError(f"{path}: not parseable: {error}") from error


@functools.cache
def _schema() -> Any:
    return json.loads(SCENARIO_SCHEMA.read_text())


def apply_overrides(
    state: dict[str, JSONValue],
    overrides: Mapping[str, Any],
) -> dict[str, JSONValue]:
    """Layer a scenario's `overrides` onto a saved state, in place.

    A key is `TAG.attribute` and names a field the saved device row already
    has, so an override adjusts a device and never invents one. Whether the
    value is one the device accepts is `restore_state`'s to say: it validates
    the whole state, overrides included, before anything is written.
    """
    equipment = state["equipment"]
    assert isinstance(equipment, dict)  # a library file is a capture_state save

    for key, value in overrides.items():
        tag, dot, attribute = key.partition(".")
        row = equipment.get(tag) if dot else None

        if not isinstance(row, dict) or attribute not in row:
            raise ScenarioConfigError(
                f"initial_condition override {key!r} does not name a saved "
                f"'TAG.attribute' of this condition",
            )

        row[attribute] = value

    return state


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioConfigError(f"{what} must be a number, got {value!r}")

    if not math.isfinite(value) or value < 0:
        raise ScenarioConfigError(f"{what} must be a finite number of 0 or more, got {value!r}")

    return float(value)


def malfunction_from_config(config: Mapping[str, Any]) -> Malfunction:
    """A `Malfunction` from a scenario's malfunction entry.

    Only the profile and start condition `app.disturbances.malfunction` has -
    `step` and `at_time` - are known; a ramp or a condition onset is T13-3's,
    and is refused by name rather than approximated.
    """
    where = f"malfunction {config['target_tag']}.{config['parameter']}"

    profile = config.get("profile", {"type": "step"})
    if profile.get("type") != "step" or set(profile) != {"type"}:
        raise ScenarioConfigError(f"{where} has profile {profile!r}; only {{'type': 'step'}} is supported")

    onset = config.get("start_condition", {"type": "at_time", "sim_time": 0})
    if onset.get("type") != "at_time" or set(onset) - {"type", "sim_time"}:
        raise ScenarioConfigError(
            f"{where} has start_condition {onset!r}; only 'at_time' with a sim_time is supported",
        )

    return Malfunction(
        target_tag=config["target_tag"],
        parameter=config["parameter"],
        value=config["value"],
        profile=Step(),
        start_condition=AtTime(sim_time=_number(onset.get("sim_time", 0), f"{where} sim_time")),
    )


@dataclass(frozen=True)
class ScenarioResult:
    """Where a run stands, and how it ended once it has.

    Every time is scenario time: seconds since the run began, not the plant
    clock the initial condition was saved at.
    """

    scenario_id: str
    phase: Phase
    outcome: Outcome | None
    elapsed_s: float
    time_limit_s: float
    difficulty: str
    seed: int
    objectives: tuple[ObjectiveResult, ...]
    triggers_fired: Mapping[str, float]
    actions: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        # Any: a JSON document for the API, whose values are all JSON-safe.
        return {
            "scenario_id": self.scenario_id,
            "phase": self.phase.value,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "elapsed_s": self.elapsed_s,
            "time_limit_s": self.time_limit_s,
            "difficulty": self.difficulty,
            "seed": self.seed,
            "objectives": [
                {"id": r.id, "status": r.status.value, "ended_at": r.ended_at}
                for r in self.objectives
            ],
            "triggers_fired": dict(self.triggers_fired),
            "actions": [dict(action) for action in self.actions],
        }


class TickKind(str, Enum):
    START = "start"
    STEP = "step"
    ABORT = "abort"


@dataclass(frozen=True)
class Tick:
    """One lifecycle input of a run, after `actions` logged actions.

    `dt`, `speed` and `paused` are a step's: the dt it was called with and
    the clock state it ran under, which together fix the elapsed time the
    clock applied bit for bit. They are None on a start or an abort.
    """

    kind: TickKind
    actions: int
    dt: float | None = None
    speed: float | None = None
    paused: bool | None = None


@dataclass(frozen=True)
class RunInputs:
    """Everything a run has taken as input, at one instant: its scenario
    document, its journal, its actions (as `ScenarioResult.actions` has
    them, on scenario time) and the clock's speed and pause as they stand
    now, which a change after the last step leaves no tick to carry."""

    config: Any  # Any: a scenario document, of the shape the scenario schema allows
    journal: tuple[Tick, ...]
    actions: tuple[Mapping[str, Any], ...]
    speed: float
    paused: bool


@dataclass
class _Run:
    """Everything one loaded scenario owns."""

    config: Any  # Any: the scenario document as loaded, validated by the schema
    scenario_id: str
    difficulty: str
    seed: int
    time_limit_s: float
    build: Callable[[], Engine]
    armed_state: dict[str, JSONValue]
    engine: Engine
    origin: float
    actions: ActionLog
    malfunctions: MalfunctionRegistry
    triggers: TriggerEvaluator
    objectives: ObjectiveEvaluator
    objective_ids: tuple[str, ...]
    phase: Phase = Phase.LOADED
    outcome: Outcome | None = None
    ended_at: float | None = None
    results: tuple[ObjectiveResult, ...] = ()
    fired: dict[str, float] = field(default_factory=dict)
    journal: list[Tick] = field(default_factory=list)

    def view(self, snapshot: Snapshot) -> Snapshot:
        """`snapshot` on the scenario's own clock."""
        return dataclasses.replace(snapshot, sim_time=snapshot.sim_time - self.origin)


class ScenarioRunner:
    """One session's scenario: load it, run it, read how it went.

    Every public method holds one lock, so a `Scheduler` worker stepping the
    runner and a request loading, starting or aborting it never interleave.
    The lock is taken last (after a scheduler's `step_lock`) and nothing here
    calls back out to one, so it cannot invert that order.
    """

    def __init__(self, library: ScenarioLibrary | None = None) -> None:
        self._library = library if library is not None else ScenarioLibrary()
        self._lock = threading.RLock()
        self._run: _Run | None = None

    @property
    def phase(self) -> Phase:
        with self._lock:
            return self._run.phase if self._run is not None else Phase.IDLE

    @property
    def engine(self) -> Engine:
        """The loaded scenario's plant, for reading. To act on it, use `act`:
        a reference taken here outlives the lock and the run, since an abort
        replaces the engine."""
        with self._lock:
            return self._loaded().engine

    @property
    def actions(self) -> ActionLog:
        """Every operator action taken in the loaded run."""
        with self._lock:
            return self._loaded().actions

    def inputs(self) -> RunInputs:
        """Every input the loaded run has taken, read under one lock so no
        step or action can land between its parts."""
        with self._lock:
            run = self._loaded()
            clock = run.engine.clock

            return RunInputs(
                config=copy.deepcopy(run.config),
                journal=tuple(run.journal),
                actions=_actions(run),
                speed=clock.speed,
                paused=clock.paused,
            )

    def act(self, target: str, action: str, value: float | None) -> None:
        """Apply one operator action to the live run and log it, under the
        lock that `step` and `abort` take, so it can never interleave with
        either, and refused once the run is over so its result stays final.
        Otherwise the same refusals as `app.api.action.apply_action`."""
        with self._lock:
            run = self._loaded()

            if run.phase not in (Phase.LOADED, Phase.RUNNING):
                raise ScenarioStateError(
                    f"cannot act on a scenario that is {run.phase.value}; its result is final",
                )

            apply_action(
                run.engine.equipment,
                run.actions,
                run.engine.clock.sim_time,
                target,
                action,
                value,
            )

    def load(self, scenario_id: str) -> ScenarioResult:
        return self.load_config(self._library.scenario(scenario_id))

    def load_config(self, config: Any) -> ScenarioResult:
        # Any: a decoded scenario file, validated against the schema below.
        with self._lock:
            if self._run is not None and self._run.phase is Phase.RUNNING:
                raise ScenarioStateError("a scenario is running; abort it before loading another")

            self._run = self._arm(config)

            return self._result(self._run)

    def start(self) -> ScenarioResult:
        with self._lock:
            run = self._loaded()

            if run.phase is not Phase.LOADED:
                raise ScenarioStateError(f"cannot start a scenario that is {run.phase.value}; load one first")

            run.phase = Phase.RUNNING
            # A malfunction due at time zero takes effect now rather than a
            # step late, so the first step already runs the disturbed plant.
            run.malfunctions.update(run.view(run.engine.snapshot()))
            run.journal.append(Tick(TickKind.START, len(run.actions)))

            return self._result(run)

    def step(self, dt: float) -> Snapshot:
        with self._lock:
            run = self._loaded()

            if run.phase is not Phase.RUNNING:
                return run.engine.snapshot()

            clock = run.engine.clock
            tick = Tick(TickKind.STEP, len(run.actions), dt=dt, speed=clock.speed, paused=clock.paused)

            snapshot = run.engine.step(dt)
            self._observe(run, snapshot)
            run.journal.append(tick)

            return snapshot

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._loaded().engine.snapshot()

    def abort(self) -> ScenarioResult:
        with self._lock:
            run = self._loaded()

            if run.phase not in (Phase.LOADED, Phase.RUNNING):
                raise ScenarioStateError(f"cannot abort a scenario that is {run.phase.value}")

            elapsed = run.engine.snapshot().sim_time - run.origin

            run.malfunctions.revert_all()
            run.engine = self._restored(run.build, run.armed_state)
            run.phase = Phase.ABORTED
            run.outcome = Outcome.ABORTED
            run.ended_at = elapsed
            run.journal.append(Tick(TickKind.ABORT, len(run.actions)))

            return self._result(run)

    def result(self) -> ScenarioResult:
        with self._lock:
            return self._result(self._loaded())

    def _loaded(self) -> _Run:
        if self._run is None:
            raise ScenarioStateError("no scenario is loaded")

        return self._run

    def _arm(self, config: Any) -> _Run:
        errors = validate(config, _schema())
        if errors:
            raise ScenarioConfigError("; ".join(errors))

        scenario_id = config["id"]
        plant_path = self._library.plant_path(config["plant"])
        condition = config["initial_condition"]

        state = self._library.condition(condition["condition"])
        apply_overrides(state, condition.get("overrides", {}))

        def build() -> Engine:
            return Engine.from_plant(load_plant_file(plant_path))

        engine = self._restored(build, state)
        armed = engine.snapshot()

        registry = EquipmentRegistry()
        for device in engine.equipment.values():
            registry.register(device)

        malfunctions = MalfunctionRegistry(registry, engine.instruments.values())
        for entry in config.get("malfunctions", []):
            malfunctions.add(malfunction_from_config(entry))

        time_limit = float(config["time_limit_s"])
        triggers = TriggerEvaluator.from_config(config.get("triggers", []))
        objectives = ObjectiveEvaluator.from_config(config.get("objectives", []), time_limit)

        triggers.validate(armed)
        objectives.validate(armed)

        return _Run(
            config=copy.deepcopy(config),
            scenario_id=scenario_id,
            difficulty=config["difficulty"],
            seed=config["seed"],
            time_limit_s=time_limit,
            build=build,
            armed_state=state,
            engine=engine,
            origin=armed.sim_time,
            actions=ActionLog(),
            malfunctions=malfunctions,
            triggers=triggers,
            objectives=objectives,
            objective_ids=tuple(entry["id"] for entry in config.get("objectives", [])),
        )

    @staticmethod
    def _restored(build: Callable[[], Engine], state: dict[str, JSONValue]) -> Engine:
        engine = build()
        restore_state(engine, state)

        return engine

    @staticmethod
    def _observe(run: _Run, snapshot: Snapshot) -> None:
        view = run.view(snapshot)

        run.malfunctions.update(view)

        for trigger_id in run.triggers.evaluate(view, run.actions):
            run.fired.setdefault(trigger_id, view.sim_time)

        run.results = run.objectives.evaluate(view)

        # A scenario with no objectives has nothing to resolve, so it runs
        # its time limit out rather than ending on the first step.
        if run.objective_ids:
            finished = run.objectives.resolved
        else:
            finished = view.sim_time >= run.time_limit_s

        if not finished:
            return

        run.phase = Phase.COMPLETE
        run.ended_at = view.sim_time
        run.outcome = _outcome(run.results)

    @staticmethod
    def _result(run: _Run) -> ScenarioResult:
        if run.ended_at is not None:
            elapsed = run.ended_at
        else:
            elapsed = run.engine.snapshot().sim_time - run.origin

        return ScenarioResult(
            scenario_id=run.scenario_id,
            phase=run.phase,
            outcome=run.outcome,
            elapsed_s=elapsed,
            time_limit_s=run.time_limit_s,
            difficulty=run.difficulty,
            seed=run.seed,
            objectives=run.results,
            triggers_fired=dict(run.fired),
            actions=_actions(run),
        )


def _actions(run: _Run) -> tuple[Mapping[str, Any], ...]:
    # Any: a JSON-safe action row, as `ScenarioResult.as_dict` sends it.
    return tuple(
        {
            "sim_time": event.sim_time - run.origin,
            "tag": event.tag,
            "action": event.data["action"],
            "value": event.data["value"],
        }
        for event in run.actions
    )


def _outcome(results: tuple[ObjectiveResult, ...]) -> Outcome:
    """A failure outranks a timeout, which outranks success: one lost
    objective is a lost run."""
    statuses = {result.status for result in results}

    if ObjectiveStatus.FAILED in statuses:
        return Outcome.FAILED

    if ObjectiveStatus.TIMED_OUT in statuses or not statuses:
        return Outcome.TIMED_OUT

    return Outcome.SUCCEEDED
