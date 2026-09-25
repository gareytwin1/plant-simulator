"""
Coupling: the one place slow state pushes back on the hydraulics — T5-2, T5-3,
T5-6.

`NetworkSolver` solves one flow domain against fixed boundary conditions and
knows nothing about any other domain. What joins two domains is a coupling
device's inventory, never a shared flow variable (ADR 0001, D6/D7/D11), and
this module is the whole of that join. It sits between the Engine and the
graph so that neither the device nor the solver has to reach across:

  - the vessel publishes a level and the head that level offers, and reads
    nothing;
  - the topology publishes node pressures and branch flows, and knows of no
    device outside its own branches;
  - a Coupling holds the ports of one against the nodes of the other, and
    moves two numbers per step between them.

**Down, into the plant.** The head is *added to* the attachment node's
`configured_pressure`, never written as the boundary outright. A boundary is
an absolute pressure and C2 requires it positive, so an empty vessel must
still leave a positive battery limit. Reading the base off the as-built value
every step, rather than off the pressure written last step, is also what
stops the offset compounding: the same level always produces the same
boundary, however many steps have run.

**Up, out of the plant.** A coupling device has no Branch, so there is no
"vessel branch" to read a flow off. The exchange is the signed flow the
branches carry at the attachment node. Boundary nodes carry no mass-balance
equation in the solver, so that is exactly what crossed the battery limit and
needs no correction.

**The node is the accounting entity, not the port** (ADR 0002, Amendment 2).
A port has no hydraulic flow of its own; the exchange belongs to the node it
attaches to, and several nozzles of one vessel may share a node. Each node is
therefore counted **exactly once**, however many ports the vessel declares on
it, and the number of nozzles never multiplies a flow. Distinct nodes are
independent and do sum: two vapor withdrawals on two nodes are two
withdrawals, which is the aggregation T5-6 exists to add.

What a node contributes depends on which directions the device declares there:

  - **both** an inlet and an outlet port — the gross components survive,
    `arrivals` into the inlet aggregate and `departures` into the outlet one.
    A vessel fed and drained through one node has zero net exchange at steady
    state and non-zero throughput, and the throughput is what residence time
    is made of;
  - **one** direction only — the node's *net* signed exchange is written
    through that declaration, `arrivals - departures` for an inlet and the
    negative of it for an outlet. That is the shared-node idiom ADR 0002 §2.2
    proved, and dropping the unmatched component there would delete a real
    feed from the balance.

Both components are **signed sums over branch orientation**, never magnitudes.
A branch running backwards contributes a negative arrival, which is what keeps
cold-start backflow (ADR 0002 §7.3) exactly as the solver found it.

**Phase and units.** A vessel holds two phases and keeps their flows apart:
GPM in `inlet_flow` / `outlet_flow`, SCFM in `gas_inlet_flow` /
`gas_outlet_flow`. SCFM summed into a GPM attribute would be silently wrong
rather than loudly wrong, so every attachment carries a confirmed unit and
that unit — never the port's name — picks the attributes.

The unit comes from the port's **declared `phase`** (T3-7): `liquid` is GPM,
`vapor` is SCFM. Machines around the node *confirm* it — a pump's
characteristic is written in GPM and a compressor's in SCFM — and a declared
phase that contradicts the machines beside it is refused. A known
**unit-neutral** device such as the control valve is written in whatever unit
the line carries, so it neither confirms a unit nor contradicts one; a device
this module has never heard of is a different thing entirely and is still
refused, because guessing is the failure mode this check exists to prevent.

**Domain names carry no engineering meaning.** `DOMAIN_UNITS` is retired
(ADR 0002 D5, Amendment 2): a domain may be called `gas`, `vent`, `flare`,
`relief_header` or anything else without its name selecting a unit. A legacy
untyped port is still classified from the machines around it where they
confirm one, and is refused — naming the port and asking for a phase — where
they do not. A **typed** attachment needs no branch at all: a vent header with
nothing on it yet is classified by its declaration, and is coupled.

**`purpose` and `control` are invisible here.** Only `phase` and `direction`
decide a balance (ADR 0002, Amendment 1 A.3). A vapor withdrawal labelled
`process`, `vent` or `relief`, under pressure control or under none,
participates in the vapor balance on identical terms.

**Gas (T5-3).** The vessel's pressure is integrated slow state and is written
as the runtime boundary at *every* gas attachment node, inlet and outlet
alike: the gas space is one well-mixed pressure, and an inlet-side machine has
to see it rise as the vessel fills or a blocked outlet would be an unbounded
ramp instead of a back-pressure. That is a replacement, where the liquid head
is an offset from the as-built pressure, and it never touches
`configured_pressure`. The liquid head is never applied to a gas boundary, and
lands only on a node the vessel *supplies* — one carrying an outlet
declaration. Both writes happen once per node, so duplicate nozzles cannot
apply a head twice. A confirmed SCFM attachment is also what activates the
vessel's gas phase.

**A failed domain holds a whole aggregate.** A domain that did not converge
still holds last step's flows (T4-3 leaves a failed solve untouched), and an
aggregate fed partly by such a domain would be a sum of numbers from two
different steps. Each of the four attributes is therefore written atomically:
if any node contributing to it belongs to a failed domain, that attribute
keeps its previous value entirely, while an aggregate whose own contributors
all converged still updates.

**Where the attachment must be.** A coupled node has to be a boundary node of
its domain. That is what ADR 0001 A7 says every coupling attachment will be,
and it is deferred to this task to enforce; the net-flow argument above also
depends on it. The check is here rather than in the loader so that an
inventory device attached mid-line stays loadable for whatever later task
wants one. One node is also owned by at most one inventory device: two
vessels on a node would each read the whole exchange as their own, and there
is no split this module could guess.
"""

