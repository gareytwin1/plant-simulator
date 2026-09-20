"""
Topology: the graph the solver walks — interface contract C2.

A node is a pressure point. A branch carries flow between two nodes and
holds exactly one device. A stream is the material state that branch carries.

The graph exists because of C1's rule about equipment: a device publishes a
curve and never reads or writes a node pressure. Pressures and flows have to
live somewhere, so they live out here, where the solver owns them. Nodes and
branches hand those values out through read-only properties and accept writes
only through set_pressure() and set_flow() — a device holding a Port has no
route to either, and `node.pressure = 800` raises instead of quietly becoming
physics.

Branches carry C1's sign convention into the graph. Positive flow runs
from_node -> to_node, which construction guarantees is also the device's
inlet -> outlet direction, so `Branch.characteristic()` is the device curve
unchanged. `Branch.residual()` is the branch equation built from it, and the
thing the network solver (T4-2) drives to zero.

Boundary nodes are the plant's battery limits: their pressure is held fixed,
and set_pressure() refuses to move it. A topology with no boundary node has
nothing anchoring its pressure field; `boundary_nodes` is the primitive the
loader (T3-3) checks that with at load time, where the failure is readable.

set_boundary_pressure() is the one sanctioned exception, added at T5-2 so a
coupling device's integrated inventory can supply a boundary condition (ADR
0001, D11). It is the mirror image of set_pressure(): it refuses on an
*internal* node, so the solver's writer and the coupling's writer can never
reach the same node. `configured_pressure` is the as-built value it is meant
to be offset from, which is what keeps the offset from accumulating.

Units follow docs/UNITS_CONVENTION.md: pressure psia, temperature °F, flow in
the device's native unit (SCFM gas, GPM liquid), elevation ft.
"""

import math
from collections.abc import Iterable, Mapping

from app.equipment.base import INLET, OUTLET, Equipment, Port
from app.statetypes import StateRow


ATMOSPHERIC_PRESSURE = 14.696  # psia
STANDARD_TEMPERATURE = 60.0  # °F — the "standard" in SCFM

COMPOSITION_TOLERANCE = 1e-6


class Node:
    """A pressure point in the plant.

    Internal nodes carry a pressure the solver writes each timestep; the
    value they are constructed with is only a starting guess. Boundary nodes
    carry a pressure the plant does not get to move — a feed header, a
    product header, an atmospheric vent.

    `pressure` is read-only and `set_pressure()` refuses on a boundary, so
    the two rules that matter are structural rather than conventional: no
    one writes a node pressure by accident, and no one moves a battery limit
    inside the plant's own solve.

    A battery limit is moved from *outside* the solve, by the inventory that
    feeds it, and `set_boundary_pressure()` is that route. It refuses on an
    internal node, so the two writers partition the graph between them rather
    than overlapping on it.

    `configured_pressure` is the pressure the node was built with and never
    changes. The coupling reads its base off that every step instead of off
    the live pressure it wrote last step, which is what makes an accumulating
    offset impossible rather than merely avoided.
    """

    __slots__ = (
        "id",
        "is_boundary",
        "elevation",
        "_pressure",
        "_configured_pressure",
    )

    id: str
    is_boundary: bool
    elevation: float
    _pressure: float
    _configured_pressure: float

    def __init__(
        self,
        id: str,
        pressure: float = ATMOSPHERIC_PRESSURE,
        is_boundary: bool = False,
        elevation: float = 0.0,
    ) -> None:
        self.id = id
        self.is_boundary = is_boundary
        self.elevation = elevation
        self._pressure = pressure
        self._configured_pressure = pressure

    @property
    def pressure(self) -> float:
        return self._pressure

    @property
    def configured_pressure(self) -> float:
        """The pressure this node was built with — the as-built value.

        Fixed at construction and never written again, so it survives every
        solve and every boundary update. A coupling that offsets a boundary
        by an inventory head reads its base here, which is why the offset
        cannot compound over a run.
        """
        return self._configured_pressure

    def set_pressure(self, pressure: float) -> None:
        """Write a solved pressure. Boundary nodes refuse.

        The solver calls this on internal nodes once per iteration. A call
        against a boundary node is a modelling error, not a process event:
        the whole point of a battery limit is that the plant inside it cannot
        push it around.
        """
        if self.is_boundary:
            raise ValueError(
                f"node {self.id!r} is a boundary — its pressure is held fixed "
                f"at {self._pressure} psia and cannot be solved for",
            )

        self._pressure = pressure

    def set_boundary_pressure(self, pressure: float) -> None:
        """Write a boundary condition from outside the solve. Internal
        nodes refuse.

        The mirror of set_pressure(). A battery limit is not solved for, but
        it is not immutable either: under D11 a coupling device's integrated
        inventory supplies it, and this is the only route that does so. An
        internal node is refused because its pressure belongs to the solver,
        and two writers on one node is the failure this pair of methods
        exists to make unreachable.

        The value must be a finite, strictly positive absolute pressure. The
        loader already checks that at load time; this is the same rule at
        runtime, where a boundary is now written every step. It is a hard
        error rather than a clamp on purpose — a zero or negative psia here
        means the mapping that produced it is wrong, and flooring it would
        hide that behind a plant which still solves.
        """
        if not self.is_boundary:
            raise ValueError(
                f"node {self.id!r} is internal — its pressure is a solver "
                f"output and cannot be set as a boundary condition",
            )

        if not math.isfinite(pressure) or pressure <= 0.0:
            raise ValueError(
                f"node {self.id!r}: boundary pressure {pressure} is not a "
                f"finite positive absolute pressure (psia)",
            )

        self._pressure = pressure

    def get_state(self) -> StateRow:
        return {
            "pressure": self._pressure,
            "is_boundary": self.is_boundary,
            "elevation": self.elevation,
        }

    def __repr__(self) -> str:
        kind = "boundary" if self.is_boundary else "internal"

        return f"Node({self.id!r}, {self._pressure} psia, {kind})"


