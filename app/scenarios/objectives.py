"""
Objective evaluator - contract C8's scenario-objective half (T14-3).

An objective is a goal an operator is asked to reach: "return discharge
pressure to normal and hold it for five minutes without a trip". It has three
ways to end, and this module evaluates all of them each step:

    success   `success.condition` holds, continuously, for `hold_duration_s`
    failure   `failure.condition` holds at any point before success
    timeout   the scenario's `time_limit_s` elapses with neither

Conditions are the same compiled `TAG.field [OP value]` expressions a
trigger uses (`app.scenarios.triggers.Condition`), read against the snapshot
and nothing else, so an objective sees exactly what an operator's console
sees and never reaches into the physics.

A hold is measured in simulated time from `snapshot.sim_time`, never a
wall clock: the timer starts on the first step the success condition holds
and restarts from scratch on any step it does not. An objective ends once -
success, failure and timeout are terminal and latch - and an ended objective
is no longer evaluated, so a tag that later disappears from the snapshot
cannot raise out of a finished run. On a step where more than one ending is
reachable the order is failure, then success, then timeout: a trip during the
last second of a hold still fails it, and reaching the goal on the step the
limit is hit still counts.

`evaluate()` is atomic, as `TriggerEvaluator.evaluate()` is: nothing is
written to the evaluator until every objective on the step has been
evaluated without raising.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.engine.snapshot import Snapshot
from app.scenarios.triggers import Condition


class ObjectiveStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ObjectiveResult:
    """One objective's standing after a step. `ended_at` is None while pending."""

    id: str
    status: ObjectiveStatus
    ended_at: float | None = None


@dataclass(frozen=True)
class Objective:
    """One scenario objective: what succeeds it, what fails it, how long to hold."""

    id: str
    success: Condition
    hold_duration_s: float = 0.0
    failure: Condition | None = None

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Objective:
        # Any: decoded scenario JSON, a shape nothing knows yet - every
        # field is validated below before it reaches a typed attribute.
        if "id" not in config:
            raise ValueError(f"an objective config is missing 'id': {config!r}")

        objective_id = config["id"]
        if not isinstance(objective_id, str) or not objective_id:
            raise ValueError(f"an objective id must be a non-empty string, got {objective_id!r}")

        unexpected = sorted(set(config) - {"id", "success", "failure"})
        if unexpected:
            raise ValueError(f"objective {objective_id!r} has unexpected key(s) {unexpected}")

        if "success" not in config:
            raise ValueError(f"objective {objective_id!r} is missing 'success'")

        success = _section(config["success"], objective_id, "success", {"hold_duration_s"})
        hold = _hold_duration(success.get("hold_duration_s", 0), objective_id)

        failure = None
        if "failure" in config:
            failure_section = _section(config["failure"], objective_id, "failure", set())
            failure = Condition.parse(_condition_text(failure_section, objective_id, "failure"))

        return cls(
            id=objective_id,
            success=Condition.parse(_condition_text(success, objective_id, "success")),
            hold_duration_s=hold,
            failure=failure,
        )


def _section(value: Any, objective_id: str, name: str, extra_keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"objective {objective_id!r} has {name}={value!r}, which is not an object")

    unexpected = sorted(set(value) - {"condition"} - extra_keys)
    if unexpected:
        raise ValueError(f"objective {objective_id!r} {name} has unexpected key(s) {unexpected}")

    return value


def _condition_text(section: Mapping[str, Any], objective_id: str, name: str) -> str:
    if "condition" not in section:
        raise ValueError(f"objective {objective_id!r} {name} has no 'condition'")

    text = section["condition"]
    if not isinstance(text, str):
        raise ValueError(f"objective {objective_id!r} {name} condition {text!r} is not a string")

    return text


def _hold_duration(value: Any, objective_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"objective {objective_id!r} has hold_duration_s={value!r}, which is not a number")

    try:
        hold = float(value)
    except OverflowError:
        raise ValueError(f"objective {objective_id!r} has hold_duration_s={value!r}, which is too large") from None

    if not math.isfinite(hold):
        raise ValueError(f"objective {objective_id!r} has hold_duration_s={value!r}, which is not finite")

    if hold < 0:
        raise ValueError(
            f"objective {objective_id!r} has hold_duration_s={hold!r}, which is "
            f"negative; scenario.schema.json requires 0 or more",
        )

    return hold


