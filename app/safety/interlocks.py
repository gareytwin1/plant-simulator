"""
Interlock definitions and evaluator (T11-1, contract C3's `interlocks` key) -
pure condition evaluation, no plant dependency, no action execution.

An `Interlock` binds `tag` (the interlock's own instrument tag, e.g.
"PSHH-101"), `condition` (a "<device tag>.<variable> <op> <threshold>"
expression, parsed by `Condition.parse`), `delay_s` (the operator's recovery
window - how long the condition must persist before it latches), `actions`
(opaque strings naming what a trip should do - carried through untouched;
only T11-2 interprets and executes them, through the arbitration layer, so
this module never touches a device) and `reset` (`"manual"` or `"auto"`).

Like T9-1's `Evaluator` and T10-1's `Alarm`, `Interlock.evaluate()` is a
deterministic function of its arguments and the state left by the previous
call - no wall clock, no plant - so a caller supplies elapsed time as `dt`
the same way the solver's own devices do, and supplies the current value of
whatever the condition names. This module does no lookup of its own; a
caller resolves `tag.variable` to a live value the same way T9-4 resolves a
`limits` entry against the snapshot.

**`delay_s` is the trip delay, not a debounce.** The raw condition has to
hold continuously for the whole delay before the interlock latches to
TRIPPED - a single `evaluate()` call whose `dt` alone reaches `delay_s` trips
immediately, and the condition clearing at any point before that resets the
accumulated time to zero rather than pausing it. That is deliberately the
"operator's recovery window" the build plan names: a transient has exactly
`delay_s` seconds to clear before the trip becomes irreversible without a
reset.

**`reset` decides what happens once TRIPPED.** `"auto"` clears itself the
moment the raw condition is no longer met - the very next `evaluate()` call
does it, no operator action needed. `"manual"` stays TRIPPED regardless of
the condition until `reset()` is called explicitly, and `reset()` itself
refuses while the condition most recently evaluated is still met - real
interlock hardware does not let an operator paper over an ongoing trip
condition by resetting through it.

**A non-finite reading fails safe.** `Condition.is_met()` treats a NaN or
infinite `value` as met regardless of operator - the opposite of what a bare
comparison would do, since every comparison except `!=` is `False` against
NaN. A lost or garbage transmitter reading is exactly the situation an
interlock exists to catch, so it drives the same timer a real excursion
would rather than silently reading as healthy, clearing a TRIPPED `"auto"`
interlock, or letting a manual `reset()` through.
"""

from __future__ import annotations

import math
import re
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

Reset = Literal["manual", "auto"]

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}

# Longest operators first: alternation takes the first branch that matches at
# the current position, so "<" ahead of "<=" would swallow the "<" out of an
# "<=" and leave a stray "=" for the threshold group to choke on.
_CONDITION_RE = re.compile(
    r"^\s*(?P<tag>[^\s.]+)\.(?P<variable>[^\s<>=!]+)\s*"
    r"(?P<op><=|>=|==|!=|<|>)\s*"
    r"(?P<threshold>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$"
)


@dataclass(frozen=True)
class Condition:
    """One `tag.variable <op> threshold` comparison, parsed from C3's
    `interlocks[].condition` string. Mirrors T9-2's `(tag, variable)` limit
    key - a caller resolves the live value the same way `app.envelope.loader`
    does - but names its own operator and threshold rather than picking from
    T9-1's fixed six-limit shape: a trip condition is one comparison, not a
    band."""

    tag: str
    variable: str
    operator: str
    threshold: float

    def __post_init__(self) -> None:
        if self.operator not in _OPERATORS:
            raise ValueError(f"unknown operator {self.operator!r}")

    @classmethod
    def parse(cls, text: str) -> Condition:
        match = _CONDITION_RE.match(text)
        if match is None:
            raise ValueError(f"malformed interlock condition: {text!r}")
        return cls(
            tag=match["tag"],
            variable=match["variable"],
            operator=match["op"],
            threshold=float(match["threshold"]),
        )

    def is_met(self, value: float) -> bool:
        if not math.isfinite(value):
            return True
        return _OPERATORS[self.operator](value, self.threshold)


