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
`app.disturbances.malfunction.StartCondition`. `TriggerEvaluator` is the one
place that remembers which one-shot triggers have already fired; a trigger
without `one_shot` re-evaluates fresh every call and may fire on every step
its condition holds, which is the point for something like "discharge
pressure is high" driving a continuous alarm-style cue.

A condition string is compiled once, at construction, into a `Condition`:
evaluating it each step is a handful of dict lookups and one comparison, not
a re-parse, which is what keeps trigger evaluation from adding measurable
per-step cost.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
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
    rf"(?P<value>-?\d+(?:\.\d+)?)$",
)
_BARE = re.compile(rf"^(?P<tag>{_TAG})\.(?P<field>{_FIELD})$")


@dataclass(frozen=True)
class Condition:
    """A compiled `TAG.field [OP value]` expression.

    `op is None` means a bare truthy check on the field's own value, the
    shape an objective's `"K-101.tripped"` failure condition needs.
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
            return cls(
                tag=match["tag"],
                field=match["field"],
                op=match["op"],
                value=float(match["value"]),
            )

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
            # String equality ("PIC-101.mode == MANUAL") is a real gap - out
            # of scope for T14-2, a C8 grammar decision for whoever needs it.
            # A bare check is deliberately narrow rather than silently true
            # for a non-boolean field.
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


def _lookup(snapshot: Snapshot, tag: str, field: str) -> JSONValue:
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

    row = found[0][1]

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
    """Whether a trigger fires now. Reads the snapshot and action log only."""

    def is_met(self, snapshot: Snapshot, actions: Sequence[ActionEvent]) -> bool: ...


@dataclass(frozen=True)
class TimeTrigger:
    """Fires on every step from `sim_time` onward."""

    sim_time: float

    def is_met(self, snapshot: Snapshot, actions: Sequence[ActionEvent]) -> bool:
        return snapshot.sim_time >= self.sim_time


@dataclass(frozen=True)
class ConditionTrigger:
    """Fires on every step the compiled condition holds against the snapshot."""

    condition: Condition

    def is_met(self, snapshot: Snapshot, actions: Sequence[ActionEvent]) -> bool:
        return self.condition.is_met(snapshot)


@dataclass(frozen=True)
class OperatorActionTrigger:
    """Fires on every step where `tag` has ever taken `action` in the log."""

    tag: str
    action: str

    def is_met(self, snapshot: Snapshot, actions: Sequence[ActionEvent]) -> bool:
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
        trigger_id = config["id"]
        trigger_type = config["type"]
        kind: TriggerKind

        if trigger_type == "time":
            kind = TimeTrigger(sim_time=float(_required(config, "sim_time", trigger_id)))
        elif trigger_type == "condition":
            kind = ConditionTrigger(
                condition=Condition.parse(_required(config, "condition", trigger_id)),
            )
        elif trigger_type == "operator_action":
            tag, action = _split_action(_required(config, "action", trigger_id))
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


class TriggerEvaluator:
    """Evaluates a scenario's triggers each step, honouring one-shot.

    The only state owned here is which one-shot triggers have already
    fired - every other trigger's `is_met` is re-checked fresh on each call,
    exactly as `MalfunctionRegistry` tracks a malfunction's onset outside its
    stateless `StartCondition`.
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

    @classmethod
    def from_config(cls, triggers: Iterable[Mapping[str, Any]]) -> TriggerEvaluator:
        return cls(Trigger.from_config(config) for config in triggers)

    def validate(self, snapshot: Snapshot) -> None:
        """Resolve every condition trigger's tag, field and comparison now.

        Raises the same `ConditionEvaluationError` `evaluate()` would, but up
        front - at scenario load or arm time - rather than out of the step
        loop partway through a run. Time and operator-action triggers need no
        snapshot to be well-formed, so there is nothing here to check for
        them.
        """
        for trigger in self._triggers:
            if isinstance(trigger.kind, ConditionTrigger):
                trigger.kind.condition.is_met(snapshot)

    def evaluate(self, snapshot: Snapshot, actions: ActionLog) -> tuple[str, ...]:
        """Return the ids of every trigger that fires on this step."""
        total = len(actions)
        events: Sequence[ActionEvent] = ()
        if any(self._needs_the_log(trigger, total) for trigger in self._triggers):
            events = actions.events

        fired: list[str] = []

        for trigger in self._triggers:
            if trigger.one_shot and trigger.id in self._fired:
                continue

            if isinstance(trigger.kind, OperatorActionTrigger):
                met = self._operator_action_is_met(trigger, total, snapshot, events)
            else:
                met = trigger.kind.is_met(snapshot, events)

            if met:
                fired.append(trigger.id)

                if trigger.one_shot:
                    self._fired.add(trigger.id)

        return tuple(fired)

    def _needs_the_log(self, trigger: Trigger, total: int) -> bool:
        # Only an operator_action trigger ever reads the log; a time or
        # condition trigger's is_met() ignores the `actions` argument.
        if not isinstance(trigger.kind, OperatorActionTrigger):
            return False

        if trigger.one_shot and trigger.id in self._fired:
            return False

        if trigger.id in self._action_matched:
            return False

        # Nothing new since the last scan means nothing new to match either.
        return self._action_seen_length.get(trigger.id, 0) != total

    def _operator_action_is_met(
        self,
        trigger: Trigger,
        total: int,
        snapshot: Snapshot,
        events: Sequence[ActionEvent],
    ) -> bool:
        if trigger.id in self._action_matched:
            return True

        if self._action_seen_length.get(trigger.id, 0) == total:
            return False

        met = trigger.kind.is_met(snapshot, events)
        self._action_seen_length[trigger.id] = total

        if met:
            self._action_matched.add(trigger.id)

        return met