class Stream:
    """The material state a branch carries.

    Sane at zero flow is the design rule. A branch that is not flowing still
    has a pressure, a temperature and a composition — a blocked-in line is
    full of something at some condition, and code downstream of the solver
    (thermo, envelopes, the snapshot) must not have to special-case a dead
    branch to avoid reading a zero temperature or dividing by a zero flow.

    Composition is a mole-fraction mapping. Empty means a single unspecified
    fluid, which is all V1 needs; anything non-empty must add up, because a
    composition that sums to 0.9 is a bug that would otherwise surface much
    later as a quietly wrong enthalpy.
    """

    __slots__ = (
        "flow",
        "pressure",
        "temperature",
        "composition",
    )

    flow: float
    pressure: float
    temperature: float
    composition: dict[str, float]

    def __init__(
        self,
        flow: float = 0.0,
        pressure: float = ATMOSPHERIC_PRESSURE,
        temperature: float = STANDARD_TEMPERATURE,
        composition: Mapping[str, float] | None = None,
    ) -> None:
        self.flow = flow
        self.pressure = pressure
        self.temperature = temperature
        self.composition = _validated_composition(composition)

    def get_state(self) -> StateRow:
        return {
            "flow": self.flow,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "composition": dict(self.composition),
        }

    def __repr__(self) -> str:
        return (
            f"Stream(flow={self.flow}, {self.pressure} psia, "
            f"{self.temperature} °F)"
        )


