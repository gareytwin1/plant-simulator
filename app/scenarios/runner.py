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
    unload  forgets the loaded run and returns the runner to IDLE. Refused
            while RUNNING, as `load` is.

**Scenario time starts at zero, wherever the initial condition's clock stood.**
A saved condition carries the sim time it was captured at, but a scenario's
`time_limit_s`, its trigger times and its malfunction onsets all mean seconds
since the scenario began. Conditions, objectives and malfunctions therefore
read a copy of each snapshot with `sim_time` rebased to the scenario's start;
the plant itself, its clock and its envelope history are never rewritten, and
the snapshot `step` returns to a consumer is the engine's own.

**A run steps a `PlantRuntime` (`app.training.runtime`), not a bare engine,**
so it is protected by the plant's trips and raises and records alarms exactly
as free play does. Every arm and every abort builds a fresh runtime *after*
the initial condition is restored, so its trips start from the restored
snapshot and its interlocks and alarms start clean. An interlock `reset` is an
operator action like any other: it goes through `act`, is journaled in the
`ActionLog` and is replayed. The run exposes `alarm_entries` and `acknowledge`
under the runner lock. Acknowledgement is not an input a replay reproduces: it
changes the alarm history only, never the plant. It is accepted in any phase,
so a COMPLETE run's alarms can still be acknowledged in the debrief; an abort
rebuilds the runtime, so it discards the aborted run's alarm history (the
action log is kept).

`ScenarioRunner` is `Steppable` (`app.engine.scheduler`): a `Scheduler` can
drive it like an engine. Before `start` and after completion `step` publishes
the current snapshot without advancing anything, the way a paused engine does.

The game layer reaches the physics only as `Malfunction` allows: through its
allowlist of engineer-changeable parameters, and through the same operator
actions a person has, taken with `ScenarioRunner.act` (an HTTP action route
passes `apply=runner.act`). `runner.engine` and `runner.actions` are for
reading: an action applied to them directly skips the runtime, so it cannot
reset an interlock and the next trip check reads the plant from before it.

A trip fires in a run, and an objective's `failure` condition can read the
plant it moved. Interlock state is not in `capture_state`, so a run armed from
an initial condition starts untripped and `_end_state` ignores interlock state
(the replayed inputs reproduce it).

**Every input that moves a run is journaled** (`inputs()`), so
`app.scenarios.replay` can play it back: a `Tick` for each `start`, advancing
`step` and `abort`, carrying how many logged actions preceded it, and for a
step the dt and the clock's speed and pause it ran with. The actions
themselves are the `ActionLog`'s, not copied here - the log stays the single
record of intent, and the count is what orders each action against the
ticks around it. A step that advances nothing (before `start`, after
completion) is not an input and is not journaled. One that raises is, with
its error: it may already have moved the plant and the run stays live, so a
replay runs it too, expects the same error there, and carries on. Identical
steps in a row - same dt, clock state and action count, none raising - are
one tick with a `repeat` count, so a clock left paused under a `Scheduler`,
which never reaches the time limit, grows the journal by nothing.

Not here: `seed` is carried into the result and drives nothing - the plant
has no random source yet. Objective and trigger results are as
`ObjectiveEvaluator` and `TriggerEvaluator` report them; scoring and the
debrief text are M15.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from app.alarms.acknowledge import Acknowledged
from app.alarms.history import HistoryEntry
from app.disturbances.malfunction import Malfunction, MalfunctionRegistry
from app.disturbances.profiles import profile_from_config, start_condition_from_config
from app.engine.engine import Engine
from app.engine.persistence import capture_state, restore_state
from app.engine.snapshot import Snapshot
from app.historian.buffer import Sample
from app.equipment.registry import EquipmentRegistry
from app.plant.loader import CONFIG_SUFFIXES, Plant, load_plant, read_plant_config
from app.plant.validate import validate
from app.scenarios.objectives import ObjectiveEvaluator, ObjectiveResult, ObjectiveStatus
from app.scenarios.triggers import TriggerEvaluator
from app.scoring.actionlog import ActionLog
from app.statetypes import JSONValue
from app.training.runtime import PlantRuntime


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