from collections.abc import Iterable, Mapping

from app.equipment.base import INLET, LIQUID, OUTLET, VAPOR, Equipment, Port
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.plant.topology import Node, Topology


GPM = "GPM"
SCFM = "SCFM"

# A known device written in no flow unit of its own. A resistance carries
# whatever the line carries, so it is valid in either service and confirms
# nothing. It is emphatically not the same as an unrecognised device, which
# is refused: "has no intrinsic unit" must never decay into "anything
# unknown is acceptable".
UNIT_NEUTRAL = "unit-neutral"

# The flow unit each device model's `characteristic(flow)` is written in.
# Deliberately a lookup rather than a default: a device absent from here is
# refused at an attachment instead of being assumed liquid.
FLOW_UNITS: dict[type[Equipment], str] = {
    CentrifugalPump: GPM,
    GasCompressor: SCFM,
    ControlValve: UNIT_NEUTRAL,
}

# Declared phase is the primary classification (ADR 0002, Amendment 1 A.1).
# Machines confirm it; domain names say nothing about it.
PHASE_UNITS: dict[str, str] = {
    LIQUID: GPM,
    VAPOR: SCFM,
}

# The vessel attribute each (unit, direction) pair aggregates into. The four
# sums of ADR 0002 A.4, keyed by the only two descriptors allowed to decide
# a balance.
AGGREGATES: dict[tuple[str, str], str] = {
    (GPM, INLET): "inlet_flow",
    (GPM, OUTLET): "outlet_flow",
    (SCFM, INLET): "gas_inlet_flow",
    (SCFM, OUTLET): "gas_outlet_flow",
}

BOTH_DIRECTIONS = frozenset({INLET, OUTLET})


class Attachment:
    """One port of a coupling device bound to the node and domain it sits on.

    Built only where the connection classifies, so an Attachment existing is
    itself the statement that this port may be read and written. It carries
    no flow: a flow belongs to the node, and `NodeExchange` is where the
    accounting lives.
    """

    __slots__ = (
        "port",
        "domain",
        "unit",
        "topology",
        "node",
    )

    port: Port
    domain: str
    unit: str
    topology: Topology
    node: Node

    def __init__(
        self,
        port: Port,
        domain: str,
        unit: str,
        topology: Topology,
        node: Node,
    ) -> None:
        self.port = port
        self.domain = domain
        self.unit = unit
        self.topology = topology
        self.node = node

    def __repr__(self) -> str:
        return (
            f"Attachment({self.port.name!r} {self.port.direction} -> "
            f"{self.node.id!r} in {self.domain!r})"
        )


