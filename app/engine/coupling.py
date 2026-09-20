"""
Coupling: the one place slow state pushes back on the hydraulics — T5-2.

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
"vessel branch" to read a flow off. The exchange is the net signed flow at
the attachment node: everything the branches bring in, less everything they
take away. Boundary nodes carry no mass-balance equation in the solver, so
that sum is exactly what crossed the battery limit and needs no correction.
More than one branch may meet the node, and the port's `direction` — never
its name — decides the sign.

**Units.** A vessel's flows are GPM. A vessel port may legitimately attach to
a gas node, and SCFM summed into a GPM attribute would be silently wrong
rather than loudly wrong, so a port is coupled only where the flow unit can
be *confirmed* GPM from the devices on the branches that meet it. A machine
declares its unit; a resistance such as the control valve has none of its own
and takes it from the domain it sits in (`liquid` GPM, `gas` SCFM), refusing
any other domain name. A gas attachment is left alone — the gas side is
T5-3's. A node whose devices this module cannot classify, or whose devices
disagree, raises when the Engine is built, because guessing is the failure mode this check exists to prevent.

**Where the attachment must be.** A coupled node has to be a boundary node of
its domain. That is what ADR 0001 A7 says every coupling attachment will be,
and it is deferred to this task to enforce; the net-flow argument above also
depends on it. The check is here rather than in the loader so that an
inventory device attached mid-line stays loadable for whatever later task
wants one.
"""

from collections.abc import Iterable, Mapping

from app.equipment.base import INLET, Equipment, Port
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.plant.topology import Node, Topology


GPM = "GPM"
SCFM = "SCFM"

# The flow unit each device model's `characteristic(flow)` is written in.
# Deliberately a lookup rather than a default: a device absent from here is
# refused at an attachment instead of being assumed liquid.
FLOW_UNITS: dict[type[Equipment], str] = {
    CentrifugalPump: GPM,
    GasCompressor: SCFM,
}

# A device with no unit of its own: a resistance is written in whatever flow
# unit the line it sits in carries, so that is read off the domain it is
# installed in. Domain names are semantically load-bearing for exactly these
# devices — an undeclared name, and DEFAULT_DOMAIN, are refused rather than
# guessed at.
DOMAIN_RESOLVED: frozenset[type[Equipment]] = frozenset({ControlValve})

DOMAIN_UNITS: dict[str, str] = {
    "liquid": GPM,
    "gas": SCFM,
}

# What a Vessel's inlet_flow and outlet_flow are measured in.
INVENTORY_UNIT = GPM


class Attachment:
    """One port of a coupling device bound to the node and domain it sits on.

    Built only where the flow unit was confirmed, so an Attachment existing
    is itself the statement that this port may be read and written.
    """

    __slots__ = (
        "port",
        "domain",
        "topology",
        "node",
    )

    port: Port
    domain: str
    topology: Topology
    node: Node

    def __init__(
        self,
        port: Port,
        domain: str,
        topology: Topology,
        node: Node,
    ) -> None:
        self.port = port
        self.domain = domain
        self.topology = topology
        self.node = node

    @property
    def net_flow(self) -> float:
        """What the device exchanges with the plant at this node, signed so
        that positive is always *into* the device.

        An inlet takes whatever the branches leave at the node; an outlet
        supplies whatever they carry away. Direction decides it, because a
        port named "inlet" that was declared an OUTLET is a naming accident
        and this sign is not.
        """
        arrivals = sum(
            branch.flow for branch in self.topology.branches_to(self.node.id)
        )
        departures = sum(
            branch.flow for branch in self.topology.branches_from(self.node.id)
        )

        if self.port.direction == INLET:
            return arrivals - departures

        return departures - arrivals

    def __repr__(self) -> str:
        return (
            f"Attachment({self.port.name!r} {self.port.direction} -> "
            f"{self.node.id!r} in {self.domain!r})"
        )