class Branch:
    """One flow path between two nodes, holding exactly one device.

    Construction is what wires a device into the plant: an inlet port is
    connected to `from_node` and an outlet port to `to_node`. That binding is
    the only place port wiring is decided, which is why a device can be
    handed around, reset, or reused without losing its place in the graph —
    `reset()` preserves ports precisely because the topology owns them, not
    the device.

    Which ports is normally obvious and left unsaid: a device with one inlet
    and one outlet is wired by direction alone, whatever it happens to call
    them. A device with more — a vessel with a vent, an exchanger with a
    utility side — sits in more than one branch, and each branch names the
    two ports it claims. Two branches claiming the same port is refused,
    because the second would silently steal the first one's connection.

    `flow` is a solver output and lives in exactly one place, on the stream
    the branch carries. The property reads it and set_flow() writes it, so
    branch and stream can never disagree about how much is moving.

    The branch is also where C1's sign convention becomes concrete, because
    the branch is what knows which way round the device is. Positive flow
    runs from_node -> to_node, and construction guarantees that is the same
    direction as the device's inlet -> outlet, because `from_port` must be an
    INLET and `to_port` an OUTLET. A branch therefore never has to flip the
    curve to match its own orientation, and there is no second convention
    here to get out of step with the one in `Equipment.characteristic`.

    `characteristic()` reads the curve in graph terms — the rise from
    `from_node` to `to_node` — and `residual()` is the branch equation the
    network solver (T4-2) drives to zero. Both take a trial flow, because the
    solver evaluates candidate flows far from the one the branch is currently
    carrying, and default to the current flow for reading a solved plant.
    """

    __slots__ = (
        "id",
        "from_node",
        "to_node",
        "device",
        "from_port",
        "to_port",
        "stream",
    )

    id: str
    from_node: Node
    to_node: Node
    device: Equipment
    from_port: Port
    to_port: Port
    stream: Stream

    def __init__(
        self,
        id: str,
        from_node: Node,
        to_node: Node,
        device: Equipment,
        flow: float = 0.0,
        from_port: str | None = None,
        to_port: str | None = None,
    ) -> None:
        if device is None:
            raise ValueError(
                f"branch {id!r} needs a device — a branch is a flow path "
                f"through something, and a bare line is a resistance device",
            )

        if from_node is to_node:
            raise ValueError(
                f"branch {id!r} starts and ends at node {from_node.id!r}, "
                f"so it has no pressure difference to carry flow",
            )

        self.id = id
        self.from_node = from_node
        self.to_node = to_node
        self.device = device

        self.stream = Stream(
            flow=flow,
            pressure=from_node.pressure,
        )

        self.from_port = self._resolve_port(from_port, INLET)
        self.to_port = self._resolve_port(to_port, OUTLET)

        self._bind(self.from_port, from_node)
        self._bind(self.to_port, to_node)

    @property
    def flow(self) -> float:
        return self.stream.flow

    def set_flow(self, flow: float) -> None:
        """Write a solved flow. The stream is where it is stored."""
        self.stream.flow = flow

    def set_stream(self, stream: Stream) -> None:
        """Replace the material state wholesale.

        Flow rides along with it, because the stream owns flow — a caller
        that wants to keep the current flow passes it in on the new stream.
        """
        self.stream = stream

    @property
    def nodes(self) -> tuple[Node, Node]:
        return (
            self.from_node,
            self.to_node,
        )

    def characteristic(self, flow: float | None = None) -> float:
        """The device curve in graph terms: the pressure rise this branch
        applies going from_node -> to_node at `flow`.

        Positive raises pressure along the branch, negative drops it. It is
        `Equipment.characteristic` unchanged rather than re-signed, because a
        branch is always wired inlet-first and the two directions agree.

        `flow` defaults to the flow the branch is carrying, which is how a
        solved plant is read. The solver passes a trial flow instead.
        """
        return self.device.characteristic(
            self.flow if flow is None else flow,
        )

    def residual(self, flow: float | None = None) -> float:
        """How far this branch sits off its own curve, in psi.

        The branch equation is that the pressure the graph offers across the
        branch equals the pressure change the device produces at the flow
        being carried:

            from_node.pressure + characteristic(flow) - to_node.pressure = 0

        Zero means the branch is on its curve. Positive means the device is
        producing more rise than the two node pressures can absorb, so that
        flow is too low; negative means the reverse. The solver drives this
        to zero on every branch at once, and because the curve is
        non-increasing in flow the residual is too — one sign change, one
        root, and a Jacobian diagonal that does not vanish.
        """
        return (
            self.from_node.pressure
            + self.characteristic(flow)
            - self.to_node.pressure
        )

    def get_state(self) -> StateRow:
        return {
            "from_node": self.from_node.id,
            "to_node": self.to_node.id,
            "device": self.device.tag,
            "from_port": self.from_port.name,
            "to_port": self.to_port.name,
            "flow": self.stream.flow,
        }

    def _resolve_port(self, name: str | None, direction: str) -> Port:
        if name is None:
            return self._sole_port(direction)

        port = self.device.port(name)

        if port.direction != direction:
            raise ValueError(
                f"branch {self.id!r} wires {self.device.tag}.{name} as its "
                f"{direction} end, but that port is an {port.direction}",
            )

        return port

    def _sole_port(self, direction: str) -> Port:
        matching = [
            port
            for port in self.device.ports.values()
            if port.direction == direction
        ]

        if len(matching) != 1:
            raise ValueError(
                f"branch {self.id!r} cannot pick an {direction} port on "
                f"{self.device.tag} by direction alone, found "
                f"{len(matching)}: {sorted(port.name for port in matching)} — "
                f"name from_port/to_port to choose",
            )

        return matching[0]

    def _bind(self, port: Port, node: Node) -> None:
        connected_to = port.node

        if connected_to is not None and connected_to is not node:
            raise ValueError(
                f"branch {self.id!r} claims {self.device.tag}.{port.name}, "
                f"which is already connected to node {connected_to.id!r}",
            )

        port.connect(node)

    def __repr__(self) -> str:
        return (
            f"Branch({self.id!r}, {self.from_node.id!r} -> "
            f"{self.to_node.id!r}, {self.device.tag}, flow={self.stream.flow})"
        )