class NodeExchange:
    """One node a coupling device attaches to, with every port it attaches
    through.

    The unit of account. A node has one exchange with the plant however many
    nozzles are declared on it, and this is what the aggregates are summed
    over — so duplicate ports of the same phase and direction cost nothing
    and distinct nodes stay independent.
    """

    __slots__ = (
        "node",
        "topology",
        "domain",
        "unit",
        "attachments",
        "directions",
    )

    node: Node
    topology: Topology
    domain: str
    unit: str
    attachments: tuple[Attachment, ...]
    directions: frozenset[str]

    def __init__(
        self,
        node: Node,
        topology: Topology,
        domain: str,
        unit: str,
        attachments: Iterable[Attachment],
    ) -> None:
        self.node = node
        self.topology = topology
        self.domain = domain
        self.unit = unit
        self.attachments = tuple(attachments)
        self.directions = frozenset(
            attachment.port.direction for attachment in self.attachments
        )

    @property
    def arrivals(self) -> float:
        """Signed, by branch orientation — not a magnitude. A feed running
        backwards arrives negatively, and that is the physics, not an error
        to clamp away.
        """
        return sum(
            branch.flow for branch in self.topology.branches_to(self.node.id)
        )

    @property
    def departures(self) -> float:
        return sum(
            branch.flow for branch in self.topology.branches_from(self.node.id)
        )

    @property
    def targets(self) -> tuple[str, ...]:
        """The vessel attributes this node writes into, flows aside.

        Read on its own so a failed domain can hold the right aggregates
        without reading a single branch of the solve it disowned.
        """
        if self.directions == BOTH_DIRECTIONS:
            return (
                AGGREGATES[self.unit, INLET],
                AGGREGATES[self.unit, OUTLET],
            )

        direction = INLET if INLET in self.directions else OUTLET

        return (AGGREGATES[self.unit, direction],)

    def contributions(self) -> tuple[tuple[str, float], ...]:
        """What this node adds to each aggregate, counted once for the node.

        Two components where the device declares both directions here, so a
        vessel fed and drained through one node keeps its gross throughput
        at a steady state whose net is zero. One, the net exchange, where it
        declares a single direction — the unmatched component is folded in
        rather than dropped, because the branch carrying it is real.
        """
        arrivals = self.arrivals
        departures = self.departures

        if self.directions == BOTH_DIRECTIONS:
            inlet, outlet = self.targets

            return (
                (inlet, arrivals),
                (outlet, departures),
            )

        target = self.targets[0]

        if INLET in self.directions:
            return ((target, arrivals - departures),)

        return ((target, departures - arrivals),)

    def __repr__(self) -> str:
        ports = ", ".join(
            f"{attachment.port.name!r} {attachment.port.direction}"
            for attachment in self.attachments
        )

        return f"NodeExchange({self.node.id!r} in {self.domain!r}, {self.unit}: {ports})"


