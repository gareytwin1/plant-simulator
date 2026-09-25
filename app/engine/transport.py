"""
Energy transport: where the temperature at every node and on every stream
comes from, once a domain has solved its flows - T6-5.

The solver says how much moves and which way. This module says how hot it is
when it gets there. It runs after a domain converges, reads the flows and
pressures the solver committed, and writes a temperature onto every branch's
stream. It moves no mass and touches no pressure, so nothing it does can
feed back into the solve of the same step.

**Upwind, and the sign is the direction.** A branch's upstream node is its
`from_node` when the flow is positive and its `to_node` when it is negative.
The fluid entering a branch is at its upstream node's temperature, whichever
end that is, and the stream a branch publishes is the fluid *leaving* it -
the temperature it delivers to its downstream node. Reversal is therefore not
a case: the field follows the flow because the flow's sign is the only thing
that says which node feeds which. Reading the upstream end off the branch's
orientation instead is the bug that inverts a temperature field when a
compressor surges.

**A node is a mixing point.** An internal node's temperature is the mix of
every stream arriving at it, weighted by `app.plant.thermo.mix_streams`'s
rule, and every stream leaving it leaves at that temperature. Mixing is
algebraic - a node holds no inventory - so the enthalpy leaving a node equals
the enthalpy arriving to rounding, and the plant-wide balance closes to
whatever the devices themselves add or remove.

**A boundary node is a reservoir.** It supplies whatever leaves it at its own
temperature, however much hotter the stream arriving at it is; what arrives
is visible on the arriving stream. A boundary with no temperature of its own
supplies at `STANDARD_TEMPERATURE`, the same default every `Stream` starts at.

**A node nothing flows into keeps the temperature it had.** A blocked-in
junction is full of fluid at some condition, and the condition it had is a
better answer than an average of lines that are not flowing. Exactly zero
flow arrives nowhere; a stopped machine's residual flow (see
.workspace/memory/project_state.md) arrives, and is weighted like any other.

**Devices.** A device implementing `thermo.ThermalDevice` sets the leaving
temperature from the arriving stream and the solved pressures at its ports'
nodes; any other device passes temperature through unchanged. The domain's
phase - declared on a port, else confirmed by the machines in the domain
exactly as the coupling confirms a flow unit - is what the arriving stream is
built with. A domain that nothing classifies carries temperature by flow
weight alone, which is `mix_streams`'s own rule for an unspecified fluid, and
refuses a thermal device, which cannot be handed a stream of unknown phase.

**Order.** Nodes are visited upstream first, so a series or branching plant
is exact in one sweep; a second sweep confirms it. A recirculation loop among
internal nodes has no upstream-first order, and is swept until it settles -
it settles whenever fresh feed enters the loop. A loop with a heat source and
no feed has no steady temperature at all; that raises rather than publishing
a number from whichever sweep happened to be last.

Composition is not transported. No boundary supplies one (C3 has no field for
it), so every stream carries the unspecified fluid it was built with.
"""

import math
from collections.abc import Mapping

from app.engine.coupling import PHASE_UNITS, flow_unit
from app.plant.thermo import StreamState, ThermalDevice, mix_streams
from app.plant.topology import STANDARD_TEMPERATURE, Branch, Topology


# Convergence of a recirculation loop, in °F. A loop-free plant never tests
# it: its confirming sweep recomputes identical floats.
SWEEP_TOLERANCE = 1e-9
MAX_SWEEPS = 200

ABSOLUTE_ZERO = -459.67  # °F

UNIT_PHASES = {
    unit: phase
    for phase, unit in PHASE_UNITS.items()
}


