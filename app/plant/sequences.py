"""
Startup and shutdown sequences (T12-4): the procedures that walk the plant
state machine (T12-3) through a cold start and back down again, as
`config/sequences/*.yaml` describes them.

A sequences file holds two things for one plant:

    gates       the conditions on each edge of `PlantStateMachine`, the plant
                numbers T12-3 left to its caller
    sequences   named procedures, each an ordered list of steps

A step runs in four parts, in order:

    in            the plant states the step may run from
    permissives   conditions that must hold before the step does anything
    actions       operator actions it takes, `"<tag>.<action> [value]"`
    hold          conditions that must hold continuously for `for_s` seconds
                  of simulated time before the step is done, and then
    advance       the plant state it moves the machine to, if any

**A hold point is a condition held for a time, not a button.** The hold clock
is the `sim_time` of the snapshots a run is given, so a run has no clock of its
own and is as deterministic as the engine stepping it. A hold restarts from
zero whenever any of its conditions stops holding.

**A step out of order is blocked, not reordered.** `SequenceRun.request` starts
any step by name, and it is refused with every reason when the plant is not in
one of the step's `in` states or a permissive does not hold. `update` runs the
procedure in order on its own, waiting at a step until its permissives hold.

**Actions go through the caller's action path.** A run never touches a device:
it calls the `act` function it is given with `(tag, action, value)`, the
signature of `ScenarioRunner.act`, so the same allowlist and the same action
log apply to a sequence as to an operator. Loading checks every action against
the `operable` map the caller passes (tag -> action -> whether it takes a
value), which is that allowlist, and every condition against the equipment the
snapshot publishes, reporting every problem together.

**Normal and emergency shutdown are different procedures.** A normal shutdown
runs from a running plant through staged steps that each wait for the one
before (the compressor is unloaded before it is stopped). An emergency
shutdown takes every stopping action in one step, with no permissives, from any
running state, and moves straight to SHUTTING_DOWN, which the machine never
refuses. Both end in COLD once the machines have run down.

Not wired: no session or API endpoint loads a sequence yet, and trips are not
live in a session (nothing calls `TripSystem.update`), so an emergency
shutdown is something a caller starts, never a trip response.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from app.engine.snapshot import Snapshot
from app.plant.states import TRANSITIONS, Edge, PlantState, PlantStateMachine
from app.safety.actions import number
from app.safety.permissives import Permissive

Act = Callable[[str, str, float | None], object]

_ACTION_RE = re.compile(r"^(?P<tag>[^\s.]+)\.(?P<action>\w+)(?:\s+(?P<value>\S+))?$")
_FILE_KEYS = {"gates", "sequences"}
_GATE_KEYS = {"from", "to", "when"}
_STEP_KEYS = {"step", "in", "permissives", "actions", "hold", "advance"}
_HOLD_KEYS = {"when", "for_s"}


@dataclass(frozen=True)
class Action:
    tag: str
    action: str
    value: float | None

    def __str__(self) -> str:
        return f"{self.tag}.{self.action}" + ("" if self.value is None else f" {self.value:g}")


@dataclass(frozen=True)
class Step:
    name: str
    states: frozenset[PlantState]
    permissives: tuple[Permissive, ...]
    actions: tuple[Action, ...]
    hold: tuple[Permissive, ...]
    hold_s: float
    advance: PlantState | None


@dataclass(frozen=True)
class Sequences:
    gates: Mapping[Edge, tuple[str, ...]]
    procedures: Mapping[str, tuple[Step, ...]]

    def machine(
        self,
        snapshot: Snapshot,
        state: PlantState = PlantState.COLD,
    ) -> PlantStateMachine:
        return PlantStateMachine(snapshot, self.gates, state=state)


def load_sequences(
    path: Path | str,
    snapshot: Snapshot,
    operable: Mapping[str, Mapping[str, bool]],
) -> Sequences:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    errors: list[str] = []

    if not isinstance(raw, dict):
        errors.append("the file must be a mapping")
        raw = {}

    _keys(raw, _FILE_KEYS, "sequences file", errors, required=_FILE_KEYS)
    gates = _gates(raw.get("gates") or [], errors)
    procedures: dict[str, tuple[Step, ...]] = {}
    sequences = raw.get("sequences") or {}

    if not isinstance(sequences, dict):
        errors.append("sequences must be a mapping of name to steps")
        sequences = {}

    for name, steps in sequences.items():
        if not isinstance(steps, list) or not steps:
            errors.append(f"sequence {name!r} must be a non-empty list of steps")
            continue

        procedures[name] = tuple(
            step
            for index, entry in enumerate(steps)
            if (step := _step(f"sequence {name!r} step {index + 1}", entry, operable, errors))
        )

    for name, steps in procedures.items():
        names = [step.name for step in steps]
        duplicates = sorted({step for step in names if names.count(step) > 1})

        if duplicates:
            errors.append(f"sequence {name!r} repeats step(s) {duplicates}")

        for step in steps:
            for permissive in (*step.permissives, *step.hold):
                errors.extend(_unresolved(f"sequence {name!r} step {step.name!r}", permissive, snapshot))

    gate_errors: list[str] = []

    for (source, target), conditions in gates.items():
        for permissive in _conditions(list(conditions), f"gate {source} -> {target}", gate_errors):
            gate_errors.extend(_unresolved(f"gate {source} -> {target}", permissive, snapshot))

    errors.extend(gate_errors)

    if not gate_errors:
        try:
            PlantStateMachine(snapshot, gates)
        except ValueError as error:
            errors.append(str(error))

    if errors:
        raise ValueError(
            f"sequences file {path} rejected, {len(errors)} problem(s):\n"
            + "\n".join(f"  {error}" for error in errors),
        )

    return Sequences(gates=MappingProxyType(gates), procedures=MappingProxyType(procedures))


class SequenceRun:
    """One procedure being run against a plant state machine."""

    def __init__(
        self,
        steps: Sequence[Step],
        machine: PlantStateMachine,
        act: Act,
    ) -> None:
        self._steps = tuple(steps)
        self._machine = machine
        self._act = act
        self._next = 0
        self._running: Step | None = None
        self._held_since: float | None = None

    @property
    def active(self) -> str | None:
        return None if self._running is None else self._running.name

    @property
    def done(self) -> bool:
        return self._running is None and self._next >= len(self._steps)

    def request(self, name: str, snapshot: Snapshot) -> tuple[str, ...]:
        """Start the named step now. Returns every reason it may not, and
        then nothing has been done."""
        names = [step.name for step in self._steps]

        if name not in names:
            raise KeyError(f"no step {name!r}, only {names}")

        if self._running is not None:
            return (f"step {self._running.name!r} is still running",)

        return self._start(names.index(name), snapshot)

    def update(self, snapshot: Snapshot) -> tuple[str, ...]:
        """Run the procedure one scan: start the next step when it may, or
        carry the running one on. Returns why it is waiting, empty when it is
        not. A step with a hold is first judged on the scan after its
        actions, so a hold never passes on a reading taken before them."""
        if self._running is not None:
            return self._progress(self._running, snapshot)

        if self.done:
            return ()

        reasons = self._start(self._next, snapshot)

        if reasons or self._running is None or self._running.hold:
            return reasons

        return self._finish(self._running, snapshot)

    def _start(self, index: int, snapshot: Snapshot) -> tuple[str, ...]:
        step = self._steps[index]
        state = self._machine.state

        if state not in step.states:
            return (f"step {step.name!r} runs from {sorted(state.value for state in step.states)}, the plant is {state}",)

        reasons = _reasons(step.permissives, snapshot)

        if reasons:
            return reasons

        for action in step.actions:
            self._act(action.tag, action.action, action.value)

        self._running = step
        self._next = index + 1
        self._held_since = None

        return ()

    def _progress(self, step: Step, snapshot: Snapshot) -> tuple[str, ...]:
        reasons = _reasons(step.hold, snapshot)

        if reasons:
            self._held_since = None
            return reasons

        if self._held_since is None:
            self._held_since = snapshot.sim_time

        held = snapshot.sim_time - self._held_since

        if held < step.hold_s:
            return (f"step {step.name!r} holding, {held:g} of {step.hold_s:g} s",)

        return self._finish(step, snapshot)

    def _finish(self, step: Step, snapshot: Snapshot) -> tuple[str, ...]:
        if step.advance is not None:
            reasons = self._machine.advance(step.advance, snapshot)

            if reasons:
                return reasons

        self._running = None
        self._held_since = None

        return ()


def _reasons(permissives: Sequence[Permissive], snapshot: Snapshot) -> tuple[str, ...]:
    return tuple(
        reason
        for permissive in permissives
        if (reason := permissive.reason(snapshot)) is not None
    )


def _keys(
    entry: Mapping[str, Any],
    allowed: set[str],
    where: str,
    errors: list[str],
    required: frozenset[str] | set[str] = frozenset(),
) -> None:
    unknown = sorted(set(entry) - allowed)
    missing = sorted(required - set(entry))

    if unknown:
        errors.append(f"{where} has unknown key(s) {unknown}")

    if missing:
        errors.append(f"{where} is missing {missing}")


def _state(value: Any, where: str, errors: list[str]) -> PlantState | None:
    try:
        return PlantState(value)
    except ValueError:
        errors.append(f"{where}: {value!r} is not a plant state, only {[s.value for s in PlantState]}")
        return None


def _conditions(value: Any, where: str, errors: list[str]) -> tuple[Permissive, ...]:
    if not isinstance(value, list) or not all(isinstance(text, str) for text in value):
        errors.append(f"{where} must be a list of condition strings")
        return ()

    parsed: list[Permissive] = []

    for text in value:
        try:
            parsed.append(Permissive.parse(text))
        except ValueError as error:
            errors.append(f"{where}: {error}")

    return tuple(parsed)


def _gates(entries: Any, errors: list[str]) -> dict[Edge, tuple[str, ...]]:
    gates: dict[Edge, tuple[str, ...]] = {}

    if not isinstance(entries, list):
        errors.append("gates must be a list")
        return gates

    for index, entry in enumerate(entries):
        where = f"gate {index + 1}"

        if not isinstance(entry, dict):
            errors.append(f"{where} must be a mapping")
            continue

        _keys(entry, _GATE_KEYS, where, errors, required={"from", "to"})
        source = _state(entry.get("from"), where, errors)
        target = _state(entry.get("to"), where, errors)
        when = entry.get("when") or []

        if not isinstance(when, list) or not all(isinstance(text, str) for text in when):
            errors.append(f"{where} when must be a list of condition strings")
            continue

        if source is None or target is None:
            continue

        if (source, target) in gates:
            errors.append(f"{where} repeats {source} -> {target}")
            continue

        gates[(source, target)] = tuple(when)

    return gates


def _step(
    where: str,
    entry: Any,
    operable: Mapping[str, Mapping[str, bool]],
    errors: list[str],
) -> Step | None:
    if not isinstance(entry, dict):
        errors.append(f"{where} must be a mapping")
        return None

    _keys(entry, _STEP_KEYS, where, errors, required={"step", "in"})
    name = entry.get("step")

    if not isinstance(name, str) or not name:
        errors.append(f"{where} needs a step name")
        return None

    where = f"{where} {name!r}"
    raw_states = entry.get("in")
    states = frozenset(
        state
        for value in (raw_states if isinstance(raw_states, list) else [])
        if (state := _state(value, f"{where} in", errors)) is not None
    )

    if not isinstance(raw_states, list) or not raw_states:
        errors.append(f"{where} in must be a non-empty list of plant states")

    advance = None if entry.get("advance") is None else _state(entry["advance"], f"{where} advance", errors)

    if advance is not None:
        illegal = sorted(state.value for state in states if (state, advance) not in TRANSITIONS)

        if illegal:
            errors.append(f"{where} advances to {advance}, which {illegal} cannot reach")

    hold = entry.get("hold") or {}
    hold_s = 0.0
    hold_when: tuple[Permissive, ...] = ()

    if not isinstance(hold, dict):
        errors.append(f"{where} hold must be a mapping")
    else:
        _keys(hold, _HOLD_KEYS, f"{where} hold", errors, required={"when"} if hold else set())
        hold_when = _conditions(hold.get("when", []), f"{where} hold when", errors)
        raw_s = hold.get("for_s", 0.0)
        seconds = number(raw_s)

        if seconds is None or not math.isfinite(seconds) or seconds < 0.0:
            errors.append(f"{where} hold for_s must be a non-negative number, got {raw_s!r}")
        else:
            hold_s = seconds

    return Step(
        name=name,
        states=states,
        permissives=_conditions(entry.get("permissives", []), f"{where} permissives", errors),
        actions=tuple(
            action
            for text in _strings(entry.get("actions", []), f"{where} actions", errors)
            if (action := _action(text, f"{where} action {text!r}", operable, errors))
        ),
        hold=hold_when,
        hold_s=hold_s,
        advance=advance,
    )


def _strings(value: Any, where: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(text, str) for text in value):
        errors.append(f"{where} must be a list of strings")
        return []

    return value


def _action(
    text: str,
    where: str,
    operable: Mapping[str, Mapping[str, bool]],
    errors: list[str],
) -> Action | None:
    match = _ACTION_RE.match(text.strip())

    if match is None:
        errors.append(f"{where} is not '<tag>.<action> [value]'")
        return None

    tag, action, raw = match["tag"], match["action"], match["value"]

    if tag not in operable:
        errors.append(f"{where} names unknown device {tag!r}, only {sorted(operable)}")
        return None

    if action not in operable[tag]:
        errors.append(f"{where}: {tag} allows only {sorted(operable[tag])}")
        return None

    takes_value = operable[tag][action]

    if raw is None:
        if takes_value:
            errors.append(f"{where} requires a value")
            return None

        return Action(tag, action, None)

    if not takes_value:
        errors.append(f"{where} takes no value")
        return None

    try:
        value = float(raw)
    except ValueError:
        value = math.nan

    if not math.isfinite(value):
        errors.append(f"{where} value must be a finite number")
        return None

    return Action(tag, action, value)


def _unresolved(where: str, permissive: Permissive, snapshot: Snapshot) -> list[str]:
    condition = permissive.condition
    point = f"{condition.tag}.{condition.variable}"

    if condition.tag not in snapshot.equipment:
        return [f"{where} names unknown device {condition.tag!r}, only {sorted(snapshot.equipment)}"]

    row = snapshot.equipment[condition.tag]

    if condition.variable not in row:
        return [f"{where}: {point} is not published by {condition.tag}"]

    if number(row[condition.variable]) is None:
        return [f"{where}: {point} is not a number"]

    return []
