"""
Training session (T16-8) - one browser's plant, free play and scenarios alike.

A console needs one config-loaded plant per browser, driven by one background
scheduler and kept alive while its stream is open. `TrainingSession` is that
plant. It stands alone: it does not subclass, import or reuse the legacy
`app.engine.sessions.Session`, which holds two single-machine test plants and
two schedulers from the earliest pages.

It owns three things:

- `free`: a `PlantRuntime` of `config.FREE_PLAY_PLANT` restored to
  `config.FREE_PLAY_CONDITION`, both resolved through `ScenarioLibrary` so free
  play reads the same files a scenario of that plant does.
- `runner`: a `ScenarioRunner`, idle until a scenario is loaded.
- `training_scheduler`: a `Scheduler` over the session itself. It is created
  here and started by whoever serves the console; nothing here starts a thread.

**The session is `Steppable`.** `step` and `snapshot` go to the runner while
its phase is not IDLE - loaded, running, complete or aborted - and to free play
otherwise. Free play does not advance while a scenario is loaded, and unloading
hands it back exactly where it was left.

**Every write is a scheduler command**, so it runs under `step_lock` and
publishes at once: `act`, `acknowledge`, and a scenario's `load`, `start`,
`abort` and `unload`. A response read straight after a write sees it, and a
load switches the published snapshot to the scenario's plant in the same
command. The runner's own lock nests inside `step_lock`, the order
`app.scenarios.runner` documents. Alarm entries are read under `step_lock` too,
which pins which plant answers: a load or unload cannot land between choosing
the plant and reading it.

`end()` closes the scheduler and is idempotent. A closed scheduler refuses
`start` and a manual step, so a request still holding a reclaimed session
cannot start a worker the registry no longer counts.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app import config
from app.alarms.acknowledge import Acknowledged
from app.alarms.history import HistoryEntry
from app.api.visibility import operator_view
from app.engine.engine import Engine
from app.engine.persistence import restore_state
from app.engine.scheduler import Scheduler
from app.engine.snapshot import Snapshot
from app.historian.buffer import Sample
from app.plant.loader import load_plant, read_plant_config
from app.scenarios.runner import Phase, ScenarioLibrary, ScenarioResult, ScenarioRunner
from app.statetypes import JSONValue
from app.training.runtime import PlantRuntime


@dataclass(frozen=True)
class Standing:
    """Where the session's plant stands, as the landing page shows it: free
    play, or a scenario in some phase. `scenario_id` is the loaded scenario's
    id, for a caller to look its title up: it is never sent to a page."""

    mode: str
    phase: str
    sim_time: float
    scenario_id: str | None


class TrainingSession:
    def __init__(self, library: ScenarioLibrary | None = None) -> None:
        library = library if library is not None else ScenarioLibrary()

        self.free = _free_play(library)
        self.runner = ScenarioRunner(library)
        self.training_scheduler = Scheduler(self)
        # Which plant's devices a view classes rows by. Replaced, never
        # mutated, after every command, so a reader needs no lock.
        self._shown = self.free.engine.equipment

    def end(self) -> None:
        """Close the scheduler, stopping and joining its worker. Permanent and
        safe to repeat, or on a session whose scheduler was never started."""
        self.training_scheduler.close()

    # Steppable: what the scheduler drives. step() runs under step_lock on the
    # worker or a manual step; snapshot() is called under it too, by command()
    # and by the scheduler's first read, so the phase it reads is stable.

    def step(self, dt: float) -> Snapshot:
        return self._plant().step(dt)

    def snapshot(self) -> Snapshot:
        return self._plant().snapshot()

    def operator_view(self, snapshot: Snapshot) -> dict[str, JSONValue]:
        """`snapshot` as a browser may see it: each equipment row cut to what
        its device class lists as operator-visible. Takes no lock, so a stream
        tick never waits on a step. Classes come from the plant the last
        command left showing; a scenario load or unload between the caller
        reading `snapshot` and this call can pair a snapshot with the other
        plant's classes, which fails closed (empty or narrower rows) for the
        one event it affects."""
        return operator_view(snapshot, self._shown)

    def act(self, target: str, action: str, value: float | None) -> None:
        """One operator action on the plant the snapshot shows. Refusals are
        those of `PlantRuntime.act`, or of `ScenarioRunner.act` once a
        scenario is loaded."""

        def apply() -> None:
            if self.runner.phase is Phase.IDLE:
                self.free.act(target, action, value)
            else:
                self.runner.act(target, action, value)

        self._command(apply)

    def acknowledge(self, alarm_id: str) -> Acknowledged:
        def apply() -> Acknowledged:
            if self.runner.phase is Phase.IDLE:
                return self.free.acknowledge(alarm_id)

            return self.runner.acknowledge(alarm_id)

        return self._command(apply)

    def alarm_entries(self) -> tuple[HistoryEntry, ...]:
        """The alarm history of the plant the snapshot shows, oldest first."""
        with self.training_scheduler.step_lock:
            if self.runner.phase is Phase.IDLE:
                return self.free.alarm_entries()

            return self.runner.alarm_entries()

    def plant_description(self) -> tuple[str, dict[str, JSONValue]]:
        """The id of the plant the snapshot shows - the plant file stem, as
        `config.FREE_PLAY_PLANT` or a scenario's `plant` names it - and its
        `PlantRuntime.plant_description`, read together under `step_lock`.
        Never starts the scheduler."""
        with self.training_scheduler.step_lock:
            if self.runner.phase is Phase.IDLE:
                return config.FREE_PLAY_PLANT, self.free.plant_description()

            return self.runner.plant_description()

    def trend_points(self) -> tuple[str, ...]:
        """The trend points of the plant the snapshot shows, sorted."""
        with self.training_scheduler.step_lock:
            return self._plant().trend_points()

    def trend_descriptors(self) -> dict[str, str]:
        """The operator's word for each trend point of the plant the snapshot shows."""
        with self.training_scheduler.step_lock:
            return self._plant().trend_descriptors()

    def trend_units(self) -> dict[str, str]:
        """The unit of each trend point of the plant the snapshot shows."""
        with self.training_scheduler.step_lock:
            return self._plant().trend_units()

    def trend_limits(self) -> dict[str, dict[str, float]]:
        """The evaluated envelope bounds of the plant the snapshot shows."""
        with self.training_scheduler.step_lock:
            return self._plant().trend_limits()

    def trend_history(self, points: Sequence[str]) -> dict[str, tuple[Sample, ...]]:
        """The samples of `points` from the plant the snapshot shows.
        `KeyError` for a point that plant does not publish."""
        with self.training_scheduler.step_lock:
            return self._plant().trend_history(points)

    def load(self, scenario_id: str) -> ScenarioResult:
        return self._command(lambda: self.runner.load(scenario_id))

    def start(self) -> ScenarioResult:
        return self._command(self.runner.start)

    def abort(self) -> ScenarioResult:
        return self._command(self.runner.abort)

    def unload(self) -> None:
        self._command(self.runner.unload)

    def result(self) -> ScenarioResult:
        return self.runner.result()

    def standing(self) -> Standing:
        """Read under `step_lock`, so the mode, phase and time are one moment.
        Never starts the scheduler."""
        with self.training_scheduler.step_lock:
            phase = self.runner.phase
            sim_time = self.snapshot().sim_time

            if phase is Phase.IDLE:
                return Standing("free_play", phase.value, sim_time, None)

            return Standing("scenario", phase.value, sim_time, self.runner.result().scenario_id)

    def _plant(self) -> PlantRuntime | ScenarioRunner:
        return self.free if self.runner.phase is Phase.IDLE else self.runner

    def _command[T](self, apply: Callable[[], T]) -> T:
        """Run `apply` as a scheduler command and return what it returned. The
        command publishes the snapshot it leaves behind, so a failed `apply`
        publishes nothing."""
        outcome: list[T] = []

        def run() -> None:
            try:
                outcome.append(apply())
            finally:
                self._shown = self._plant().engine.equipment

        self.training_scheduler.command(run)

        return outcome[0]


def _free_play(library: ScenarioLibrary) -> PlantRuntime:
    plant = load_plant(read_plant_config(library.plant_path(config.FREE_PLAY_PLANT)))
    engine = Engine.from_plant(plant)
    restore_state(engine, library.condition(config.FREE_PLAY_CONDITION))

    # After the restore, so the trips start from the restored snapshot.
    return PlantRuntime(engine, plant)
