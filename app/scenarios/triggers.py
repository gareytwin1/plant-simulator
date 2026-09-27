"""
Trigger evaluator - contract C8's scenario-trigger half (T14-2).

A trigger fires on a step where its condition holds, so a scenario can drive
a malfunction's onset, a debrief note or a UI cue off the same three things
an operator experiences: elapsed time, the plant's own state, and their own
actions. Each trigger type reads exactly one of those:

    TimeTrigger           snapshot.sim_time
    ConditionTrigger      a "TAG.field" comparison against the snapshot
    OperatorActionTrigger the operator action log

`is_met` on every kind is pure - it reads only the snapshot and action log
handed to it, never a clock or a copy of prior state - the same shape as
`app.disturbances.malfunction.StartCondition`. `TriggerEvaluator` is where
external bookkeeping lives instead: which one-shot triggers have already
fired, and, for an operator-action trigger, how much of the log it has
already looked at and whether it has matched. A time or condition trigger
carries none of that - it re-evaluates fresh every call and may fire on
every step its condition holds, which is the point for something like
"discharge pressure is high" driving a continuous alarm-style cue. An
operator-action trigger is different: once the log shows its action ever
happened, it stays met forever (the log is append-only, so a match can't
un-happen), and `TriggerEvaluator` latches that rather than re-scanning.

A condition string is compiled once, at construction, into a `Condition`:
evaluating it each step is a handful of dict lookups and one comparison, not
a re-parse. An operator-action trigger gets a smaller version of the same
saving: on a step where nothing new has been recorded since it last looked,
`TriggerEvaluator` skips the log entirely, and on a step where it has grown,
`itertools.islice` over the log's `__iter__` means only the entries added
since its own last look are ever *compared* against - it never repeats the
tag/action check on one it has already ruled out, and never copies the
whole log to do it. `islice` still has to step past the old entries one by
one to reach that point (`ActionLog` exposes no way to jump straight to an
index), so a rescan's cost is not independent of total log length, just
cheaper per old entry than a full re-comparison would be. Making it truly
independent of log length needs `ActionLog` itself to expose a real slice,
which belongs to T15-1, not this task.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.engine.snapshot import Section, Snapshot
from app.scoring.actionlog import ActionEvent, ActionLog
from app.statetypes import JSONValue


class ConditionSyntaxError(ValueError):
    """A condition string is not `TAG.field OP value` or bare `TAG.field`."""


class ConditionEvaluationError(ValueError):
    """A condition string's tag, field or comparison doesn't fit the snapshot."""


_TAG = r"[A-Za-z0-9_-]+"
_FIELD = r"[A-Za-z0-9_]+"
_OPS = ("==", "!=", ">=", "<=", ">", "<")

_COMPARISON = re.compile(
    rf"^(?P<tag>{_TAG})\.(?P<field>{_FIELD})\s*(?P<op>{'|'.join(_OPS)})\s*"
    rf"(?P<value>\S+)$",
)
_BARE = re.compile(rf"^(?P<tag>{_TAG})\.(?P<field>{_FIELD})$")


@dataclass(frozen=True)
class Condition:
    """A compiled `TAG.field [OP value]` expression.

    `op is None` means a bare boolean check on the field's own value, the
    shape an objective's `"K-101.tripped"` failure condition needs. String
    equality ("PIC-101.mode == MANUAL") is a real gap - out of scope for
    T14-2, a C8 grammar decision for whoever needs it.
    """

    tag: str
    field: str
    op: str | None
    value: float | None

    @classmethod
    def parse(cls, expression: str) -> Condition:
        text = expression.strip()

        match = _COMPARISON.match(text)
        if match is not None:
            try:
                value = float(match["value"])
            except ValueError:
                raise ConditionSyntaxError(
                    f"{match['value']!r} is not a number in condition {expression!r}",
                ) from None

            if not math.isfinite(value):
                raise ConditionSyntaxError(
                    f"{match['value']!r} is not a finite number in condition {expression!r}",
                )

            return cls(tag=match["tag"], field=match["field"], op=match["op"], value=value)

        match = _BARE.match(text)
        if match is not None:
            return cls(tag=match["tag"], field=match["field"], op=None, value=None)

        raise ConditionSyntaxError(
            f"{expression!r} is not a valid condition; expected "
            f"'TAG.field OP value' or bare 'TAG.field'",
        )

    def is_met(self, snapshot: Snapshot) -> bool:
        current = _lookup(snapshot, self.tag, self.field)

        if self.op is None:
            # Deliberately narrow rather than silently true for a
            # non-boolean field - see the class docstring.
            if not isinstance(current, bool):
                raise ConditionEvaluationError(
                    f"{self.tag}.{self.field} is {current!r}, not true/false; "
                    f"a bare condition needs a boolean field",
                )

            return current

        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ConditionEvaluationError(
                f"{self.tag}.{self.field} is {current!r}, not a number; "
                f"'{self.op}' needs one",
            )

        assert self.value is not None  # every comparison op parses a value

        return _compare(self.op, float(current), self.value)


def _resolve_tag(snapshot: Snapshot, tag: str) -> Mapping[str, JSONValue]:
    # Priority order a bare tag resolves against. `envelope` is keyed
    # "TAG.variable" rather than nested by tag, so it isn't a lookup source
    # here.
    sections: tuple[tuple[str, Section], ...] = (
        ("equipment", snapshot.equipment),
        ("nodes", snapshot.nodes),
        ("streams", snapshot.streams),
        ("controllers", snapshot.controllers),
    )
    found = [(name, section[tag]) for name, section in sections if tag in section]

    if not found:
        raise ConditionEvaluationError(
            f"no equipment, node, stream or controller tagged {tag!r} in this snapshot",
        )

    if len(found) > 1:
        raise ConditionEvaluationError(
            f"tag {tag!r} is ambiguous: present in both "
            f"{found[0][0]} and {found[1][0]}",
        )

    return found[0][1]


def _lookup(snapshot: Snapshot, tag: str, field: str) -> JSONValue:
    row = _resolve_tag(snapshot, tag)

    if field not in row:
        raise ConditionEvaluationError(
            f"{tag!r} has no field {field!r}; has {sorted(row)}",
        )

    return row[field]


def _compare(op: str, current: float, target: float) -> bool:
    if op == "==":
        return current == target
    if op == "!=":
        return current != target
    if op == ">=":
        return current >= target
    if op == "<=":
        return current <= target
    if op == ">":
        return current > target
    if op == "<":
        return current < target

    raise AssertionError(f"unreachable: {op!r} is not one of {_OPS}")


class TriggerKind(Protocol):
    """Whether a trigger fires now. Reads the snapshot and action log only.

    `actions` is `Iterable`, not `Sequence`: `TriggerEvaluator` may hand an
    operator-action trigger a one-shot slice of the log (only the entries
    added since it last looked) rather than the whole thing, and a
    `Sequence`'s random access and `len()` are never needed to check "did
    any of these match".
    """

    def is_met(self, snapshot: Snapshot, actions: Iterable[ActionEvent]) -> bool: ...


@dataclass(frozen=True)
class TimeTrigger:
    """Fires on every step from `sim_time` onward."""

    sim_time: float

    def is_met(self, snapshot: Snapshot, actions: Iterable[ActionEvent]) -> bool:
        return snapshot.sim_time >= self.sim_time


@dataclass(frozen=True)
class ConditionTrigger:
    """Fires on every step the compiled condition holds against the snapshot."""

    condition: Condition

    def is_met(self, snapshot: Snapshot, actions: Iterable[ActionEvent]) -> bool:
        return self.condition.is_met(snapshot)


@dataclass(frozen=True)
class OperatorActionTrigger:
    """Fires on every step where `tag` has ever taken `action` in the log."""

    tag: str
    action: str

    def is_met(self, snapshot: Snapshot, actions: Iterable[ActionEvent]) -> bool:
        return any(
            event.tag == self.tag and event.data.get("action") == self.action
            for event in actions
        )


def _split_action(value: str) -> tuple[str, str]:
    tag, dot, action = value.partition(".")

    if not dot or not tag or not action:
        raise ConditionSyntaxError(
            f"{value!r} is not a valid operator_action trigger action; "
            f"expected 'TAG.action' with both parts non-empty",
        )

    return tag, action


@dataclass(frozen=True)
class Trigger:
    """One scenario trigger: an id, what fires it, and whether it re-arms."""

    id: str
    kind: TriggerKind
    one_shot: bool = False

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Trigger:
        # Any: decoded scenario JSON, a shape nothing knows yet - every
        # field is validated below before it reaches a typed attribute.
        if "id" not in config:
            raise ValueError(f"a trigger config is missing 'id': {config!r}")

        trigger_id = config["id"]
        if not isinstance(trigger_id, str) or not trigger_id:
            raise ValueError(f"a trigger id must be a non-empty string, got {trigger_id!r}")

        if "type" not in config:
            raise ValueError(f"trigger {trigger_id!r} is missing 'type'")

        trigger_type = config["type"]
        kind: TriggerKind

        if trigger_type == "time":
            sim_time = _required_number(config, "sim_time", trigger_id)
            if sim_time < 0:
                raise ValueError(
                    f"trigger {trigger_id!r} has sim_time={sim_time!r}, which is "
                    f"negative; scenario.schema.json requires 0 or more",
                )

            kind = TimeTrigger(sim_time=sim_time)
        elif trigger_type == "condition":
            kind = ConditionTrigger(
                condition=Condition.parse(_required_string(config, "condition", trigger_id)),
            )
        elif trigger_type == "operator_action":
            tag, action = _split_action(_required_string(config, "action", trigger_id))
            kind = OperatorActionTrigger(tag=tag, action=action)
        else:
            raise ValueError(
                f"trigger {trigger_id!r} has unknown type {trigger_type!r}; "
                f"expected 'time', 'condition' or 'operator_action'",
            )

        one_shot = config.get("one_shot", False)
        if not isinstance(one_shot, bool):
            raise ValueError(
                f"trigger {trigger_id!r} has one_shot={one_shot!r}, which is not a bool",
            )

        return cls(id=trigger_id, kind=kind, one_shot=one_shot)


def _required(config: Mapping[str, Any], key: str, trigger_id: str) -> Any:
    if key not in config:
        raise ValueError(
            f"trigger {trigger_id!r} is type {config['type']!r} but has no {key!r}",
        )

    return config[key]


def _required_number(config: Mapping[str, Any], key: str, trigger_id: str) -> float:
    value = _required(config, key, trigger_id)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"trigger {trigger_id!r} has {key}={value!r}, which is not a number")

    try:
        number = float(value)
    except OverflowError:
        # An int too large for a float (e.g. a 400-digit sim_time) - still a
        # malformed config, not a crash.
        raise ValueError(
            f"trigger {trigger_id!r} has {key}={value!r}, which is too large",
        ) from None

    if not math.isfinite(number):
        raise ValueError(f"trigger {trigger_id!r} has {key}={value!r}, which is not finite")

    return number


def _required_string(config: Mapping[str, Any], key: str, trigger_id: str) -> str:
    value = _required(config, key, trigger_id)

    if not isinstance(value, str):
        raise ValueError(f"trigger {trigger_id!r} has {key}={value!r}, which is not a string")

    return value


class TriggerEvaluator:
    """Evaluates one scenario run's triggers each step, honouring one-shot.

    Every trigger's `is_met` is otherwise pure; the state that makes it
    cheap to re-check lives here instead, the way `MalfunctionRegistry`
    tracks a malfunction's onset outside its stateless `StartCondition`:
    which one-shot triggers have already fired, and, for an
    operator-action trigger, how much of the action log it has already
    looked at and whether it has matched.

    That state is scoped to a single run against a single `ActionLog` -
    an instance is not meant to outlive the run it was built for, and
    `evaluate()` enforces that rather than trusting the caller: the first
    call binds the evaluator to whichever `ActionLog` it was given, and a
    later call with a *different* log raises. Starting a new run (a
    scenario reset or replay) means constructing a fresh `TriggerEvaluator`
    over the same, already-compiled `Trigger` tuple - `TriggerEvaluator(the
    same triggers)`, not `TriggerEvaluator.from_config(the same config)`,
    which would re-parse every condition string for no reason.
    """

    def __init__(self, triggers: Iterable[Trigger]) -> None:
        self._triggers = tuple(triggers)

        seen: set[str] = set()
        for trigger in self._triggers:
            if trigger.id in seen:
                raise ValueError(f"duplicate trigger id {trigger.id!r}")
            seen.add(trigger.id)

        self._fired: set[str] = set()
        # An operator_action trigger reads an append-only log: once matched,
        # it stays matched, so a latched id never needs scanning again. Until
        # then, `_action_seen_length` remembers the log length as of the last
        # scan - unchanged means nothing new could have matched, so most
        # steps (no action taken) cost a length check, not a rescan.
        self._action_matched: set[str] = set()
        self._action_seen_length: dict[str, int] = {}
        # The ActionLog this evaluator is bound to - set on the first
        # evaluate() call. A strong reference, not just its id(): holding it
        # keeps that log alive, so unlike an id-only check this can never be
        # fooled by CPython reusing a freed object's address.
        self._action_log: ActionLog | None = None

    @classmethod
    def from_config(cls, triggers: Iterable[Mapping[str, Any]]) -> TriggerEvaluator:
        return cls(Trigger.from_config(config) for config in triggers)

    def validate(self, snapshot: Snapshot) -> None:
        """Resolve every trigger's snapshot references against `snapshot` now.

        Raises the same `ConditionEvaluationError` `evaluate()` would, but up
        front - at scenario load or arm time - rather than out of the step
        loop partway through a run: a condition trigger's tag, field and
        comparison, and an operator-action trigger's tag. Its action verb
        cannot be checked this way - only the log tells whether it is one a
        device ever accepts - and a time trigger needs no snapshot at all.
        """
        for trigger in self._triggers:
            if isinstance(trigger.kind, ConditionTrigger):
                trigger.kind.condition.is_met(snapshot)
            elif isinstance(trigger.kind, OperatorActionTrigger):
                # The C5 action endpoint's ACTIONS allowlist (app/api/action.py)
                # keys on Equipment subclasses only - a node, stream or
                # controller is never an operator_action target, so checking
                # against those sections too would both reject a valid tag
                # that happens to collide with one of them and accept a tag
                # that exists only as one, which no action could ever reach.
                if trigger.kind.tag not in snapshot.equipment:
                    raise ConditionEvaluationError(
                        f"no equipment tagged {trigger.kind.tag!r} in this "
                        f"snapshot; an operator_action trigger can only "
                        f"target equipment",
                    )

    def evaluate(self, snapshot: Snapshot, actions: ActionLog) -> tuple[str, ...]:
        """Return the ids of every trigger that fires on this step.

        `actions` must be the same `ActionLog` on every call - see the
        class docstring. Raises `ValueError` if a later call passes a
        different one.
        """
        if self._action_log is None:
            self._action_log = actions
        elif actions is not self._action_log:
            raise ValueError(
                "TriggerEvaluator.evaluate() was called with a different "
                "ActionLog than its first call - it is scoped to one run; "
                "construct a new TriggerEvaluator for a new one",
            )

        fired: list[str] = []

        for trigger in self._triggers:
            if trigger.one_shot and trigger.id in self._fired:
                continue

            if isinstance(trigger.kind, OperatorActionTrigger):
                met = self._operator_action_is_met(trigger, actions, snapshot)
            else:
                met = trigger.kind.is_met(snapshot, ())

            if met:
                fired.append(trigger.id)

                if trigger.one_shot:
                    self._fired.add(trigger.id)

        return tuple(fired)

    def _operator_action_is_met(
        self,
        trigger: Trigger,
        actions: ActionLog,
        snapshot: Snapshot,
    ) -> bool:
        if trigger.id in self._action_matched:
            return True

        total = len(actions)
        seen = self._action_seen_length.get(trigger.id, 0)

        if seen == total:
            # Nothing new since the last look means nothing new to match.
            return False

        # is_met only ever compares the entries added since this trigger's
        # own last look, never a copy of the whole log and never a repeat
        # comparison of one it has already ruled out. islice still has to
        # step past the earlier entries to reach them - see the module
        # docstring for why that's a smaller saving than a real cursor.
        new_entries = itertools.islice(actions, seen, None)
        met = trigger.kind.is_met(snapshot, new_entries)
        self._action_seen_length[trigger.id] = total

        if met:
            self._action_matched.add(trigger.id)

        return met