class ObjectiveEvaluator:
    """Evaluates one scenario run's objectives each step.

    The state that makes an objective more than a pure condition check lives
    here: when each pending hold began, and each objective's latched ending.
    It is scoped to a single run - starting another (a reset or replay)
    means constructing a fresh `ObjectiveEvaluator` over the same,
    already-compiled `Objective` tuple.

    `time_limit_s` is the scenario's own limit, not a per-objective one; None
    means the run has no limit and no objective can time out.
    """

    def __init__(self, objectives: Iterable[Objective], time_limit_s: float | None = None) -> None:
        self._objectives = tuple(objectives)

        seen: set[str] = set()
        for objective in self._objectives:
            if objective.id in seen:
                raise ValueError(f"duplicate objective id {objective.id!r}")
            seen.add(objective.id)

        if time_limit_s is not None and (not math.isfinite(time_limit_s) or time_limit_s < 0):
            raise ValueError(f"time_limit_s must be a finite number of 0 or more, got {time_limit_s!r}")

        self._time_limit_s = time_limit_s
        self._held_since: dict[str, float] = {}
        self._ended: dict[str, ObjectiveResult] = {}

    @classmethod
    def from_config(
        cls,
        objectives: Iterable[Mapping[str, Any]],
        time_limit_s: float | None = None,
    ) -> ObjectiveEvaluator:
        return cls((Objective.from_config(config) for config in objectives), time_limit_s)

    @property
    def resolved(self) -> bool:
        """True once every objective has ended, so a runner may stop asking.

        Vacuously true for a scenario with no objectives: there is nothing left to wait for.
        """
        return len(self._ended) == len(self._objectives)

    def validate(self, snapshot: Snapshot) -> None:
        """Resolve every condition against `snapshot` now, at load or arm time.

        Raises the same `ConditionEvaluationError` `evaluate()` would, but
        up front rather than out of the step loop partway through a run.
        """
        for objective in self._objectives:
            objective.success.is_met(snapshot)

            if objective.failure is not None:
                objective.failure.is_met(snapshot)

    def evaluate(self, snapshot: Snapshot) -> tuple[ObjectiveResult, ...]:
        """Return every objective's standing after this step, in config order."""
        now = snapshot.sim_time
        timed_out = self._time_limit_s is not None and now >= self._time_limit_s

        # Nothing below touches self until the whole loop has finished
        # without raising - see the module docstring.
        ended = dict(self._ended)
        held_since = dict(self._held_since)
        results: list[ObjectiveResult] = []

        for objective in self._objectives:
            latched = ended.get(objective.id)
            if latched is not None:
                results.append(latched)
                continue

            result = self._step(objective, snapshot, now, timed_out, held_since)

            if result.status is not ObjectiveStatus.PENDING:
                ended[objective.id] = result
                held_since.pop(objective.id, None)

            results.append(result)

        self._ended = ended
        self._held_since = held_since

        return tuple(results)

    @staticmethod
    def _step(
        objective: Objective,
        snapshot: Snapshot,
        now: float,
        timed_out: bool,
        held_since: dict[str, float],
    ) -> ObjectiveResult:
        if objective.failure is not None and objective.failure.is_met(snapshot):
            return ObjectiveResult(objective.id, ObjectiveStatus.FAILED, now)

        if objective.success.is_met(snapshot):
            started = held_since.setdefault(objective.id, now)

            if now - started >= objective.hold_duration_s:
                return ObjectiveResult(objective.id, ObjectiveStatus.SUCCEEDED, now)
        else:
            held_since.pop(objective.id, None)

        if timed_out:
            return ObjectiveResult(objective.id, ObjectiveStatus.TIMED_OUT, now)

        return ObjectiveResult(objective.id, ObjectiveStatus.PENDING)