class VesselCoupling:
    """One vessel and the confirmed attachments it couples through.

    A vessel with no confirmed attachment still gets a Coupling, holding
    none. It integrates on whatever flows a caller writes, exactly as it did
    before this module existed.
    """

    def __init__(
        self,
        vessel: Vessel,
        attachments: Iterable[Attachment] = (),
    ) -> None:
        self.vessel = vessel
        self.attachments: tuple[Attachment, ...] = tuple(attachments)
        self.exchanges: tuple[NodeExchange, ...] = _exchanges(
            vessel,
            self.attachments,
        )

    def write_boundary_pressures(self) -> None:
        """Push the inventory down into the plant, once per node.

        A liquid head lands on a node the vessel *supplies* — one carrying
        an outlet declaration. A node the plant only delivers to keeps the
        battery limit the config gave it. Gas pressure is the vessel's own
        and lands on every gas node. Both are written per node rather than
        per port, so a second nozzle on a node cannot add a head twice or
        write a pressure that contradicts the first.
        """
        for exchange in self.exchanges:
            if exchange.unit == SCFM:
                self.vessel.activate_gas()
                exchange.node.set_boundary_pressure(self.vessel.pressure)

                continue

            if OUTLET not in exchange.directions:
                continue

            exchange.node.set_boundary_pressure(
                exchange.node.configured_pressure + self.vessel.head,
            )

    def write_flows(self, converged: Iterable[str]) -> None:
        """Pull the solved exchange up out of the plant, aggregate by
        aggregate.

        Candidates are gathered before anything is written, so an attribute
        is either the sum of every node feeding it or last step's value. A
        domain that did not converge holds the whole aggregate it touches:
        its branches still carry last step's flows, and a sum mixing two
        steps is a number no solver ever produced.
        """
        solved = set(converged)

        totals: dict[str, list[float]] = {}
        held: set[str] = set()

        for exchange in self.exchanges:
            if exchange.domain not in solved:
                held.update(exchange.targets)

                continue

            for attribute, value in exchange.contributions():
                totals.setdefault(attribute, []).append(value)

        for attribute, values in totals.items():
            if attribute in held:
                continue

            setattr(self.vessel, attribute, _total(values))

    def __repr__(self) -> str:
        return f"VesselCoupling({self.vessel.tag!r}, {list(self.attachments)})"


def build_couplings(
    devices: Iterable[Equipment],
    topologies: Mapping[str, Topology],
) -> list[VesselCoupling]:
    """Bind every coupling device in the plant to the graph around it.

    Raises where an attachment cannot be coupled safely — a phase that no
    declaration or machine establishes, a declaration the machines
    contradict, two phases on one node, an attachment on an internal node,
    or one node claimed by two inventory devices. All of them are modelling
    errors, and the Engine is built once per plant, so this is the cheapest
    place to find them.
    """
    couplings = [
        VesselCoupling(
            device,
            [
                attachment
                for port in device.ports.values()
                if (attachment := _attachment(device, port, topologies)) is not None
            ],
        )
        for device in devices
        if isinstance(device, Vessel)
    ]

    _reject_shared_nodes(couplings)

    return couplings


def _exchanges(
    vessel: Vessel,
    attachments: Iterable[Attachment],
) -> tuple[NodeExchange, ...]:
    """Group a device's attachments by the node they share, in port order."""
    grouped: dict[str, list[Attachment]] = {}

    for attachment in attachments:
        grouped.setdefault(attachment.node.id, []).append(attachment)

    exchanges = []

    for node_id, group in grouped.items():
        first = group[0]

        for attachment in group[1:]:
            if attachment.unit == first.unit:
                continue

            raise ValueError(
                f"{vessel.tag} attaches node {node_id!r} through "
                f"{_described(first)} and {_described(attachment)} — one node "
                f"carries one phase, and it cannot be counted once as liquid "
                f"and again as vapor (ADR 0002, Amendment 2)",
            )

        exchanges.append(
            NodeExchange(
                first.node,
                first.topology,
                first.domain,
                first.unit,
                group,
            ),
        )

    return tuple(exchanges)


def _reject_shared_nodes(couplings: Iterable[VesselCoupling]) -> None:
    """One node, one inventory owner.

    Two vessels on a node would each read the whole exchange as their own,
    and the same transfer would land in two inventories. Dividing it is a
    guess, so this refuses instead.
    """
    claimed: dict[str, tuple[VesselCoupling, Attachment]] = {}

    for coupling in couplings:
        for attachment in coupling.attachments:
            owner = claimed.get(attachment.node.id)

            if owner is None:
                claimed[attachment.node.id] = (coupling, attachment)

                continue

            if owner[0] is coupling:
                continue

            raise ValueError(
                f"node {attachment.node.id!r} is claimed by two inventory "
                f"devices — {owner[0].vessel.tag}.{owner[1].port.name} and "
                f"{coupling.vessel.tag}.{attachment.port.name}. Each would "
                f"read the whole node exchange as its own, and there is no "
                f"split to guess (ADR 0002, Amendment 2)",
            )


