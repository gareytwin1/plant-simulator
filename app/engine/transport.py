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
every stream arriving at it, and every stream leaving it leaves at that
temperature. The weights are `thermo.mix_streams`'s, flow times heat
capacity, and since composition is not transported (below) every stream in a
domain has the same heat capacity and the weight is the flow. Mixing is
algebraic - a node holds no inventory - so the enthalpy leaving a node equals
the enthalpy arriving, and the plant-wide balance closes to whatever the
devices themselves add or remove.

**A boundary node is a reservoir.** It supplies whatever leaves it at its own
temperature, however much hotter the stream arriving at it is; what arrives
is visible on the arriving stream. A boundary with no temperature of its own
supplies at `STANDARD_TEMPERATURE`, the same default every `Stream` starts at.

**What no feed reaches keeps the temperature it had.** A blocked-in junction
or a dead line is full of fluid at some condition, and the condition it had
is a better answer than one computed from lines that are not flowing. That
covers a branch at exactly zero flow, and every internal node no flowing path
from a boundary reaches - including a loop circulating with no feed, whose
heating is a holdup dynamic this algebraic model does not have. A stopped
machine's residual flow (see .workspace/memory/project_state.md) is flow, and
is weighted like any other.

**Devices.** A device implementing `thermo.ThermalDevice` sets the leaving
temperature from the arriving stream and the solved pressures at its ports'
nodes; any other device passes temperature through unchanged. Which branches
are thermal is decided once, at construction. The domain's phase - declared
on a port, else confirmed by the machines in the domain exactly as the
coupling confirms a flow unit - is what the arriving stream is built with. A
domain that nothing classifies refuses a thermal device, which cannot be
handed a stream of unknown phase.

**Solve.** The fed nodes' temperatures are one small linear system: each row
says a node's flow-weighted temperature equals what arrives at it. A device
enters it linearised about the current iterate, and the system is re-solved
until the iterate stops moving. A pass-through or affine device - every one
V1 has, the compressor included - is exact on the first solve, so a series
plant, a branching one and a recirculation loop alike settle on the second.
Every solve starts from the same point, so the answer is a function of the
committed plant alone and stepping a stopped engine republishes it
bit-for-bit.

A loop whose devices return more heat than its feed can carry away has no
steady temperature. That is reported, not raised: the domain keeps the
temperatures it had, `settled` goes False and `failure` says why, the same
bargain a solve that does not converge makes. C4 carries no field for it;
widening the snapshot is its own task.