class VesselCoupling:
    """One vessel and the confirmed-liquid attachments it couples through.

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

    def write_boundary_pressures(self) -> None:
        """Push the inventory head down into the plant.

        The head lands on the outlet attachment only — the node the vessel
        *supplies*. An inlet attachment is a node the plant delivers to, and
        its pressure stays the battery limit the config gave it.
        """
        for attachment in self.attachments:
            if attachment.port.direction == INLET:
                continue

            node = attachment.node

            node.set_boundary_pressure(
                node.configured_pressure + self.vessel.head,
            )

    def write_flows(self, converged: Iterable[str]) -> None:
        """Pull the solved exchange up out of the plant.

        A domain that did not converge is skipped: its branches still hold
        last step's flows (T4-3 leaves a failed solve untouched), and
        integrating a vessel against numbers the solver disowned would turn
        one bad step into a level that never recovers.
        """
        solved = set(converged)

        for attachment in self.attachments:
            if attachment.domain not in solved:
                continue

            if attachment.port.direction == INLET:
                self.vessel.inlet_flow = attachment.net_flow
            else:
                self.vessel.outlet_flow = attachment.net_flow

    def __repr__(self) -> str:
        return f"VesselCoupling({self.vessel.tag!r}, {list(self.attachments)})"


def build_couplings(
    devices: Iterable[Equipment],
    topologies: Mapping[str, Topology],
) -> list[VesselCoupling]:
    """Bind every coupling device in the plant to the graph around it.

    Raises where an attachment cannot be coupled safely — an unclassifiable
    flow unit, two domains meeting at one node, or a liquid attachment on an
    internal node. All three are modelling errors, and the Engine is built
    once per plant, so this is the cheapest place to find them.
    """
    return [
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

    if _unit_at(device, port, domain, topology, node) != INVENTORY_UNIT:
        return None

    if not node.is_boundary:
        raise ValueError(
            f"{device.tag}.{port.name} couples to node {node.id!r}, which is "
            f"internal — a coupling device's attachment must be a boundary "
            f"node of the domain it terminates (ADR 0001, A7)",
        )

    return Attachment(port, domain, topology, node)


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
    domain: str,
    topology: Topology,
    node: Node,
) -> str | None:
    """The flow unit of the branches meeting `node`, or None if there are
    none to read it from.

    An empty answer is not treated as liquid. A domain may legitimately hold
    nodes and no branches (ADR 0001, A8), and the vessel venting to an
    equipment-free gas boundary is exactly that case — assuming GPM there
    would couple the one attachment this module is least able to check.
    """
    units = set()

    for branch in topology.branches_at(node.id):
        unit = _flow_unit(branch.device, domain)

        if unit is None:
            raise ValueError(
                f"{device.tag}.{port.name} attaches to node {node.id!r}, which "
                f"carries {branch.device.tag} "
                f"({type(branch.device).__name__}) — its flow unit is not "
                f"declared in app/engine/coupling.py, and guessing one would "
                f"mix {GPM} with {SCFM}",
            )

        units.add(unit)

    if len(units) > 1:
        raise ValueError(
            f"{device.tag}.{port.name} attaches to node {node.id!r}, where "
            f"branches disagree on flow unit ({sorted(units)}) — one node "
            f"carries one unit",
        )

    return units.pop() if units else None


def _flow_unit(device: Equipment, domain: str) -> str | None:
    for kind in type(device).__mro__:
        if kind in FLOW_UNITS:
            return FLOW_UNITS[kind]

        if kind in DOMAIN_RESOLVED:
            if domain not in DOMAIN_UNITS:
                raise ValueError(
                    f"{device.tag} ({type(device).__name__}) takes its flow "
                    f"unit from its domain, but domain {domain!r} has no "
                    f"declared flow unit — only {sorted(DOMAIN_UNITS)}",
                )

            return DOMAIN_UNITS[domain]

    return None