def _attachment(
    device: Vessel,
    port: Port,
    topologies: Mapping[str, Topology],
) -> Attachment | None:
    if port.node is None:
        return None

    located = _locate(port.node.id, topologies)

    if located is None:
        return None

    domain, topology = located
    node = topology.node(port.node.id)

    unit = _unit_at(device, port, topology, node)

    if not node.is_boundary:
        raise ValueError(
            f"{device.tag}.{port.name} couples to node {node.id!r}, which is "
            f"internal — a coupling device's attachment must be a boundary "
            f"node of the domain it terminates (ADR 0001, A7)",
        )

    return Attachment(port, domain, unit, topology, node)


def _locate(
    node_id: str,
    topologies: Mapping[str, Topology],
) -> tuple[str, Topology] | None:
    # Node ids are unique plant-wide, so the first domain holding one is the
    # only domain holding it.
    for domain, topology in topologies.items():
        if node_id in topology.nodes:
            return domain, topology

    return None


def _unit_at(
    device: Vessel,
    port: Port,
    topology: Topology,
    node: Node,
) -> str:
    """The flow unit this attachment carries.

    Declared phase decides it and the machines at the node confirm it. A
    typed port therefore couples with no branch to read at all — a vent
    header with nothing on it yet is still a vapor connection — while an
    untyped one has nothing to fall back on and is refused rather than
    assumed liquid.
    """
    confirmed = _confirmed_unit(device, port, topology, node)

    if port.phase is not None:
        declared = PHASE_UNITS[port.phase]

        if confirmed is not None and confirmed != declared:
            raise ValueError(
                f"{device.tag}.{port.name} declares phase {port.phase!r} "
                f"({declared}) at node {node.id!r}, but the equipment there "
                f"is written in {confirmed} — the declaration and the plant "
                f"disagree about which phase this connection carries",
            )

        return declared

    if confirmed is None:
        raise ValueError(
            f"{device.tag}.{port.name} attaches to node {node.id!r} and "
            f"declares no phase, and nothing at that node establishes one — "
            f"declare phase: {LIQUID!r} or phase: {VAPOR!r} on the port "
            f"(ADR 0002, Amendment 1 A.6). Domain names carry no flow unit",
        )

    return confirmed


def _confirmed_unit(
    device: Vessel,
    port: Port,
    topology: Topology,
    node: Node,
) -> str | None:
    """The unit the equipment at `node` is written in, or None where none of
    it declares one.

    A unit-neutral device abstains rather than answering, so a node carrying
    only valves confirms nothing and leaves the declaration to say what this
    is. An unrecognised device is a different case and raises.
    """
    units = set()

    for branch in topology.branches_at(node.id):
        unit = flow_unit(branch.device)

        if unit is None:
            raise ValueError(
                f"{device.tag}.{port.name} attaches to node {node.id!r}, which "
                f"carries {branch.device.tag} "
                f"({type(branch.device).__name__}) — its flow unit is not "
                f"declared in app/engine/coupling.py, and guessing one would "
                f"mix {GPM} with {SCFM}",
            )

        if unit == UNIT_NEUTRAL:
            continue

        units.add(unit)

    if len(units) > 1:
        raise ValueError(
            f"{device.tag}.{port.name} attaches to node {node.id!r}, where "
            f"branches disagree on flow unit ({sorted(units)}) — one node "
            f"carries one unit",
        )

    return units.pop() if units else None


def flow_unit(device: Equipment) -> str | None:
    for kind in type(device).__mro__:
        if kind in FLOW_UNITS:
            return FLOW_UNITS[kind]

    return None


def _described(attachment: Attachment) -> str:
    port = attachment.port

    if port.phase is not None:
        return f"{port.name!r} (phase {port.phase!r}, {attachment.unit})"

    return f"{port.name!r} (undeclared, confirmed {attachment.unit})"


def _total(values: list[float]) -> float:
    """Sum without a zero seed.

    `sum()` starts at 0, which turns a lone -0.0 into +0.0 and adds a
    rounding step no single-attachment plant had before aggregation existed.
    One contribution is written exactly as the node produced it.
    """
    total = values[0]

    for value in values[1:]:
        total += value

    return total
