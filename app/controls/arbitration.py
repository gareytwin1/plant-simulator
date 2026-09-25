"""
Command arbitration: the one place that decides what a final element is told (T7-4).

Three subsystems will want to move the same valve, and each has a reason to
think it should win: an interlock driving it to its safe position, an operator
taking it in manual, and a controller trimming it in auto. If each wrote the
device directly, the last writer in a step would win, so the outcome would
depend on execution order instead of on who has authority.

Instead each source posts a *standing* demand against a named output, and the
arbiter resolves every output by one fixed precedence:

    interlock  >  operator  >  controller

A demand stays in force until its requester releases it. That is what makes
precedence mean something across steps rather than only within one: a
tripped valve stays at the interlock's demand however often the operator or
the controller re-posts, and falls back to the next source only when the trip
is released. Resolution reads the demands held at that moment and never the
order they arrived in, so it cannot depend on which subsystem ran first.

Several requesters of one source may hold the same output at once, e.g. two
trips that both close one valve. Releasing one leaves the other in force. They
must agree, though: a second requester posting a different value from the same
source is a configuration fault, and it raises rather than letting arrival
order pick between them. Values that differ only by float rounding, within
`AGREEMENT`, count as agreeing.

An output is bound once, to the setter that moves its device's slow-state
target (`ControlValve.set_position_target`, for instance). `apply()` is the
only thing that calls it. The device keeps its own travel limits and stroke
rate; the arbiter decides *which* value it is sent, never how the device gets
there. An output nobody is demanding is left alone, holding its last target.
Every output is written even if another's actuator raises, so one faulty
device cannot stop a trip reaching the rest; the failures are raised together
afterwards.

Nothing here reads the plant or knows about time: arbitration is a pure
decision over the demands posted, and `apply()` is idempotent.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class Source(StrEnum):
    INTERLOCK = "interlock"
    OPERATOR = "operator"
    CONTROLLER = "controller"


PRECEDENCE = (
    Source.INTERLOCK,
    Source.OPERATOR,
    Source.CONTROLLER,
)

AGREEMENT = 1e-9

Actuator = Callable[[float], None]


class ConflictingDemand(ValueError):
    pass


@dataclass(frozen=True)
class Resolution:
    output: str
    value: float
    source: Source
    requesters: tuple[str, ...]


class CommandArbiter:
    def __init__(self) -> None:
        self._actuators: dict[str, Actuator] = {}
        self._demands: dict[str, dict[Source, dict[str, float]]] = {}

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(self._actuators)

    def bind(self, output: str, actuator: Actuator) -> None:
        if output in self._actuators:
            raise ValueError(f"output {output!r} is already bound")

        self._actuators[output] = actuator
        self._demands[output] = {source: {} for source in PRECEDENCE}

    def demand(
        self,
        output: str,
        source: Source,
        requester: str,
        value: float,
    ) -> None:
        held = self._held(output, Source(source))

        if not math.isfinite(value):
            raise ValueError(
                f"{requester!r} demanded a non-finite value for "
                f"{output!r}: {value}",
            )

        disagreeing = sorted(
            other
            for other, other_value in held.items()
            if other != requester
            and not math.isclose(other_value, value, abs_tol=AGREEMENT)
        )

        if disagreeing:
            raise ConflictingDemand(
                f"{source} {requester!r} demands {value} on {output!r}, "
                f"but {', '.join(repr(other) for other in disagreeing)} "
                f"already holds {held[disagreeing[0]]}",
            )

        held[requester] = float(value)

    def release(
        self,
        output: str,
        source: Source,
        requester: str,
    ) -> None:
        self._held(output, Source(source)).pop(requester, None)

    def resolve(self, output: str) -> Resolution | None:
        if output not in self._demands:
            raise KeyError(f"output {output!r} is not bound")

        for source in PRECEDENCE:
            held = self._demands[output][source]

            if held:
                requesters = tuple(sorted(held))

                return Resolution(
                    output=output,
                    value=held[requesters[0]],
                    source=source,
                    requesters=requesters,
                )

        return None

    def apply(self) -> None:
        resolved = [
            (self._actuators[output], resolution.value)
            for output in self._actuators
            if (resolution := self.resolve(output)) is not None
        ]
        failures: list[Exception] = []

        for actuator, value in resolved:
            try:
                actuator(value)
            except Exception as failure:
                failures.append(failure)

        if failures:
            raise ExceptionGroup("actuator writes failed", failures)

    def _held(self, output: str, source: Source) -> dict[str, float]:
        if output not in self._demands:
            raise KeyError(f"output {output!r} is not bound")

        return self._demands[output][source]