class Topology:
    """The connected graph: nodes, the branches between them, and the
    devices those branches hold.

    The container's job is to keep the graph addressable and self-consistent.
    It rejects the three ways a lookup table silently loses information — a
    repeated node id, a repeated branch id, a repeated device tag — and it
    refuses a branch whose nodes it does not hold, because a branch pointing
    at a node from some other graph is not a graph at all.

    Whether a graph is *solvable* is a different question, and a louder one:
    dangling ports, disconnected subgraphs and boundary pressures of zero all
    belong to the loader (T3-3), which can name the offending config path.
    `boundary_nodes` and `unconnected_ports()` are the primitives it checks
    with.
    """

    def __init__(
        self,
        nodes: Iterable[Node] = (),
        branches: Iterable[Branch] = (),
    ) -> None:
        self.nodes: dict[str, Node] = {}
        self.branches: dict[str, Branch] = {}

        for node in nodes:
            self.add_node(node)

        for branch in branches:
            self.add_branch(branch)

    def add_node(self, node: Node) -> Node:
        if node.id in self.nodes:
            raise ValueError(
                f"duplicate node id {node.id!r}",
            )

        self.nodes[node.id] = node

        return node

    def add_branch(self, branch: Branch) -> Branch:
        if branch.id in self.branches:
            raise ValueError(
                f"duplicate branch id {branch.id!r}",
            )

        for node in branch.nodes:
            if self.nodes.get(node.id) is not node:
                raise ValueError(
                    f"branch {branch.id!r} connects node {node.id!r}, which "
                    f"is not a node of this topology",
                )

        existing = self.devices.get(branch.device.tag)

        if existing is not None and existing is not branch.device:
            raise ValueError(
                f"duplicate device tag {branch.device.tag!r}, already on "
                f"branch {self._branch_of(branch.device.tag)!r}",
            )

        self.branches[branch.id] = branch

        return branch

    def node(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise KeyError(
                f"no node {node_id!r} in this topology, only "
                f"{sorted(self.nodes)}",
            )

        return self.nodes[node_id]

    def branch(self, branch_id: str) -> Branch:
        if branch_id not in self.branches:
            raise KeyError(
                f"no branch {branch_id!r} in this topology, only "
                f"{sorted(self.branches)}",
            )

        return self.branches[branch_id]

    def device(self, tag: str) -> Equipment:
        devices = self.devices

        if tag not in devices:
            raise KeyError(
                f"no device {tag!r} in this topology, only "
                f"{sorted(devices)}",
            )

        return devices[tag]

    @property
    def devices(self) -> dict[str, Equipment]:
        return {
            branch.device.tag: branch.device
            for branch in self.branches.values()
        }

    @property
    def boundary_nodes(self) -> dict[str, Node]:
        return {
            node_id: node
            for node_id, node in self.nodes.items()
            if node.is_boundary
        }

    @property
    def internal_nodes(self) -> dict[str, Node]:
        return {
            node_id: node
            for node_id, node in self.nodes.items()
            if not node.is_boundary
        }

    def branches_from(self, node_id: str) -> tuple[Branch, ...]:
        node = self.node(node_id)

        return tuple(
            branch
            for branch in self.branches.values()
            if branch.from_node is node
        )

    def branches_to(self, node_id: str) -> tuple[Branch, ...]:
        node = self.node(node_id)

        return tuple(
            branch
            for branch in self.branches.values()
            if branch.to_node is node
        )

    def branches_at(self, node_id: str) -> tuple[Branch, ...]:
        node = self.node(node_id)

        return tuple(
            branch
            for branch in self.branches.values()
            if node in branch.nodes
        )

    def unconnected_ports(self) -> tuple[tuple[str, Port], ...]:
        """Every port no branch wired to a node, as (tag, port) pairs.

        A device with more ports than its branch uses — a vessel with a vent,
        an exchanger's utility side — leaves them here. Empty is what a
        solvable topology looks like; T3-3 turns anything else into a load
        error naming the device.
        """
        return tuple(
            (device.tag, port)
            for tag, device in sorted(self.devices.items())
            for port in device.ports.values()
            if not port.connected
        )

    def get_state(self) -> dict[str, dict[str, StateRow]]:
        """Nodes, branches and streams as flat JSON-safe rows.

        C4 gives streams ids of their own ("S-01"). Until a stream is more
        than the material a branch carries, the branch is its id — the
        snapshot owner can remap that without the graph changing shape.
        """
        return {
            "nodes": {
                node_id: node.get_state()
                for node_id, node in self.nodes.items()
            },
            "branches": {
                branch_id: branch.get_state()
                for branch_id, branch in self.branches.items()
            },
            "streams": {
                branch_id: branch.stream.get_state()
                for branch_id, branch in self.branches.items()
            },
        }

    def describe(self) -> str:
        """The graph as printable text, for reading a plant at a glance."""
        lines = [
            f"Topology: {len(self.nodes)} nodes "
            f"({len(self.boundary_nodes)} boundary), "
            f"{len(self.branches)} branches",
        ]

        for node_id, node in self.nodes.items():
            kind = "boundary" if node.is_boundary else "internal"

            lines.append(
                f"  node   {node_id:<8} {node.pressure:>9.2f} psia  {kind}",
            )

        for branch_id, branch in self.branches.items():
            lines.append(
                f"  branch {branch_id:<8} {branch.from_node.id} -> "
                f"{branch.to_node.id}  {branch.device.tag}  "
                f"flow {branch.flow:.2f}",
            )

        for tag, port in self.unconnected_ports():
            lines.append(
                f"  open   {tag}.{port.name} ({port.direction})",
            )

        return "\n".join(lines)

    def _branch_of(self, tag: str) -> str | None:
        for branch_id, branch in self.branches.items():
            if branch.device.tag == tag:
                return branch_id

        return None

    def __str__(self) -> str:
        return self.describe()

    def __repr__(self) -> str:
        return (
            f"<Topology {len(self.nodes)} nodes, "
            f"{len(self.branches)} branches>"
        )


def _validated_composition(
    composition: Mapping[str, float] | None,
) -> dict[str, float]:
    fractions = dict(composition or {})

    if not fractions:
        return fractions

    for component, fraction in fractions.items():
        if fraction < 0.0:
            raise ValueError(
                f"composition fraction for {component!r} is negative: "
                f"{fraction}",
            )

    total = sum(fractions.values())

    if abs(total - 1.0) > COMPOSITION_TOLERANCE:
        raise ValueError(
            f"composition fractions must sum to 1.0, got {total} for "
            f"{sorted(fractions)}",
        )

    return fractions
