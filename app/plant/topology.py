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

Boundary nodes are the plant's battery limits: their pressure is held fixed,
and set_pressure() refuses to move it. A topology with no boundary node has
nothing anchoring its pressure field; `boundary_nodes` is the primitive the
loader (T3-3) checks that with at load time, where the failure is readable.

Units follow docs/UNITS_CONVENTION.md: pressure psia, temperature °F, flow in
the device's native unit (SCFM gas, GPM liquid), elevation ft.
"""

from app.equipment.base import INLET, OUTLET


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
    at all.
    """

    __slots__ = (
        "id",
        "is_boundary",
        "elevation",
        "_pressure",
    )

    def __init__(
        self,
        id,
        pressure=ATMOSPHERIC_PRESSURE,
        is_boundary=False,
        elevation=0.0,
    ):
        self.id = id
        self.is_boundary = is_boundary
        self.elevation = elevation
        self._pressure = pressure

    @property
    def pressure(self):
        return self._pressure

    def set_pressure(self, pressure):
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

    def get_state(self):
        return {
            "pressure": self._pressure,
            "is_boundary": self.is_boundary,
            "elevation": self.elevation,
        }

    def __repr__(self):
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

    def __init__(
        self,
        flow=0.0,
        pressure=ATMOSPHERIC_PRESSURE,
        temperature=STANDARD_TEMPERATURE,
        composition=None,
    ):
        self.flow = flow
        self.pressure = pressure
        self.temperature = temperature
        self.composition = _validated_composition(composition)

    def get_state(self):
        return {
            "flow": self.flow,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "composition": dict(self.composition),
        }

    def __repr__(self):
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

    Sign convention for the device curve is deliberately not defined here —
    T4-1 owns it, and guessing at it now would freeze the wrong thing.
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

    def __init__(
        self,
        id,
        from_node,
        to_node,
        device,
        flow=0.0,
        from_port=None,
        to_port=None,
    ):
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
    def flow(self):
        return self.stream.flow

    def set_flow(self, flow):
        """Write a solved flow. The stream is where it is stored."""
        self.stream.flow = flow

    def set_stream(self, stream):
        """Replace the material state wholesale.

        Flow rides along with it, because the stream owns flow — a caller
        that wants to keep the current flow passes it in on the new stream.
        """
        self.stream = stream

    @property
    def nodes(self):
        return (
            self.from_node,
            self.to_node,
        )

    def get_state(self):
        return {
            "from_node": self.from_node.id,
            "to_node": self.to_node.id,
            "device": self.device.tag,
            "from_port": self.from_port.name,
            "to_port": self.to_port.name,
            "flow": self.stream.flow,
        }

    def _resolve_port(self, name, direction):
        if name is None:
            return self._sole_port(direction)

        port = self.device.port(name)

        if port.direction != direction:
            raise ValueError(
                f"branch {self.id!r} wires {self.device.tag}.{name} as its "
                f"{direction} end, but that port is an {port.direction}",
            )

        return port

    def _sole_port(self, direction):
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

    def _bind(self, port, node):
        if port.connected and port.node is not node:
            raise ValueError(
                f"branch {self.id!r} claims {self.device.tag}.{port.name}, "
                f"which is already connected to node {port.node.id!r}",
            )

        port.connect(node)

    def __repr__(self):
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

    def __init__(self, nodes=(), branches=()):
        self.nodes = {}
        self.branches = {}

        for node in nodes:
            self.add_node(node)

        for branch in branches:
            self.add_branch(branch)

    def add_node(self, node):
        if node.id in self.nodes:
            raise ValueError(
                f"duplicate node id {node.id!r}",
            )

        self.nodes[node.id] = node

        return node

    def add_branch(self, branch):
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

    def node(self, node_id):
        if node_id not in self.nodes:
            raise KeyError(
                f"no node {node_id!r} in this topology, only "
                f"{sorted(self.nodes)}",
            )

        return self.nodes[node_id]

    def branch(self, branch_id):
        if branch_id not in self.branches:
            raise KeyError(
                f"no branch {branch_id!r} in this topology, only "
                f"{sorted(self.branches)}",
            )

        return self.branches[branch_id]

    def device(self, tag):
        devices = self.devices

        if tag not in devices:
            raise KeyError(
                f"no device {tag!r} in this topology, only "
                f"{sorted(devices)}",
            )

        return devices[tag]

    @property
    def devices(self):
        return {
            branch.device.tag: branch.device
            for branch in self.branches.values()
        }

    @property
    def boundary_nodes(self):
        return {
            node_id: node
            for node_id, node in self.nodes.items()
            if node.is_boundary
        }

    @property
    def internal_nodes(self):
        return {
            node_id: node
            for node_id, node in self.nodes.items()
            if not node.is_boundary
        }

    def branches_from(self, node_id):
        node = self.node(node_id)

        return tuple(
            branch
            for branch in self.branches.values()
            if branch.from_node is node
        )

    def branches_to(self, node_id):
        node = self.node(node_id)

        return tuple(
            branch
            for branch in self.branches.values()
            if branch.to_node is node
        )

    def branches_at(self, node_id):
        node = self.node(node_id)

        return tuple(
            branch
            for branch in self.branches.values()
            if node in branch.nodes
        )

    def unconnected_ports(self):
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

    def get_state(self):
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

    def describe(self):
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

    def _branch_of(self, tag):
        for branch_id, branch in self.branches.items():
            if branch.device.tag == tag:
                return branch_id

        return None

    def __str__(self):
        return self.describe()

    def __repr__(self):
        return (
            f"<Topology {len(self.nodes)} nodes, "
            f"{len(self.branches)} branches>"
        )


def _validated_composition(composition):
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