Composition is not transported. No boundary supplies one (C3 has no field for
it), so every stream carries the unspecified fluid it was built with.
"""

import math
from collections.abc import Mapping

from app.engine.coupling import PHASE_UNITS, flow_unit
from app.engine.network import SolverError, solve_linear
from app.plant.thermo import StreamState, ThermalDevice
from app.plant.topology import STANDARD_TEMPERATURE, Branch, Topology


# How far an iterate may move and still count as settled, in °F.
SETTLE_TOLERANCE = 1e-9
MAX_ITERATIONS = 50

ABSOLUTE_ZERO = -459.67  # °F

UNIT_PHASES = {
    unit: phase
    for phase, unit in PHASE_UNITS.items()
}


class DomainTransport:
    """Energy transport over one flow domain's topology.

    `temperatures` is the node temperature field. It is what a node no feed
    reaches keeps, and what a domain that did not settle holds. Stream
    temperatures are written onto the topology's own streams, where C4
    already reads them.
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

        self.thermal: dict[str, ThermalDevice] = {
            branch_id: branch.device
            for branch_id, branch in topology.branches.items()
            if isinstance(branch.device, ThermalDevice)
        }

        if self.phase is None and self.thermal:
            raise ValueError(
                f"domain {domain!r} carries thermal branches "
                f"{sorted(self.thermal)}, but no port in it declares a phase "
                f"and no machine in it confirms one - declare phase on a "
                f"port, so the heat they add has a heat capacity to act on",
            )

        self.temperatures: dict[str, float] = {
            node_id: (
                boundary_temperatures.get(node_id, STANDARD_TEMPERATURE)
                if node.is_boundary
                else STANDARD_TEMPERATURE
            )
            for node_id, node in topology.nodes.items()
        }

        self.settled = True
        self.failure: str | None = None

    def propagate(self) -> None:
        """Recompute every node and stream temperature from the flows and
        pressures the solver last committed, or hold them all and say why.
        """
        arrivals = self._arrivals()

        try:
            temperatures = self._solve(arrivals, _fed(self.topology, arrivals))
            failure = _unphysical(temperatures)
        except SolverError as error:
            failure = str(error)

        if failure is not None:
            self.settled = False
            self.failure = f"domain {self.domain!r}: {failure}"
            return

        self.settled = True
        self.failure = None
        self.temperatures = temperatures

        for branch_id, branch in self.topology.branches.items():
            if branch.flow != 0.0:
                branch.stream.temperature = self._leaving(
                    branch_id,
                    temperatures[_upstream(branch)],
                )

    def _solve(
        self,
        arrivals: Mapping[str, list[str]],
        fed: list[str],
    ) -> dict[str, float]:
        temperatures = dict(self.temperatures)
        column = {node_id: index for index, node_id in enumerate(fed)}

        for node_id in fed:
            temperatures[node_id] = STANDARD_TEMPERATURE

        for _ in range(MAX_ITERATIONS):
            matrix = [[0.0] * len(fed) for _ in fed]
            rhs = [0.0] * len(fed)

            for row, node_id in enumerate(fed):
                for branch_id in arrivals[node_id]:
                    upstream = _upstream(self.topology.branches[branch_id])
                    weight = abs(self.topology.branches[branch_id].flow)
                    slope, offset = self._linearised(
                        branch_id,
                        temperatures[upstream],
                    )

                    matrix[row][row] += weight
                    rhs[row] += weight * offset

                    if upstream in column:
                        matrix[row][column[upstream]] -= weight * slope
                    else:
                        rhs[row] += weight * slope * temperatures[upstream]

            solution = solve_linear(matrix, rhs) if fed else []
            change = max(
                (
                    abs(value - temperatures[node_id])
                    for node_id, value in zip(fed, solution)
                ),
                default=0.0,
            )

            temperatures.update(zip(fed, solution))

            if change <= SETTLE_TOLERANCE:
                return temperatures

        raise SolverError(
            f"node temperatures did not settle in {MAX_ITERATIONS} "
            f"iterations",
        )

    def _linearised(
        self,
        branch_id: str,
        arriving: float,
    ) -> tuple[float, float]:
        """The leaving temperature as slope * arriving + offset, about the
        arriving temperature given. A pass-through is (1, 0) exactly."""
        if branch_id not in self.thermal:
            return 1.0, 0.0

        leaving = self._leaving(branch_id, arriving)
        slope = self._leaving(branch_id, arriving + 1.0) - leaving

        return slope, leaving - slope * arriving

    def _arrivals(self) -> dict[str, list[str]]:
        arrivals: dict[str, list[str]] = {
            node_id: []
            for node_id in self.topology.internal_nodes
        }

        for branch_id, branch in self.topology.branches.items():
            downstream = _downstream(branch)

            if downstream in arrivals:
                arrivals[downstream].append(branch_id)

        return arrivals

    def _leaving(self, branch_id: str, arriving: float) -> float:
        device = self.thermal.get(branch_id)

        if device is None:
            return arriving

        assert self.phase is not None

        branch = self.topology.branches[branch_id]
        leaving = device.leaving_temperature(
            StreamState(branch.flow, arriving, self.phase),
            branch.from_node.pressure,
            branch.to_node.pressure,
        )

        if not math.isfinite(leaving):
            raise ValueError(
                f"{branch.device.tag} on branch {branch_id!r} returned a "
                f"leaving temperature of {leaving} at flow {branch.flow} - a "
                f"thermal device must be finite at every finite input",
            )

        return leaving


def _upstream(branch: Branch) -> str:
    return branch.to_node.id if branch.flow < 0.0 else branch.from_node.id


def _downstream(branch: Branch) -> str | None:
    if branch.flow > 0.0:
        return branch.to_node.id

    if branch.flow < 0.0:
        return branch.from_node.id

    return None


def _fed(topology: Topology, arrivals: Mapping[str, list[str]]) -> list[str]:
    """Internal nodes a flowing path from a boundary reaches, in id order."""
    feeds: dict[str, list[str]] = {}

    for node_id, branch_ids in arrivals.items():
        for branch_id in branch_ids:
            upstream = _upstream(topology.branches[branch_id])
            feeds.setdefault(upstream, []).append(node_id)

    reached: set[str] = set()
    frontier = list(topology.boundary_nodes)

    while frontier:
        for node_id in feeds.get(frontier.pop(), []):
            if node_id not in reached:
                reached.add(node_id)
                frontier.append(node_id)

    return sorted(reached)


def _unphysical(temperatures: Mapping[str, float]) -> str | None:
    cold = sorted(
        node_id
        for node_id, temperature in temperatures.items()
        if not math.isfinite(temperature) or temperature <= ABSOLUTE_ZERO
    )

    if not cold:
        return None

    return (
        f"no steady temperature at {cold} - a loop returning more heat than "
        f"its feed carries away"
    )


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
