"""
Injection profiles and condition onset: the rest of C8's malfunction half (T13-3).

`app.disturbances.malfunction` defines the two protocols a malfunction is
shaped by, and ships the minimal `Step` and `AtTime`. This module adds the two
that make a fault gradual or consequential:

    Ramp            the change arrives linearly over `duration_s`, so a trend
                    moves before any alarm does
    WhenCondition   onset waits for a "TAG.field OP value" comparison against
                    the snapshot, reusing the trigger grammar

Both are pure. `Ramp` reads only `elapsed`; `WhenCondition` reads only the
snapshot it is handed. Neither holds state: a condition onset fires once
because the registry latches the first update that sees it met and captures the
original value there. A condition that clears afterwards does not revert the
fault - `MalfunctionRegistry.revert` does.

`profile_from_config` and `start_condition_from_config` decode a scenario's
tagged-union entries and refuse what they do not know by name.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.disturbances.malfunction import AtTime, Profile, StartCondition, Step
from app.engine.snapshot import Snapshot
from app.scenarios.triggers import Condition


@dataclass(frozen=True)
class Ramp:
    """Linear from the original value at onset to the malfunction's value."""

    duration_s: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.duration_s, bool)
            or not isinstance(self.duration_s, (int, float))
            or not math.isfinite(self.duration_s)
            or self.duration_s <= 0
        ):
            raise ValueError(f"ramp duration_s must be a finite number above 0, got {self.duration_s!r}")

    def fraction(self, elapsed: float) -> float:
        return min(max(elapsed / self.duration_s, 0.0), 1.0)


@dataclass(frozen=True)
class WhenCondition:
    """Starts on the first update whose snapshot satisfies `condition`."""

    condition: str
    _compiled: Condition = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", Condition.parse(self.condition))

    def is_met(self, snapshot: Snapshot) -> bool:
        return self._compiled.is_met(snapshot)


def profile_from_config(config: Mapping[str, Any], where: str) -> Profile:
    # Any: a decoded scenario entry; its shape is what this function checks.
    kind = config.get("type")

    if kind == "step" and set(config) == {"type"}:
        return Step()

    if kind == "ramp" and set(config) == {"type", "duration_s"}:
        try:
            return Ramp(duration_s=config["duration_s"])
        except ValueError as error:
            raise ValueError(f"{where} {error}") from None

    raise ValueError(
        f"{where} has profile {dict(config)!r}; supported are "
        f"{{'type': 'step'}} and {{'type': 'ramp', 'duration_s': <seconds above 0>}}",
    )


def start_condition_from_config(config: Mapping[str, Any], where: str) -> StartCondition:
    # Any: a decoded scenario entry; its shape is what this function checks.
    kind = config.get("type")

    if kind == "at_time" and set(config) <= {"type", "sim_time"}:
        return AtTime(sim_time=config.get("sim_time", 0))

    if kind == "condition" and set(config) == {"type", "condition"}:
        try:
            return WhenCondition(condition=config["condition"])
        except ValueError as error:
            raise ValueError(f"{where} {error}") from None

    raise ValueError(
        f"{where} has start_condition {dict(config)!r}; supported are 'at_time' "
        f"with an optional sim_time and 'condition' with a condition string",
    )
