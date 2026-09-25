"""
State snapshot — Contract C4.

The single object every consumer reads: the historian, the console, the
trend API, the scoring module and the scenario engine all take this and
nothing else. Freezing its shape is what lets the rest of the system be
built before the physics behind it exists.

Sections that don't have a subsystem yet — controllers, envelope, alarms —
are present but empty, and solver reports a trivial placeholder until
something on the request path actually solves a network. nodes and streams
are not in that category: they carry real solved numbers for any Engine
built from a plant, since T4-4. The shape is fixed now; the content fills in
as each milestone lands. Nothing in a Snapshot can be mutated after it is
built:
every mapping is a MappingProxyType over a deep copy of its input, and
alarms is a tuple of the same, so a consumer holding a reference cannot
corrupt what another consumer already read, and mutating the caller's
original input after the fact cannot leak into a Snapshot already built
from it.
"""

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from app.statetypes import JSONValue


Section = MappingProxyType[str, Mapping[str, JSONValue]]

# C4's solver section, in full. A solve reports more than this — which of the
# pressure and flow residuals was the worst, and why a failed solve stopped —
# and that stays on the solver's own result: widening the section is a change
# to C4, made as its own task, not a field a caller adds in passing.
SOLVER_KEYS = (
    "converged",
    "iterations",
    "residual",
)

# What the section says before anything has solved a network. Honest rather
# than optimistic: nothing has been asked of a solver, so there is no
# unconverged solve being hidden. It stops being the answer at T4-4, when the
# engine starts handing real diagnostics in.
DEFAULT_SOLVER_STATUS: Mapping[str, JSONValue] = {
    "converged": True,
    "iterations": 0,
    "residual": 0.0,
}


class SolverDiagnostics(Protocol):
    """What C4 needs a solve result to be.

    Structural on purpose: C4 is the read contract every consumer depends
    on and it must not depend on the solver in turn.
    `app.engine.network.SolverResult` satisfies this, and so does anything
    else that reports the same three numbers.
    """

    @property
    def converged(self) -> bool: ...

    @property
    def iterations(self) -> int: ...

    @property
    def residual(self) -> float: ...


def solver_status(result: SolverDiagnostics) -> dict[str, JSONValue]:
    """Project a solve result onto C4's solver section.

    The one sanctioned way a solve reaches a snapshot, and the reason it
    exists is `converged`: it is read straight off the result, so a solve
    that failed cannot arrive at a consumer looking like one that landed,
    and the iteration count and residual that came with it are there to say
    how badly. Richer diagnostics stay behind this projection — see
    SOLVER_KEYS.
    """
    return {
        "converged": result.converged,
        "iterations": result.iterations,
        "residual": result.residual,
    }


@dataclass(frozen=True)
class Snapshot:
    sim_time: float
    speed: float
    running: bool
    equipment: Section
    nodes: Section
    streams: Section
    controllers: Section
    envelope: Section
    alarms: tuple[Mapping[str, JSONValue], ...]
    solver: MappingProxyType[str, JSONValue]

    def as_dict(self) -> dict[str, JSONValue]:
        """Flat, JSON-safe dict matching C4 exactly."""
        return {
            "sim_time": self.sim_time,
            "speed": self.speed,
            "running": self.running,
            "equipment": _thawed(self.equipment),
            "nodes": _thawed(self.nodes),
            "streams": _thawed(self.streams),
            "controllers": _thawed(self.controllers),
            "envelope": _thawed(self.envelope),
            "alarms": [dict(alarm) for alarm in self.alarms],
            "solver": dict(self.solver),
        }


def build_snapshot(
    sim_time: float,
    speed: float,
    running: bool,
    equipment: Mapping[str, Mapping[str, JSONValue]],
    nodes: Mapping[str, Mapping[str, JSONValue]] | None = None,
    streams: Mapping[str, Mapping[str, JSONValue]] | None = None,
    controllers: Mapping[str, Mapping[str, JSONValue]] | None = None,
    envelope: Mapping[str, Mapping[str, JSONValue]] | None = None,
    alarms: Iterable[Mapping[str, JSONValue]] | None = None,
    solver: Mapping[str, JSONValue] | None = None,
) -> Snapshot:
    """Assemble an immutable Snapshot from plain mutable inputs.

    Every per-tag state dict and every alarm is deep-copied before being
    frozen, so nothing the caller still owns — and later mutates — can
    leak through into a Snapshot already built from it.

    `solver` is either nothing at all — omitted for the default placeholder,
    or `{}` for "no solve to report" — or C4's three keys in full. See
    `_validated_solver` for why half a solver section is refused rather than
    passed through.
    """
    sections = {"nodes": nodes, "streams": streams, "controllers": controllers, "envelope": envelope}
    frozen_sections = {
        name: _frozen(value or {})
        for name, value in sections.items()
    }

    return Snapshot(
        sim_time=sim_time,
        speed=speed,
        running=running,
        equipment=_frozen(equipment),
        alarms=tuple(
            MappingProxyType(copy.deepcopy(alarm))
            for alarm in (alarms or ())
        ),
        solver=MappingProxyType(
            copy.deepcopy(
                DEFAULT_SOLVER_STATUS
                if solver is None
                else _validated_solver(solver)
            )
        ),
        **frozen_sections,
    )


def _validated_solver(
    solver: Mapping[str, JSONValue],
) -> Mapping[str, JSONValue]:
    """Refuse a solver section that is neither empty nor C4's full triple.

    A partial section is the case worth catching. One carrying an iteration
    count and a residual but no `converged` reads as a clean solve to every
    consumer that treats the flag as optional, which is the silent failure
    the solver's diagnostics exist to prevent — a snapshot must not be able
    to imply a solve landed by saying nothing about it. An unexpected key is
    refused for the other reason: C4's shape is frozen, and a section
    quietly growing a field is a contract change made by accident.

    Empty stays legal and means what it says: no solve is being reported.
    """
    if not solver:
        return solver

    missing = [key for key in SOLVER_KEYS if key not in solver]
    unexpected = sorted(set(solver) - set(SOLVER_KEYS))

    if missing or unexpected:
        raise ValueError(
            f"a snapshot's solver section is C4's {list(SOLVER_KEYS)} in full "
            f"or empty, never part of one: missing {missing}, unexpected "
            f"{unexpected}",
        )

    return solver


def _frozen(mapping: Mapping[str, Mapping[str, JSONValue]]) -> Section:
    return MappingProxyType(
        {
            tag: MappingProxyType(copy.deepcopy(state))
            for tag, state in mapping.items()
        }
    )


def _thawed(mapping: Mapping[str, Mapping[str, JSONValue]]) -> dict[str, JSONValue]:
    return {
        tag: dict(state)
        for tag, state in mapping.items()
    }