class DomainTransport:
    """Energy transport over one flow domain's topology.

    `temperatures` is the node temperature field, and the only state this
    holds: it is what a node nothing flows into keeps. Stream temperatures
    are written onto the topology's own streams, where C4 already reads them.
    """

    def __init__(
        self,
        domain: str,
        topology: Topology,
        boundary_temperatures: Mapping[str, float],
    ) -> None:
        self.domain = domain
        self.topology = topology
        self.phase = _domain_phase(domain, topology)

        if self.phase is None:
            thermal = sorted(
                branch.device.tag
                for branch in topology.branches.values()
                if isinstance(branch.device, ThermalDevice)
            )

            if thermal:
                raise ValueError(
                    f"domain {domain!r} carries {thermal}, which change "
                    f"temperature, but no port in it declares a phase and no "
                    f"machine in it confirms one - declare phase on a port, "
                    f"so the heat they add has a heat capacity to act on",
                )

        self.temperatures: dict[str, float] = {
            node_id: (
                boundary_temperatures.get(node_id, STANDARD_TEMPERATURE)
                if node.is_boundary
                else STANDARD_TEMPERATURE
            )
            for node_id, node in topology.nodes.items()
        }

    def propagate(self) -> None:
        """Recompute every node and stream temperature from the flows and
        pressures the solver last committed.

        Pure in the committed state: calling it twice on an unchanged plant
        changes nothing, which is what keeps stepping a stopped engine a
        no-op.
        """
        arrivals = self._arrivals()
        order = _upstream_first(arrivals)
        temperatures = dict(self.temperatures)

        for _ in range(MAX_SWEEPS):
            change = 0.0

            for node_id in order:
                temperature = self._mixed(arrivals[node_id], temperatures)

                if temperature is None:
                    continue

                change = max(change, abs(temperature - temperatures[node_id]))
                temperatures[node_id] = temperature

            if change <= SWEEP_TOLERANCE:
                break
        else:
            raise ValueError(
                f"domain {self.domain!r}: node temperatures did not settle in "
                f"{MAX_SWEEPS} sweeps - a recirculation loop with a heat "
                f"source and no fresh feed has no steady temperature",
            )

        self.temperatures = temperatures

        for branch in self.topology.branches.values():
            branch.stream.temperature = self._leaving(branch, temperatures)

    def _arrivals(self) -> dict[str, list[Branch]]:
        arrivals: dict[str, list[Branch]] = {
            node_id: []
            for node_id in self.topology.internal_nodes
        }

        for branch in self.topology.branches.values():
            downstream = _downstream(branch)

            if downstream is not None and downstream in arrivals:
                arrivals[downstream].append(branch)

        return arrivals

    def _mixed(
        self,
        branches: list[Branch],
        temperatures: Mapping[str, float],
    ) -> float | None:
        if not branches:
            return None

        streams = [
            (abs(branch.flow), self._leaving(branch, temperatures))
            for branch in branches
        ]

        if self.phase is None:
            return (
                sum(flow * temperature for flow, temperature in streams)
                / sum(flow for flow, _ in streams)
            )

        return mix_streams(
            StreamState(flow, temperature, self.phase)
            for flow, temperature in streams
        ).temperature

    def _leaving(
        self,
        branch: Branch,
        temperatures: Mapping[str, float],
    ) -> float:
        arriving = temperatures[_upstream(branch)]
        device = branch.device

        if not isinstance(device, ThermalDevice):
            return arriving

        assert self.phase is not None

        leaving = device.leaving_temperature(
            StreamState(branch.flow, arriving, self.phase),
            branch.from_node.pressure,
            branch.to_node.pressure,
        )

        if not math.isfinite(leaving):
            raise ValueError(
                f"{device.tag} on branch {branch.id!r} returned a leaving "
                f"temperature of {leaving} at flow {branch.flow} - a thermal "
                f"device must be finite at every finite input, zero flow "
                f"included",
            )

        return leaving


def _upstream(branch: Branch) -> str:
    """The node the fluid enters from. At exactly zero flow nothing enters,
    and the branch's own orientation stands in, so a dead line still reports
    what its inlet side is at."""
    return branch.to_node.id if branch.flow < 0.0 else branch.from_node.id


def _downstream(branch: Branch) -> str | None:
    if branch.flow > 0.0:
        return branch.to_node.id

    if branch.flow < 0.0:
        return branch.from_node.id

    return None


def _upstream_first(arrivals: Mapping[str, list[Branch]]) -> list[str]:
    """Internal nodes, each after every internal node that feeds it.

    Where a loop leaves nothing ready, the lowest id goes next, so the order
    is deterministic and the sweeps close the loop.
    """
    feeds = {
        node_id: {_upstream(branch) for branch in branches} & arrivals.keys()
        for node_id, branches in arrivals.items()
    }

    order: list[str] = []
    placed: set[str] = set()

    while len(order) < len(feeds):
        pending = sorted(feeds.keys() - placed)
        ready = [node_id for node_id in pending if feeds[node_id] <= placed]

        for node_id in ready or pending[:1]:
            order.append(node_id)
            placed.add(node_id)

    return order


def _domain_phase(domain: str, topology: Topology) -> str | None:
    """The one phase this domain carries: declared on a port where any port
    says, confirmed by the machines otherwise, and None where nothing does.

    Declarations and machines must agree, and must agree with each other -
    one domain carries one phase, which is the rule the coupling already
    enforces node by node.
    """
    phases: dict[str, str] = {}

    for branch in topology.branches.values():
        for port in (branch.from_port, branch.to_port):
            if port.phase is not None:
                phases.setdefault(
                    port.phase,
                    f"{branch.device.tag}.{port.name} declares it",
                )

        unit = flow_unit(branch.device)

        if unit in UNIT_PHASES:
            phases.setdefault(
                UNIT_PHASES[unit],
                f"{branch.device.tag} is written in {unit}",
            )

    if len(phases) > 1:
        raise ValueError(
            f"domain {domain!r} carries more than one phase: "
            f"{dict(sorted(phases.items()))} - one flow domain is one phase",
        )

    return next(iter(phases), None)
