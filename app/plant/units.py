"""
Point units (T20-1) - the unit of every trend point, from the plant.

A trend window or a faceplate shows a unit beside every value, and the page
must never guess one from a field name. `point_units` names the unit of each
point the operator view publishes, once, when a plant runtime is built.

**Equipment fields** come from `FIELD_UNITS`, an explicit per-class allowlist
in the same shape as `app.api.visibility.VISIBLE`: a field missing from it has
no unit (`''`) until someone lists it. A field listed as `DOMAIN_FLOW` is a
flow, whose unit is that of the domain the device's branch sits in. A device in
a branch also gets units for the solved points the engine composes onto its row
(`SOLVED_POINT_UNITS`) unless its class lists them itself.

**Streams** carry their domain's flow unit. A domain's unit comes from the
same classification `app.engine.coupling` already applies, and never from a
domain, node or port name (ADR 0002 Amendment 2): the machines in it confirm
one (`flow_unit`), and a vessel's coupling attachments carry the unit its
declared phase gives. Two sources that disagree are a build error naming both;
a domain nothing classifies - only valves, no vessel - has no unit, rather
than a guess.

**Nodes** carry psia for pressure and °F for temperature; a stream's pressure
and temperature read the same. **Loops**: pv and sp take the unit of the
loop's measurement (`LoopBinding.pv_unit`, which `app.controls.loader` takes
from `unit_lookup` when it binds the loop), and out is a fraction.

`FRACTION` marks a value from 0 to 1 that every display shows as a percent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from app.engine.coupling import GPM, SCFM, UNIT_NEUTRAL, VesselCoupling, flow_unit
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.exchanger import HeatExchanger
from app.equipment.furnace import Furnace
from app.equipment.pump import CentrifugalPump
from app.equipment.relief import ReliefValve
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.historian.points import trend_values
from app.plant.topology import Topology
from app.statetypes import JSONValue

if TYPE_CHECKING:
    # The loader takes its loops' units from this module, so the import runs
    # one way only at runtime.
    from app.controls.loader import LoopBinding


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
    # The fields below the first line of each entry are process measurements
    # the operator view hides but a loop may measure (T20-4).
    Vessel: {
        "level": FRACTION,
        "pressure": PSIA,
        "inlet_flow": GPM,
        "outlet_flow": GPM,
        "gas_inlet_flow": SCFM,
        "gas_outlet_flow": SCFM,
    },
    HeatExchanger: {
        "inlet_temperature": DEG_F,
        "cold_temperature": DEG_F,
        "metal_temperature": DEG_F,
    },
    Furnace: {"firing_rate": BTU_PER_HOUR, "duty_setpoint": BTU_PER_HOUR},
    ReliefValve: {"inlet_pressure": PSIA},
}

# The solved points the engine composes onto the row of a device in one branch
# (T9-5), for a class that does not list them itself.
SOLVED_POINT_UNITS: dict[str, str] = {
    "flow": DOMAIN_FLOW,
    "inlet_pressure": PSIA,
    "outlet_pressure": PSIA,
}

NODE_UNITS: dict[str, str] = {"pressure": PSIA, "temperature": DEG_F}
STREAM_UNITS: dict[str, str] = {"flow": DOMAIN_FLOW, "pressure": PSIA, "temperature": DEG_F}


def domain_units(
    topologies: Mapping[str, Topology],
    couplings: Iterable[VesselCoupling],
) -> dict[str, str]:
    """The flow unit of each domain, `''` where nothing classifies it.
    `ValueError` when two sources in one domain disagree.

    `load_plant` refuses such a plant first (T20-20), so this check is a
    backstop that a loaded plant never reaches; it stays for topologies built
    by hand.
    """
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


def unit_lookup(
    equipment: Mapping[str, Equipment],
    topologies: Mapping[str, Topology],
    couplings: Iterable[VesselCoupling],
) -> Callable[[str, str, str], str]:
    """`unit(section, row_id, field)`: the unit of one published point of the
    plant whose equipment, topologies and couplings are given, `''` where
    nothing names one."""
    by_domain = domain_units(topologies, couplings)
    flow_units: dict[str, dict[str, str]] = {"equipment": {}, "streams": {}}

    for domain, topology in topologies.items():
        for branch in topology.branches.values():
            flow_units["streams"][branch.id] = by_domain[domain]
            flow_units["equipment"][branch.device.tag] = by_domain[domain]

    def unit(section: str, row_id: str, field: str) -> str:
        if section == "equipment":
            device = equipment.get(row_id)
            listed = FIELD_UNITS.get(type(device), {}) if device is not None else {}

            if field in listed:
                found = listed[field]
            elif row_id in flow_units["equipment"]:
                found = SOLVED_POINT_UNITS.get(field, NO_UNIT)
            else:
                found = NO_UNIT
        elif section == "nodes":
            found = NODE_UNITS.get(field, NO_UNIT)
        elif section == "streams":
            found = STREAM_UNITS.get(field, NO_UNIT)
        else:
            found = NO_UNIT

        return flow_units.get(section, {}).get(row_id, NO_UNIT) if found == DOMAIN_FLOW else found

    return unit


def point_units(
    view: Mapping[str, JSONValue],
    equipment: Mapping[str, Equipment],
    loops: Mapping[str, LoopBinding],
    topologies: Mapping[str, Topology],
    couplings: Iterable[VesselCoupling],
) -> dict[str, str]:
    """The unit of every trend point of `view`, an operator view of the plant
    whose equipment, loops, topologies and couplings are given."""
    unit = unit_lookup(equipment, topologies, couplings)
    units: dict[str, str] = {}

    for section in ("equipment", "nodes", "streams"):
        for point in _points(view, section):
            row_id, _, field = point.rpartition(".")
            units[point] = unit(section, row_id, field)

    for point in _points(view, "controllers"):
        tag, _, field = point.rpartition(".")
        binding = loops.get(tag)

        if field == "out":
            units[point] = FRACTION
        else:
            units[point] = binding.pv_unit if binding is not None else NO_UNIT

    return units


def _points(view: Mapping[str, JSONValue], section: str) -> Iterable[str]:
    return trend_values({section: view.get(section)})
