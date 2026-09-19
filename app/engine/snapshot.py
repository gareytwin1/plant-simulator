"""
State snapshot — Contract C4.

The single object every consumer reads: the historian, the console, the
trend API, the scoring module and the scenario engine all take this and
nothing else. Freezing its shape is what lets the rest of the system be
built before the physics behind it exists.

Sections that don't have a subsystem yet — nodes, streams, controllers,
envelope, alarms — are present but empty, and solver reports a trivial
placeholder. The shape is fixed now; the content fills in as each
milestone lands. Nothing in a Snapshot can be mutated after it is built:
every mapping is a MappingProxyType over a deep copy of its input, and
alarms is a tuple of the same, so a consumer holding a reference cannot
corrupt what another consumer already read, and mutating the caller's
original input after the fact cannot leak into a Snapshot already built
from it.
"""

import copy
from dataclasses import dataclass
from types import MappingProxyType


DEFAULT_SOLVER_STATUS = {
    "converged": True,
    "iterations": 0,
    "residual": 0.0,
}


@dataclass(frozen=True)
class Snapshot:
    sim_time: float
    speed: float
    running: bool
    equipment: MappingProxyType
    nodes: MappingProxyType
    streams: MappingProxyType
    controllers: MappingProxyType
    envelope: MappingProxyType
    alarms: tuple
    solver: MappingProxyType

    def as_dict(self):
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
    sim_time,
    speed,
    running,
    equipment,
    nodes=None,
    streams=None,
    controllers=None,
    envelope=None,
    alarms=None,
    solver=None,
):
    """Assemble an immutable Snapshot from plain mutable inputs.

    Every per-tag state dict and every alarm is deep-copied before being
    frozen, so nothing the caller still owns — and later mutates — can
    leak through into a Snapshot already built from it.
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
            copy.deepcopy(DEFAULT_SOLVER_STATUS if solver is None else solver)
        ),
        **frozen_sections,
    )


def _frozen(mapping):
    return MappingProxyType(
        {
            tag: MappingProxyType(copy.deepcopy(state))
            for tag, state in mapping.items()
        }
    )


def _thawed(mapping):
    return {
        tag: dict(state)
        for tag, state in mapping.items()
    }