@dataclass(frozen=True)
class InterlockDefinition:
    tag: str
    condition: Condition
    delay_s: float
    actions: tuple[str, ...]
    reset: Reset

    def __post_init__(self) -> None:
        if not math.isfinite(self.delay_s) or self.delay_s < 0.0:
            raise ValueError(f"delay_s must be a finite, non-negative number, got {self.delay_s}")
        if not self.actions:
            raise ValueError("actions must not be empty")
        if self.reset not in ("manual", "auto"):
            raise ValueError(f"reset must be 'manual' or 'auto', got {self.reset!r}")


class InterlockState(StrEnum):
    NORMAL = "normal"
    PENDING = "pending"
    TRIPPED = "tripped"


class Interlock:
    def __init__(self, definition: InterlockDefinition) -> None:
        self.definition = definition
        self._state = InterlockState.NORMAL
        self._pending_elapsed = 0.0
        self._condition_met = False

    @property
    def state(self) -> InterlockState:
        return self._state

    @property
    def tripped(self) -> bool:
        return self._state is InterlockState.TRIPPED

    @property
    def pending_elapsed(self) -> float:
        return self._pending_elapsed

    def evaluate(self, value: float, dt: float) -> InterlockState:
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError(f"dt must be a finite, non-negative number, got {dt}")

        self._condition_met = self.definition.condition.is_met(value)

        if self._state is InterlockState.TRIPPED:
            if self.definition.reset == "auto" and not self._condition_met:
                self._clear()
            return self._state

        if self._condition_met:
            self._pending_elapsed += dt
            if self._delay_elapsed():
                self._state = InterlockState.TRIPPED
                self._pending_elapsed = 0.0
            else:
                self._state = InterlockState.PENDING
        else:
            self._clear()

        return self._state

    def _delay_elapsed(self) -> bool:
        # A plain >= can miss by float noise: ten additions of 0.1 sum to
        # 0.9999999999999999, one ULP short of a 1.0 delay, which would trip
        # the interlock one whole step late. isclose absorbs that without
        # opening a real early-trip window - the tolerance is far below any
        # dt this simulator uses.
        return self._pending_elapsed >= self.definition.delay_s or math.isclose(
            self._pending_elapsed, self.definition.delay_s, rel_tol=1e-9, abs_tol=1e-9
        )

    def reset(self) -> bool:
        """Manually clear a TRIPPED interlock. Returns whether it took
        effect: a no-op when not TRIPPED, when `definition.reset` is
        `"auto"` (`evaluate()` already clears those on its own), or when the
        condition most recently evaluated is still met."""
        if (
            self._state is not InterlockState.TRIPPED
            or self.definition.reset != "manual"
            or self._condition_met
        ):
            return False
        self._clear()
        return True

    def _clear(self) -> None:
        self._state = InterlockState.NORMAL
        self._pending_elapsed = 0.0


def load_interlocks(config: Mapping[str, Any]) -> dict[str, Interlock]:
    """Build one `Interlock` per entry in `config["interlocks"]` - the C3
    passthrough section `app/plant/loader.py` carries untouched. `config` is
    decoded JSON or YAML of the shape `app.plant.validate.validate()` checks
    (`Any` per .claude/rules/python.md). A config with no `interlocks` key at
    all yields an empty mapping: not every plant defines one.

    A repeated `tag` is rejected the same way T9-2's `load_limits` rejects a
    repeated `(tag, variable)` - nothing else about the section enforces
    uniqueness, so a copy-pasted entry left with the old tag would otherwise
    discard a real interlock without any signal that it happened.
    """
    interlocks: dict[str, Interlock] = {}

    for entry in config.get("interlocks", []):
        tag = entry["tag"]

        if tag in interlocks:
            raise ValueError(f"interlock {tag} is defined more than once")

        try:
            condition = Condition.parse(entry["condition"])
            definition = InterlockDefinition(
                tag=tag,
                condition=condition,
                delay_s=entry["delay_s"],
                actions=tuple(entry["actions"]),
                reset=entry["reset"],
            )
        except ValueError as exc:
            raise ValueError(f"interlock {tag}: {exc}") from exc

        interlocks[tag] = Interlock(definition)

    return interlocks


def get_interlock(interlocks: Mapping[str, Interlock], tag: str) -> Interlock | None:
    """Look up one interlock by tag, warning rather than crashing if absent -
    mirrors T9-2's `get_limit`."""
    if tag not in interlocks:
        warnings.warn(f"no interlock configured for {tag}", stacklevel=2)
        return None

    return interlocks[tag]