class ScenarioChanged(ScenarioConfigError):
    """A scenario's plant file or initial condition is not the one expected."""


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

    def catalogue(self) -> tuple[CatalogueEntry, ...]:
        """Every scenario file, easiest first and then by title. A file that
        fails the schema, or whose `id` is not its file name, raises
        `ScenarioConfigError`, so a bad file is found here rather than hidden.
        It reads every file: build it once, not per request."""
        entries = []

        for path in self.scenarios.iterdir():
            if not path.is_file() or path.suffix.lower() not in CONFIG_SUFFIXES or path.name.startswith("."):
                continue

            document = _read_document(path)
            errors = validate(document, _schema())
            if errors:
                raise ScenarioConfigError(f"{path.name}: " + "; ".join(errors))

            if document["id"] != path.stem:
                raise ScenarioConfigError(f"{path.name}: id {document['id']!r} must equal the file name {path.stem!r}")

            entries.append(
                CatalogueEntry(
                    id=path.stem,
                    key=scenario_key(path.stem),
                    title=document.get("title", path.stem),
                    briefing=document.get("briefing", ""),
                    difficulty=document["difficulty"],
                    time_limit_s=float(document["time_limit_s"]),
                ),
            )

        if len({entry.key for entry in entries}) != len(entries):
            raise ScenarioConfigError("two scenario files share a key")

        return tuple(sorted(entries, key=lambda e: (_DIFFICULTY_ORDER[e.difficulty], e.title, e.id)))

    def plant_path(self, name: str) -> Path:
        return _find(self.plants, name, CONFIG_SUFFIXES, "plant")

    def condition_path(self, name: str) -> Path:
        return _find(self.conditions, name, (".json",), "initial condition")

    def condition(self, name: str) -> dict[str, JSONValue]:
        """The named initial condition, as `capture_state` saved it."""
        path = self.condition_path(name)

        return _decode_condition(path, path.read_bytes())


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def scenario_key(scenario_id: str) -> str:
    """The handle a browser holds for a scenario instead of its id: the first
    12 hex digits of the id's SHA-256. Deterministic, so it is the same across
    restarts and needs no field in the scenario file to keep in sync. It stops
    a trainee reading the cause off the page, not one who hashes guessed ids:
    it is a spoiler guard, not a security boundary."""
    return hashlib.sha256(scenario_id.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class CatalogueEntry:
    """What a scenario list may show of a scenario file before it is run.

    Never the file's `description`: it carries the diagnosis path, and the id
    of a scenario can name its cause, so a page that shows entries shows
    `title`, `briefing` and `difficulty`, and posts `key` back to load one.
    `id` stays server side until the run ends.
    """

    id: str
    key: str
    title: str
    briefing: str
    difficulty: str
    time_limit_s: float


def _decode_condition(path: Path, data: bytes) -> dict[str, JSONValue]:
    try:
        document = json.loads(data)
    except json.JSONDecodeError as error:
        raise ScenarioConfigError(f"{path}: not parseable: {error}") from error

    if not isinstance(document, dict):
        raise ScenarioConfigError(f"initial condition {path.stem!r} is not a JSON object")

    return document


def _plain(name: str, kind: str) -> None:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ScenarioNotFound(f"{kind} name {name!r} is not a plain name (letters, digits, - and _)")


def _find(directory: Path, name: str, suffixes: tuple[str, ...], kind: str) -> Path:
    _plain(name, kind)

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

    The profile and start condition are decoded by `app.disturbances.profiles`;
    anything it does not know is refused by name rather than approximated.
    """
    where = f"malfunction {config['target_tag']}.{config['parameter']}"

    onset = dict(config.get("start_condition", {"type": "at_time", "sim_time": 0}))
    if onset.get("type") == "at_time":
        onset["sim_time"] = _number(onset.get("sim_time", 0), f"{where} sim_time")

    try:
        profile = profile_from_config(config.get("profile", {"type": "step"}), where)
        start_condition = start_condition_from_config(onset, where)
    except ValueError as error:
        raise ScenarioConfigError(str(error)) from None

    return Malfunction(
        target_tag=config["target_tag"],
        parameter=config["parameter"],
        value=config["value"],
        profile=profile,
        start_condition=start_condition,
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

    `error` is set when the call raised (`error_text`): it may already have
    moved the plant, and the run stays live, so it is still an input.

    `repeat` is how many identical steps in a row this one tick stands for.
    """

    kind: TickKind
    actions: int
    dt: float | None = None
    speed: float | None = None
    paused: bool | None = None
    error: str | None = None
    repeat: int = 1


def error_text(error: Exception) -> str:
    """How a `Tick` records the error its call raised. Replay compares it as
    text, so it relies on the determinism rule reaching error messages too:
    one built from anything but the plant's own values (an object's id, a
    wall-clock time) would make a faithful replay read as a divergence."""
    return f"{type(error).__name__}: {error}"


@contextmanager
def _journaling(run: _Run, tick: Tick) -> Iterator[None]:
    """Journal `tick` once its call returns, or with its error once it raises."""
    try:
        yield
    except Exception as error:
        run.journal.append(dataclasses.replace(tick, error=error_text(error)))
        raise

    last = run.journal[-1] if run.journal else None

    if tick.kind is TickKind.STEP and last is not None and dataclasses.replace(last, repeat=1) == tick:
        run.journal[-1] = dataclasses.replace(last, repeat=last.repeat + 1)
    else:
        run.journal.append(tick)


@dataclass(frozen=True)
class RunInputs:
    """Everything a run has taken as input, at one instant: its scenario
    document, its journal, its actions (as `ScenarioResult.actions` has
    them, on scenario time) and the clock's speed and pause as they stand
    now, which a change after the last step leaves no tick to carry.

    `fingerprint` digests what the document names but does not contain -
    the name the document gives the plant file and the initial condition,
    and each file's content as read at load - so a replay can tell when
    either has changed under it, or been swapped for another.

    `end_state` digests where those inputs led: the plant's captured state
    and the run's result. It is what a replay has to reach, so a change in
    the physics or the evaluation code, which no input records, still
    shows."""

    config: Any  # Any: a scenario document, of the shape the scenario schema allows
    fingerprint: str
    journal: tuple[Tick, ...]
    actions: tuple[Mapping[str, Any], ...]
    speed: float
    paused: bool
    end_state: str


@dataclass
class _Run:
    """Everything one loaded scenario owns."""

    config: Any  # Any: the scenario document as loaded, validated by the schema
    fingerprint: str
    scenario_id: str
    difficulty: str
    seed: int
    time_limit_s: float
    build: Callable[[], Plant]
    armed_state: dict[str, JSONValue]
    runtime: PlantRuntime
    origin: float
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

    @property
    def engine(self) -> Engine:
        return self.runtime.engine

    @property
    def actions(self) -> ActionLog:
        return self.runtime.actions

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
                fingerprint=run.fingerprint,
                journal=tuple(run.journal),
                actions=_actions(run),
                speed=clock.speed,
                paused=clock.paused,
                end_state=self._end_state(run),
            )

    def act(self, target: str, action: str, value: float | None) -> float:
        """Apply one operator action to the live run and log it, under the
        lock that `step` and `abort` take, so it can never interleave with
        either, and refused once the run is over so its result stays final.
        Otherwise the same refusals as `app.api.action.apply_action`.

        Returns the scenario time the action was logged at, as
        `ScenarioResult.actions` reports it."""
        with self._lock:
            run = self._loaded()

            if run.phase not in (Phase.LOADED, Phase.RUNNING):
                raise ScenarioStateError(
                    f"cannot act on a scenario that is {run.phase.value}; its result is final",
                )

            sim_time = run.engine.clock.sim_time
            run.runtime.act(target, action, value)

            return sim_time - run.origin

    def acknowledge(self, alarm_id: str) -> Acknowledged:
        """Acknowledge one alarm of the loaded run, under the runner lock."""
        with self._lock:
            return self._loaded().runtime.acknowledge(alarm_id)

    def alarm_entries(self) -> tuple[HistoryEntry, ...]:
        """The loaded run's alarm history, oldest first."""
        with self._lock:
            return self._loaded().runtime.alarm_entries()

    def trend_points(self) -> tuple[str, ...]:
        """The loaded run's trend points, under the runner lock."""
        with self._lock:
            return self._loaded().runtime.trend_points()

    def trend_history(self, points: Sequence[str]) -> dict[str, tuple[Sample, ...]]:
        """The loaded run's samples of `points`, on the engine's clock like
        the snapshots it publishes. `KeyError` for a point it does not have."""
        with self._lock:
            return self._loaded().runtime.trend_history(points)

    def unload(self) -> None:
        """Forget the loaded run, returning the runner to IDLE."""
        with self._lock:
            if self._run is not None and self._run.phase is Phase.RUNNING:
                raise ScenarioStateError("a scenario is running; abort it before unloading")

            self._run = None

    def load(self, scenario_id: str) -> ScenarioResult:
        return self.load_config(self._library.scenario(scenario_id))

    def load_config(self, config: Any, fingerprint: str | None = None) -> ScenarioResult:
        """Arm `config`. With `fingerprint`, refuse (`ScenarioChanged`) unless
        its plant file and initial condition are the ones a run with that
        `RunInputs.fingerprint` was armed from - checked on the very bytes
        the run is then built from, before anything is parsed."""
        # Any: a decoded scenario file, validated against the schema below.
        with self._lock:
            if self._run is not None and self._run.phase is Phase.RUNNING:
                raise ScenarioStateError("a scenario is running; abort it before loading another")

            self._run = self._arm(config, fingerprint)

            return self._result(self._run)

    def start(self) -> ScenarioResult:
        with self._lock:
            run = self._loaded()

            if run.phase is not Phase.LOADED:
                raise ScenarioStateError(f"cannot start a scenario that is {run.phase.value}; load one first")

            run.phase = Phase.RUNNING

            with _journaling(run, Tick(TickKind.START, len(run.actions))):
                # A malfunction due at time zero takes effect now rather than
                # a step late, so the first step already runs the disturbed plant.
                run.malfunctions.update(run.view(run.engine.snapshot()))

            return self._result(run)

    def step(self, dt: float) -> Snapshot:
        with self._lock:
            run = self._loaded()

            if run.phase is not Phase.RUNNING:
                return run.engine.snapshot()

            clock = run.engine.clock
            tick = Tick(TickKind.STEP, len(run.actions), dt=dt, speed=clock.speed, paused=clock.paused)

            with _journaling(run, tick):
                snapshot = run.runtime.step(dt)
                self._observe(run, snapshot)

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

            with _journaling(run, Tick(TickKind.ABORT, len(run.actions))):
                run.malfunctions.revert_all()
                # The rebuilt runtime starts with clean interlocks and alarms,
                # but the log stays the run's one record of what was done.
                actions = run.runtime.actions
                run.runtime = self._restored(run.build, run.armed_state)
                run.runtime.actions = actions
                run.phase = Phase.ABORTED
                run.outcome = Outcome.ABORTED
                run.ended_at = elapsed

            return self._result(run)

    def result(self) -> ScenarioResult:
        with self._lock:
            return self._result(self._loaded())

    def _loaded(self) -> _Run:
        if self._run is None:
            raise ScenarioStateError("no scenario is loaded")

        return self._run

    def _arm(self, config: Any, expected: str | None) -> _Run:
        errors = validate(config, _schema())
        if errors:
            raise ScenarioConfigError("; ".join(errors))

        scenario_id = config["id"]
        condition = config["initial_condition"]
        _plain(config["plant"], "plant")
        _plain(condition["condition"], "initial condition")

        try:
            plant_path = self._library.plant_path(config["plant"])
            condition_path = self._library.condition_path(condition["condition"])
        except ScenarioNotFound as error:
            # Under a fingerprint, a plainly named file that is missing -
            # deleted, or renamed in the document - cannot match; a name that
            # could never resolve is the document's own error.
            if expected is None:
                raise

            raise ScenarioChanged(f"{error}, so the run cannot match the expected fingerprint") from error

        # Read once: arming, every abort and the fingerprint all see the
        # files as they were at load, whatever happens to them afterwards.
        plant_bytes = plant_path.read_bytes()
        condition_bytes = condition_path.read_bytes()
        fingerprint = _fingerprint(
            (config["plant"], plant_bytes),
            (condition["condition"], condition_bytes),
        )

        if expected is not None and fingerprint != expected:
            raise ScenarioChanged(
                f"plant {config['plant']!r} or initial condition {condition['condition']!r} "
                f"has changed since the run was armed",
            )

        plant_config = read_plant_config(plant_path, plant_bytes)
        state = apply_overrides(_decode_condition(condition_path, condition_bytes), condition.get("overrides", {}))

        def build() -> Plant:
            return load_plant(copy.deepcopy(plant_config))

        runtime = self._restored(build, state)
        engine = runtime.engine
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

        malfunctions.validate(armed)
        triggers.validate(armed)
        objectives.validate(armed)

        return _Run(
            config=copy.deepcopy(config),
            fingerprint=fingerprint,
            scenario_id=scenario_id,
            difficulty=config["difficulty"],
            seed=config["seed"],
            time_limit_s=time_limit,
            build=build,
            armed_state=state,
            runtime=runtime,
            origin=armed.sim_time,
            malfunctions=malfunctions,
            triggers=triggers,
            objectives=objectives,
            objective_ids=tuple(entry["id"] for entry in config.get("objectives", [])),
        )

    def _end_state(self, run: _Run) -> str:
        where = {"state": capture_state(run.engine), "result": self._result(run).as_dict()}

        return hashlib.sha256(json.dumps(where, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _restored(build: Callable[[], Plant], state: dict[str, JSONValue]) -> PlantRuntime:
        plant = build()
        engine = Engine.from_plant(plant)
        restore_state(engine, state)

        # After the restore, so the trips start from the restored snapshot.
        return PlantRuntime(engine, plant)

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


def _fingerprint(*files: tuple[str, bytes]) -> str:
    """Each file's name and content, so a document renamed to another file
    with the same bytes is a different run too."""
    digest = hashlib.sha256()

    for name, content in files:
        digest.update(hashlib.sha256(name.encode()).digest())
        digest.update(hashlib.sha256(content).digest())

    return digest.hexdigest()


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
