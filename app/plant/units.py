"""
Point units (T20-1) - the unit of every trend point, from the plant.

A trend window or a faceplate shows a unit beside every value, and the page
must never guess one from a field name. `point_units` names the unit of each
point the operator view publishes, once, when a plant runtime is built.

**Equipment fields** come from `FIELD_UNITS`, an explicit per-class allowlist
in the same shape as `app.api.visibility.VISIBLE`: a field missing from it has
no unit (`''`) until someone lists it. A field listed as `DOMAIN_FLOW` is a
flow, whose unit is that of the domain the device's branch sits in.

**Streams** carry their domain's flow unit. A domain's unit comes from the
same classification `app.engine.coupling` already applies, and never from a
domain, node or port name (ADR 0002 Amendment 2): the machines in it confirm
one (`flow_unit`), and a vessel's coupling attachments carry the unit its
declared phase gives. Two sources that disagree are a build error naming both;
a domain nothing classifies - only valves, no vessel - has no unit, rather
than a guess.

**Nodes** are pressures, psia. **Loops**: pv and sp take the unit of the
loop's measurement (`LoopBinding.pv_unit`), and out is a fraction.

`FRACTION` marks a value from 0 to 1 that every display shows as a percent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.controls.loader import LoopBinding
from app.engine.coupling import UNIT_NEUTRAL, VesselCoupling, flow_unit
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.exchanger import HeatExchanger
from app.equipment.furnace import Furnace
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.historian.points import trend_values
from app.plant.topology import Topology
from app.statetypes import JSONValue


PSIA = "psia"
DEG_F = "°F"
BTU_PER_HOUR = "BTU/hr"
FRACTION = "fraction"
NO_UNIT = ""

# Not a unit: the field is a flow, in its device's domain unit.
DOMAIN_FLOW = "<domain flow>"

FIELD_UNITS: dict[type[Equipment], dict[str, str]] = {
    CentrifugalPump: {
        "speed": FRACTION,
        "speed_target": FRACTION,
        "flow": DOMAIN_FLOW,
        "inlet_pressure": PSIA,
        "outlet_pressure": PSIA,
    },
    GasCompressor: {
        "load": FRACTION,
        "load_target": FRACTION,
        "flow": DOMAIN_FLOW,
        "inlet_pressure": PSIA,
        "outlet_pressure": PSIA,
    },
    ControlValve: {"position": FRACTION, "position_target": FRACTION},
    Vessel: {"level": FRACTION, "pressure": PSIA},
    HeatExchanger: {"inlet_temperature": DEG_F},
    Furnace: {"firing_rate": BTU_PER_HOUR, "duty_setpoint": BTU_PER_HOUR},
}


def domain_units(
    topologies: Mapping[str, Topology],
    couplings: Iterable[VesselCoupling],
) -> dict[str, str]:
    """The flow unit of each domain, `''` where nothing classifies it.
    `ValueError` when two sources in one domain disagree."""
    sources: dict[str, dict[str, str]] = {domain: {} for domain in topologies}

    for domain, topology in topologies.items():
        for branch in topology.branches.values():
            unit = flow_unit(branch.device)

            if unit is not None and unit != UNIT_NEUTRAL:
                sources[domain].setdefault(unit, branch.device.tag)

    for coupling in couplings:
        for attachment in coupling.attachments:
            sources[attachment.domain].setdefault(
                attachment.unit, f"{coupling.vessel.tag}.{attachment.port.name}",
            )

    units: dict[str, str] = {}

    for domain, found in sources.items():
        if len(found) > 1:
            named = ", ".join(f"{source} says {unit}" for unit, source in sorted(found.items()))
            raise ValueError(f"domain {domain!r} has no single flow unit: {named}")

        units[domain] = next(iter(found), NO_UNIT)

    return units


def point_units(
    view: Mapping[str, JSONValue],
    equipment: Mapping[str, Equipment],
    loops: Mapping[str, LoopBinding],
    topologies: Mapping[str, Topology],
    couplings: Iterable[VesselCoupling],
) -> dict[str, str]:
    """The unit of every trend point of `view`, an operator view of the plant
    whose equipment, loops, topologies and couplings are given."""
    by_domain = domain_units(topologies, couplings)
    branch_unit: dict[str, str] = {}
    device_unit: dict[str, str] = {}

    for domain, topology in topologies.items():
        for branch in topology.branches.values():
            branch_unit[branch.id] = by_domain[domain]
            device_unit[branch.device.tag] = by_domain[domain]

    units: dict[str, str] = {}

    for point in _points(view, "equipment"):
        tag, field = point.rsplit(".", 1)
        device = equipment.get(tag)
        unit = FIELD_UNITS.get(type(device), {}).get(field, NO_UNIT) if device is not None else NO_UNIT
        units[point] = device_unit.get(tag, NO_UNIT) if unit == DOMAIN_FLOW else unit

    for point in _points(view, "nodes"):
        units[point] = PSIA

    for point in _points(view, "streams"):
        units[point] = branch_unit.get(point.rsplit(".", 1)[0], NO_UNIT)

    for point in _points(view, "controllers"):
        tag, field = point.rsplit(".", 1)
        binding = loops.get(tag)

        if field == "out":
            units[point] = FRACTION
        else:
            units[point] = binding.pv_unit if binding is not None else NO_UNIT

    return units


def _points(view: Mapping[str, JSONValue], section: str) -> Iterable[str]:
    return trend_values({section: view.get(section)})
