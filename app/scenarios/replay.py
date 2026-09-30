"""
Deterministic replay (T14-5): a run, played back, lands exactly where it did.

A `Recording` is everything a scenario run took as input - the scenario
document (its seed included) and, in the order they happened, every operator
action and every lifecycle `Tick` (`app.scenarios.runner`). `replay` arms a
fresh runner from the same document and feeds it the same inputs, so the
final plant state and every result metric come out bit for bit the same.

Order is the whole content of a recording. An action taken before `start` and
one taken just after it happen at the same sim time but not on the same
plant, since `start` writes any time-zero malfunction; a paused step moves no
time at all. So actions are placed among the ticks by the action log's own
count at each tick, never by their timestamps. The timestamps are checked
instead: an action that replays at a different scenario time than it was
recorded at, a tick reached with a different number of actions behind it, a
tick that raises other than as recorded, or an input the runner refuses is a
`ReplayDivergence` - a replay that has stopped reproducing the run says so,
where it happened, rather than carrying on to a plausible but different
result. A step that raised is still in the recording, with its error: it may
already have moved the plant and the run stayed live, so replay runs it,
expects that same error, and carries on.

A step replays with the dt, speed and pause it was recorded with, because
explicit integration makes the path depend on how time was cut into steps:
the same 60 s in 1 s steps and in 2 s steps are two different runs. The
clock turns (dt, speed) into elapsed time the same way both times, so the
elapsed time is identical too. Replay drives the runner directly, with no
`Scheduler` pacing it against a wall clock, which is what makes it faster
than real time.

What a recording does not carry: the plant file and the initial condition
are read by name from the `ScenarioLibrary`, as on load. It carries their
fingerprint instead, and a replay that finds either changed refuses to run
rather than reproduce a different plant. Nor does it carry random-number
state - nothing in the plant draws one yet (see `app/engine/rng.py`), and
`tests/test_scenario_replay.py` fails the build if something starts to, since
replay would then need that state too. Anything done to `runner.engine`
behind the runner's back is not an input the runner sees and is not
replayed - except the clock's speed and pause, which each step records and
the recording carries as they stood when it was taken, so a change made
after the last step is replayed too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.scenarios.runner import (
    Phase,
    ScenarioLibrary,
    ScenarioRunner,
    ScenarioStateError,
    Tick,
    TickKind,
    error_text,
)


class ReplayDivergence(RuntimeError):
    """A replay stopped reproducing the run it was recorded from."""


class RecordingFormatError(ValueError):
    """A recording document that is not the shape `Recording.as_dict` writes."""


@dataclass(frozen=True)
class Act:
    """One operator action, at the scenario time it was taken."""

    target: str
    action: str
    value: float | None
    sim_time: float


type Input = Act | Tick


@dataclass(frozen=True)
class Recording:
    """A run's inputs: its scenario document, then every action and tick,
    then the clock's speed and pause as they stood when it was taken."""

    scenario: Any  # Any: a scenario document, of the shape the scenario schema allows
    inputs: tuple[Input, ...]
    speed: float = 1.0
    paused: bool = False
    fingerprint: str | None = None  # `RunInputs.fingerprint`; None replays unchecked

    @classmethod
    def of(cls, runner: ScenarioRunner) -> Recording:
        """The loaded run so far, as a recording."""
        run = runner.inputs()
        actions = run.actions
        inputs: list[Input] = []
        taken = 0

        def catch_up(count: int) -> None:
            nonlocal taken

            # `inputs()` reads journal and log under one lock, so they agree.
            assert count <= len(actions), f"a tick follows {count} actions, but the log holds {len(actions)}"

            for action in actions[taken:count]:
                inputs.append(
                    Act(
                        target=action["tag"],
                        action=action["action"],
                        value=action["value"],
                        sim_time=action["sim_time"],
                    ),
                )

            taken = max(taken, count)

        for tick in run.journal:
            catch_up(tick.actions)
            inputs.append(tick)

        catch_up(len(actions))

        return cls(
            scenario=run.config,
            inputs=tuple(inputs),
            speed=run.speed,
            paused=run.paused,
            fingerprint=run.fingerprint,
        )

    def as_dict(self) -> dict[str, Any]:
        # Any: a JSON document; the scenario is whatever its file decoded to.
        return {
            "scenario": self.scenario,
            "inputs": [_encode(item) for item in self.inputs],
            "clock": {"speed": self.speed, "paused": self.paused},
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> Recording:
        # Any: a decoded JSON document, checked here field by field.
        if not isinstance(document, Mapping) or set(document) != {"scenario", "inputs", "clock", "fingerprint"}:
            raise RecordingFormatError(
                "a recording is an object with exactly 'scenario', 'inputs', 'clock' and 'fingerprint'",
            )

        fingerprint = document["fingerprint"]
        if fingerprint is not None and not isinstance(fingerprint, str):
            raise RecordingFormatError(f"recording 'fingerprint' must be text or null, got {fingerprint!r}")

        inputs = document["inputs"]
        if not isinstance(inputs, list):
            raise RecordingFormatError("recording 'inputs' must be a list")

        clock = document["clock"]
        if not isinstance(clock, dict) or set(clock) != {"speed", "paused"}:
            raise RecordingFormatError("recording 'clock' must be an object with exactly 'speed' and 'paused'")

        if not isinstance(clock["paused"], bool):
            raise RecordingFormatError("clock.paused must be true or false")

        return cls(
            scenario=document["scenario"],
            inputs=tuple(_decode(item, f"inputs[{index}]") for index, item in enumerate(inputs)),
            speed=_float(clock["speed"], "clock.speed"),
            paused=clock["paused"],
            fingerprint=fingerprint,
        )


def replay(recording: Recording, library: ScenarioLibrary | None = None) -> ScenarioRunner:
    """A fresh runner, armed from the recording's scenario and driven through
    every one of its inputs. Read its `result()` and `engine` for the outcome.

    Raises `ReplayDivergence` at the first input that does not reproduce.
    """
    changed = "the plant file or initial condition has changed since this run was recorded"
    runner = ScenarioRunner(library)

    try:
        runner.load_config(recording.scenario)
    except (LookupError, ValueError) as error:
        # The run armed once, so a recording that no longer arms is one whose
        # files moved under it - when it carries a fingerprint to say so.
        if recording.fingerprint is None:
            raise

        raise ReplayDivergence(f"{changed}: it no longer loads: {error}") from error

    if recording.fingerprint is not None and runner.inputs().fingerprint != recording.fingerprint:
        raise ReplayDivergence(changed)

    for index, item in enumerate(recording.inputs):
        try:
            if isinstance(item, Act):
                _act(runner, item)
            else:
                _tick(runner, item)
        except ReplayDivergence as divergence:
            raise ReplayDivergence(f"inputs[{index}]: {divergence}") from divergence

    _set_clock(runner, recording.speed, recording.paused)

    return runner


def _act(runner: ScenarioRunner, item: Act) -> None:
    try:
        runner.act(item.target, item.action, item.value)
    except (KeyError, ValueError) as error:
        raise ReplayDivergence(f"the runner refused {item}: {error}") from error

    replayed = runner.result().actions[-1]["sim_time"]
    if replayed != item.sim_time:
        raise ReplayDivergence(
            f"{item.target} {item.action} was recorded at t={item.sim_time!r} "
            f"but replayed at t={replayed!r}",
        )


def _tick(runner: ScenarioRunner, tick: Tick) -> None:
    taken = len(runner.actions)
    if taken != tick.actions:
        raise ReplayDivergence(f"{tick.kind.value} was recorded after {tick.actions} actions, reached after {taken}")

    try:
        if tick.kind is TickKind.STEP:
            _step(runner, tick)
        elif tick.kind is TickKind.START:
            runner.start()
        else:
            runner.abort()
    except ReplayDivergence:
        raise
    except Exception as error:
        raised = error_text(error)

        if tick.error is None:
            # Refusing a start or an abort the run took is the replay's
            # failure; any other error is the plant's, and is left as it is.
            if isinstance(error, ScenarioStateError):
                raise ReplayDivergence(f"the runner refused {tick.kind.value}: {error}") from error

            raise

        if raised != tick.error:
            raise ReplayDivergence(
                f"{tick.kind.value} was recorded raising {tick.error!r} but raised {raised!r}",
            ) from error

        return

    if tick.error is not None:
        raise ReplayDivergence(f"{tick.kind.value} was recorded raising {tick.error!r} but did not")


def _step(runner: ScenarioRunner, tick: Tick) -> None:
    assert tick.dt is not None and tick.speed is not None and tick.paused is not None  # a step's fields

    phase = runner.phase
    if phase is not Phase.RUNNING:
        raise ReplayDivergence(f"a step was recorded while running, but the replay is {phase.value}")

    _set_clock(runner, tick.speed, tick.paused)

    runner.step(tick.dt)


def _set_clock(runner: ScenarioRunner, speed: float, paused: bool) -> None:
    clock = runner.engine.clock
    clock.set_speed(speed)

    if paused:
        clock.pause()
    else:
        clock.resume()


def _encode(item: Input) -> dict[str, Any]:
    # Any: one JSON-safe input row.
    if isinstance(item, Act):
        return {
            "type": "act",
            "target": item.target,
            "action": item.action,
            "value": item.value,
            "sim_time": item.sim_time,
        }

    row: dict[str, Any] = {"type": item.kind.value, "actions": item.actions, "error": item.error}
    if item.kind is TickKind.STEP:
        row.update(dt=item.dt, speed=item.speed, paused=item.paused)

    return row


_FIELDS = {
    "act": {"type", "target", "action", "value", "sim_time"},
    TickKind.START.value: {"type", "actions", "error"},
    TickKind.ABORT.value: {"type", "actions", "error"},
    TickKind.STEP.value: {"type", "actions", "error", "dt", "speed", "paused"},
}


def _decode(row: Any, path: str) -> Input:
    # Any: one decoded JSON row, checked against the shape `_encode` writes.
    kind = row.get("type") if isinstance(row, dict) else None
    if kind not in _FIELDS:
        raise RecordingFormatError(f"{path} must be an object whose 'type' is one of {sorted(_FIELDS)}")

    if set(row) != _FIELDS[kind]:
        raise RecordingFormatError(f"{path} ({kind}) must have exactly {sorted(_FIELDS[kind])}")

    if kind == "act":
        if not isinstance(row["target"], str) or not isinstance(row["action"], str):
            raise RecordingFormatError(f"{path} target and action must be strings")

        value = row["value"]
        return Act(
            target=row["target"],
            action=row["action"],
            value=None if value is None else _float(value, f"{path}.value"),
            sim_time=_float(row["sim_time"], f"{path}.sim_time"),
        )

    actions = row["actions"]
    if isinstance(actions, bool) or not isinstance(actions, int) or actions < 0:
        raise RecordingFormatError(f"{path}.actions must be a count of 0 or more, got {actions!r}")

    error = row["error"]
    if error is not None and not isinstance(error, str):
        raise RecordingFormatError(f"{path}.error must be text or null, got {error!r}")

    if kind != TickKind.STEP.value:
        return Tick(TickKind(kind), actions, error=error)

    if not isinstance(row["paused"], bool):
        raise RecordingFormatError(f"{path}.paused must be true or false")

    return Tick(
        TickKind.STEP,
        actions,
        dt=_float(row["dt"], f"{path}.dt"),
        speed=_float(row["speed"], f"{path}.speed"),
        paused=row["paused"],
        error=error,
    )


def _float(value: Any, path: str) -> float:
    # Any: a decoded JSON value that should be a number.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordingFormatError(f"{path} must be a number, got {value!r}")

    return float(value)
