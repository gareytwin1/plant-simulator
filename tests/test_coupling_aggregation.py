"""
Coupling aggregates typed connections — T5-6.

The contract under test is docs/ADR_0002_TYPED_PORTS.md as amended by
Amendment 2. Everything here is one of six claims:

  * the **node** is the unit of account — several nozzles on one node are one
    exchange, and distinct nodes are independent and sum;
  * a node declaring both directions keeps its gross components, so a vessel
    fed and drained through one node has throughput at a net of zero, and a
    node declaring one direction keeps its net, so the idiom that already
    worked still conserves;
  * the components are signed sums over branch orientation, never magnitudes;
  * declared `phase` classifies, machines confirm, domain names say nothing,
    and a known unit-neutral device is not an unknown one;
  * `purpose` and `control` never reach a balance;
  * a failed domain holds the whole aggregate it feeds, not part of it.

The reference numbers are ADR 0002's own. §2.3's two vapor withdrawals are
reproduced exactly — 141.4214 SCFM from K-101 against a 280 psia discharge,
400.0000 SCFM through a Cv 100 vent to 84 psia — and the 141.4214 SCFM that
used to be overwritten is recovered.

The shared feed/drain node is §2.2's idiom with round design values rather
than that probe's unrecorded ones. The pump lifts N-101 (40 psia) into the
vessel's node and the drain valve takes it to N-103 (100 psia), so the steady
state solves 75 - 1.15e-4 q^2 = 60 for q = 361.1576 GPM, and the head feedback
puts the node at 113.0435 psia, which is level 0.1521739 on a 20 psi head at
full. It is self-regulating rather than horizon-bounded: level and both flows
sit inside a band 4e-8 wide relative from step 5 000 out past step 200 000,
with no clamp and no drift.

Every multi-nozzle vessel below is the production `Vessel` in configured-port
mode (T5-7), never a test-only subclass: a nozzle set that isn't `inlet` /
`outlet` comes from a `ports` entry whose every port carries `direction`, and
`Vessel` is the one class that opts in to building its port set that way. A
port set of exactly `inlet` / `outlet` needs no `direction` at all and stays
in the fixed-port mode every other device uses.
"""

import pytest

from app.engine.coupling import (
    GPM,
    SCFM,
    build_couplings,
)
from app.engine.engine import Engine
from app.engine.network import NetworkSolver
from app.equipment.base import INLET, OUTLET, Equipment, signed_square
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant


RUNNING_PUMP = {"running": True, "speed": 1.0, "speed_target": 1.0}
LOADED_COMPRESSOR = {"running": True, "load": 1.0, "load_target": 1.0}

# The §2.3 figures, to the four decimals the ADR records them at.
K_101_WITHDRAWAL = 141.4214
PV_101_WITHDRAWAL = 400.0000

# The shared-node steady state this module's own fixture settles at.
SETTLED_FLOW = 361.15756
SETTLED_LEVEL = 0.15217391
SETTLED_PRESSURE = 113.04348


# --- a branch device this module has never heard of -----------------------


class MysteryDevice(Equipment):
    """A branch device this module has never heard of. Unlike the control
    valve it is not unit-neutral, it is unclassifiable, and the difference
    is the whole of T5-6's valve rule.
    """

    def __init__(self, tag="XX-101"):
        super().__init__(tag, ports={"inlet": INLET, "outlet": OUTLET})

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return -0.0001 * signed_square(flow)

    def get_state(self):
        return {}


def types_for(**extra):
    kinds = {
        "pump": CentrifugalPump,
        "compressor": GasCompressor,
        "control_valve": ControlValve,
        "vessel": Vessel,
    }
    kinds.update(extra)

    return kinds


def built(config, **extra):
    plant = load_plant(config, device_types=types_for(**extra))

    return plant, Engine.from_plant(plant)


def flow_of(plant, domain, branch_id):
    return plant.topologies[domain].branches[branch_id].flow


# --- R1: distinct nodes are independent and sum ---------------------------


def two_vapor_withdrawals(purpose="vent", control="pressure", vent_domain="flare_header"):
    """ADR 0002 §2.3: two vapor outlets, two nodes, two domains.

    K-101 lifts the vessel's 100 psia to a 280 psia discharge, which is
    220 - 0.002 q^2 = 180 and q = sqrt(20000) = 141.4214 SCFM. PV-101 is a
    Cv 100 linear valve wide open onto 84 psia, which is 100 * sqrt(16) =
    400.0000 SCFM.

    `vapor_a` and `vapor_b` are two distinct outlet nozzles the vessel has no
    fixed port for, so this item is in configured-port mode: both carry a
    `direction`, and both must.
    """
    vapor_b = {"node": "N-301", "phase": "vapor", "purpose": purpose, "direction": OUTLET}

    if control is not None:
        vapor_b["control"] = control

    return {
        "nodes": [
            {"id": "N-201", "boundary": True, "pressure": 100.0, "domain": "gas_process"},
            {"id": "N-202", "boundary": True, "pressure": 280.0, "domain": "gas_process"},
            {"id": "N-301", "boundary": True, "pressure": 100.0, "domain": vent_domain},
            {"id": "N-302", "boundary": True, "pressure": 84.0, "domain": vent_domain},
        ],
        "equipment": [
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-201",
                "node_out": "N-202",
                "design": dict(LOADED_COMPRESSOR),
            },
            {
                "tag": "PV-101",
                "type": "control_valve",
                "node_in": "N-301",
                "node_out": "N-302",
                "design": {"capacity": 100.0},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "vapor_a": {
                        "node": "N-201",
                        "phase": "vapor",
                        "purpose": "process",
                        "direction": OUTLET,
                    },
                    "vapor_b": vapor_b,
                },
                "paths": [],
                "design": {"gas_volume": 1000.0, "initial_pressure": 100.0},
            },
        ],
    }


def test_two_vapor_withdrawals_on_two_nodes_are_summed_not_overwritten():
    """The defect T5-6 exists to fix, measured against the ADR's own figures.

    Before this task `write_flows` assigned to `gas_outlet_flow` once per
    attachment, so whichever port came last won and the other withdrawal
    left the pressure balance with no error raised.
    """
    plant, engine = built(two_vapor_withdrawals())
    vessel = plant.devices["V-101"]

    through_k = flow_of(plant, "gas_process", "B-K-101")
    through_pv = flow_of(plant, "flare_header", "B-PV-101")

    assert through_k == pytest.approx(K_101_WITHDRAWAL, abs=5e-5)
    assert through_pv == pytest.approx(PV_101_WITHDRAWAL, abs=5e-5)

    assert vessel.gas_outlet_flow == pytest.approx(through_k + through_pv)
    assert vessel.gas_outlet_flow == pytest.approx(541.4214, abs=5e-5)

    # The 141.4214 SCFM the old assignment lost, named explicitly: the total
    # exceeds the larger withdrawal by exactly the smaller one.
    assert vessel.gas_outlet_flow - through_pv == pytest.approx(
        K_101_WITHDRAWAL,
        abs=5e-5,
    )

    # Nothing was written to a phase or a direction that has no declaration.
    assert vessel.gas_inlet_flow == pytest.approx(0.0)
    assert vessel.inlet_flow == pytest.approx(0.0)
    assert vessel.outlet_flow == pytest.approx(0.0)


def test_both_withdrawals_move_the_one_pressure_they_share():
    """Aggregation is not bookkeeping: the vessel drains on the sum, so the
    pressure falls faster than either withdrawal alone would take it.
    """
    plant, engine = built(two_vapor_withdrawals())
    vessel = plant.devices["V-101"]

    both = vessel.gas_outlet_flow

    engine.step(1.0)

    assert vessel.pressure < 100.0
    # dP/dt = P_std * (-Q) / V_gas over one minute's sixtieth.
    assert vessel.pressure == pytest.approx(
        100.0 - 14.696 * both / 60.0 / 1000.0,
        rel=1e-3,
    )


# --- R2: one node is counted once -----------------------------------------


def shared_liquid_node(ports, vessel_design=None, pump_speed=1.0):
    """ADR 0002 §2.2's idiom: feed and drain meeting on the vessel's node.

    N-102 is the vessel's attachment and carries both branches. A boundary
    node has no mass balance, so the two flows are independent and the head
    the level offers is the only thing coupling them.
    """
    pump = dict(RUNNING_PUMP)
    pump["speed"] = pump_speed
    pump["speed_target"] = pump_speed

    design = {"capacity": 5000.0, "head_at_full": 20.0, "level": 0.5}
    design.update(vessel_design or {})

    return {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 40.0, "domain": "liquid"},
            {"id": "N-102", "boundary": True, "pressure": 110.0, "domain": "liquid"},
            {"id": "N-103", "boundary": True, "pressure": 100.0, "domain": "liquid"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": pump,
            },
            {
                "tag": "LV-101",
                "type": "control_valve",
                "node_in": "N-102",
                "node_out": "N-103",
                "design": {"capacity": 100.0},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": ports,
                "paths": [],
                "design": design,
            },
        ],
    }


# Fixed-port form: no `direction`, so these ride on the vessel's own `inlet`
# and `outlet` — no configured-port mode needed, matching a real
# feed-and-drain separator with exactly the two nozzles it starts with.
FEED_PORT = {"node": "N-102", "phase": "liquid", "purpose": "process"}
DRAIN_PORT = {"node": "N-102", "phase": "liquid", "purpose": "drain"}

BOTH_DIRECTIONS = {"inlet": FEED_PORT, "outlet": DRAIN_PORT}

# Configured-port form: a single nozzle, or two sharing one direction, has no
# fixed-port shape to fall back on, so every entry here carries `direction`.
CONFIGURED_FEED_PORT = {**FEED_PORT, "direction": INLET}
CONFIGURED_DRAIN_PORT = {**DRAIN_PORT, "direction": OUTLET}

DRAIN_ONLY = {"drain": CONFIGURED_DRAIN_PORT}
FEED_ONLY = {"feed": CONFIGURED_FEED_PORT}
TWIN_DRAINS = {"drain_a": CONFIGURED_DRAIN_PORT, "drain_b": CONFIGURED_DRAIN_PORT}


def node_exchange(plant):
    arrivals = flow_of(plant, "liquid", "B-P-101")
    departures = flow_of(plant, "liquid", "B-LV-101")

    return arrivals, departures


def test_a_node_declaring_both_directions_keeps_its_gross_components():
    """The case net-only accounting gets wrong. Feed and drain are equal at
    the steady state, so the node's net exchange is zero — and the vessel is
    still passing 361 GPM, which is what residence time is made of.
    """
    plant, engine = built(shared_liquid_node(BOTH_DIRECTIONS))
    vessel = plant.devices["V-101"]

    at = {}

    for step in range(1, 20001):
        engine.step(1.0)

        arrivals, departures = node_exchange(plant)

        # Exact, at every step: the aggregates *are* the node's components.
        assert vessel.inlet_flow == arrivals
        assert vessel.outlet_flow == departures
        assert vessel.inlet_flow - vessel.outlet_flow == arrivals - departures

        if step in (5000, 20000):
            at[step] = (
                vessel.level,
                vessel.inlet_flow,
                vessel.outlet_flow,
                plant.nodes["N-102"].pressure,
            )

    assert at[5000] == pytest.approx(at[20000], rel=1e-6)

    level, inlet, outlet, pressure = at[20000]

    assert level == pytest.approx(SETTLED_LEVEL, rel=1e-6)
    assert inlet == pytest.approx(SETTLED_FLOW, rel=1e-6)
    assert outlet == pytest.approx(SETTLED_FLOW, rel=1e-6)
    assert pressure == pytest.approx(SETTLED_PRESSURE, rel=1e-6)

    # Self-regulating, not horizon-bounded: nothing clamped to get here.
    assert 0.0 < level < 1.0

    # Throughput survives a net of zero, so residence time is a number.
    assert vessel.residence_time == pytest.approx(
        vessel.volume / vessel.outlet_flow * 60.0,
    )
    assert vessel.residence_time > 0.0


def test_a_one_sided_declaration_keeps_the_node_net_and_loses_no_feed():
    """The historical idiom, which must go on conserving.

    Only the drain is declared, and the node carries a feed as well. The
    unmatched arrival is folded into the net rather than dropped — so the
    vessel settles at exactly the level the two-sided declaration settles
    at, reached through a different pair of numbers.
    """
    plant, engine = built(shared_liquid_node(DRAIN_ONLY))
    vessel = plant.devices["V-101"]

    for _ in range(5000):
        engine.step(1.0)

        arrivals, departures = node_exchange(plant)

        assert vessel.inlet_flow == 0.0
        assert vessel.outlet_flow == departures - arrivals

    assert vessel.level == pytest.approx(SETTLED_LEVEL, rel=1e-6)
    assert flow_of(plant, "liquid", "B-P-101") == pytest.approx(
        SETTLED_FLOW,
        rel=1e-6,
    )

    # The feed is in the balance even though no port declares it: drop it and
    # the vessel would empty against a drain with nothing replacing it.
    assert vessel.outlet_flow == pytest.approx(0.0, abs=1e-4)
    assert vessel.residence_time is None


def test_duplicate_ports_of_one_direction_do_not_multiply_the_exchange():
    """Two nozzles, one node, one exchange. The count of semantic ports is
    not a hydraulic quantity.
    """
    one, _ = built(shared_liquid_node(DRAIN_ONLY))
    two, _ = built(shared_liquid_node(TWIN_DRAINS))

    single = one.devices["V-101"]
    doubled = two.devices["V-101"]

    assert len(doubled.ports) == 2
    assert doubled.outlet_flow == single.outlet_flow
    assert doubled.level == single.level

    # And the node was written once, not twice.
    assert two.nodes["N-102"].pressure == one.nodes["N-102"].pressure
    assert two.nodes["N-102"].pressure == pytest.approx(
        110.0 + doubled.head,
    )


def test_a_node_counted_once_survives_a_whole_run():
    """The duplicate is not merely equal on the first pass — it integrates
    identically, which it would not if the node were counted twice.
    """
    def run(ports):
        plant, engine = built(shared_liquid_node(ports))

        return [engine.step(5.0).as_dict() for _ in range(200)]

    assert run(DRAIN_ONLY) == run(TWIN_DRAINS)


# --- R3: the components are signed, never magnitudes ----------------------


def test_a_reversed_feed_arrives_negatively_and_is_not_clamped():
    """ADR 0002 §7.3's cold-start backflow, read through the aggregates.

    The pump is stopped against a node the level holds above its suction, so
    it runs backwards. `arrivals` is negative, and the inlet aggregate is
    that number exactly — clamping it would invent inventory.
    """
    plant, engine = built(shared_liquid_node(BOTH_DIRECTIONS, pump_speed=0.0))
    vessel = plant.devices["V-101"]

    arrivals, departures = node_exchange(plant)

    assert arrivals < 0.0
    assert departures > 0.0

    assert vessel.inlet_flow == arrivals
    assert vessel.inlet_flow < 0.0
    assert vessel.outlet_flow == departures

    # Both components leave the vessel, so the net is more negative than
    # either one — which an abs() or a max(flow, 0) would hide.
    assert vessel.inlet_flow - vessel.outlet_flow < -departures


# --- R4: two phases on one node, and two vessels on one node --------------


def conflicting_phases():
    """Two nozzles on one node, one liquid and one vapor.

    Deliberately on a valve-only node: a valve confirms no unit, so nothing
    in the plant could expose the contradiction and the declarations have to
    be checked against each other. Both ports are the vessel's own fixed
    `inlet` / `outlet` — no configured-port mode needed to declare a phase
    conflict.
    """
    return {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 60.0, "domain": "anything"},
            {"id": "N-102", "boundary": True, "pressure": 50.0, "domain": "anything"},
        ],
        "equipment": [
            {
                "tag": "FV-101",
                "type": "control_valve",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": {"capacity": 100.0},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "inlet": {"node": "N-101", "phase": "liquid", "purpose": "process"},
                    "outlet": {"node": "N-101", "phase": "vapor", "purpose": "vent"},
                },
                "paths": [],
                "design": {},
            },
        ],
    }


def test_two_phases_declared_on_one_node_are_refused():
    """A node may not be counted once as liquid and again as vapor, and the
    refusal names the node and both ports.
    """
    plant = load_plant(conflicting_phases(), device_types=types_for())

    with pytest.raises(ValueError) as raised:
        Engine.from_plant(plant)

    message = str(raised.value)

    assert "one node carries one phase" in message
    assert "N-101" in message
    assert "'inlet'" in message
    assert "'outlet'" in message


def test_two_vessels_on_one_node_are_refused():
    """Each coupling would read the whole node exchange as its own, so the
    same transfer would land in two inventories. There is no split to guess.
    """
    config = shared_liquid_node(DRAIN_ONLY)
    config["equipment"].append(
        {
            "tag": "V-102",
            "type": "vessel",
            "ports": {"drain": CONFIGURED_DRAIN_PORT},
            "paths": [],
            "design": {},
        },
    )

    plant = load_plant(config, device_types=types_for())

    with pytest.raises(ValueError) as raised:
        Engine.from_plant(plant)

    message = str(raised.value)

    assert "N-102" in message
    assert "V-101" in message
    assert "V-102" in message
    assert "drain" in message


# --- R5: a failed domain holds the whole aggregate ------------------------


def fed_and_two_vapors():
    config = two_vapor_withdrawals()

    config["nodes"] += [
        {"id": "N-101", "boundary": True, "pressure": 40.0, "domain": "liquid"},
        {"id": "N-102", "boundary": True, "pressure": 110.0, "domain": "liquid"},
    ]
    config["equipment"].insert(
        0,
        {
            "tag": "P-101",
            "type": "pump",
            "node_in": "N-101",
            "node_out": "N-102",
            # Ramping, so the liquid aggregate is visibly a new number each
            # step rather than one the solver left inside its tolerance.
            "design": {"running": True, "speed": 0.0, "speed_target": 1.0},
        },
    )

    vessel = config["equipment"][-1]
    vessel["ports"]["feed"] = {
        "node": "N-102",
        "phase": "liquid",
        "purpose": "process",
        "direction": INLET,
    }
    vessel["design"].update({"capacity": 5000.0, "head_at_full": 20.0, "level": 0.5})

    return config


def test_a_failed_domain_holds_its_whole_aggregate_and_no_other():
    """T4-3's rule, raised from per-attachment to per-aggregate.

    `gas_outlet_flow` draws on a converged node and a failed one. Writing the
    converged contributor alone would report 141.4214 SCFM — a number that
    mixes this step with last step's — so the whole attribute holds instead,
    while the liquid aggregate, whose own contributor converged, advances.
    """
    plant, engine = built(fed_and_two_vapors())
    vessel = plant.devices["V-101"]

    held_gas = vessel.gas_outlet_flow
    held_liquid = vessel.inlet_flow

    assert held_gas == pytest.approx(541.4214, abs=5e-5)

    engine.solvers["flare_header"] = NetworkSolver(
        plant.topologies["flare_header"],
        max_iterations=1,
    )

    for branch in plant.topologies["flare_header"].branches.values():
        branch.set_flow(0.0)

    engine.step(10.0)

    assert engine.solver_results["flare_header"].converged is False
    assert engine.solver_results["gas_process"].converged is True
    assert engine.solver_results["liquid"].converged is True

    # Held entirely — not the converged part of it.
    assert vessel.gas_outlet_flow == held_gas
    assert vessel.gas_outlet_flow != pytest.approx(
        flow_of(plant, "gas_process", "B-K-101"),
    )

    # The unrelated aggregate still moved, and is this step's number.
    assert vessel.inlet_flow != held_liquid
    assert vessel.inlet_flow == flow_of(plant, "liquid", "B-P-101")


# --- R6: phase declares, machines confirm, domain names say nothing -------


def vessel_across(branch_type, phase=None, domain="anything", design=None):
    """One branch device between two boundaries, with a two-port vessel
    wrapped around it. The vessel's ports are its own fixed `inlet` and
    `outlet` throughout — this group tests phase and unit classification, not
    port naming, so there is no reason to leave fixed-port mode. Typed only
    where `phase` says so, which is how the legacy form is exercised
    alongside the typed one.
    """
    def port(node_id):
        if phase is None:
            return node_id

        return {"node": node_id, "phase": phase, "purpose": "process"}

    return {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 60.0, "domain": domain},
            {"id": "N-102", "boundary": True, "pressure": 50.0, "domain": domain},
        ],
        "equipment": [
            {
                "tag": "XX-101",
                "type": branch_type,
                "node_in": "N-101",
                "node_out": "N-102",
                "design": design or {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": port("N-102"), "outlet": port("N-101")},
                "paths": [],
                "design": {},
            },
        ],
    }


def units_of(config, **extra):
    plant = load_plant(config, device_types=types_for(**extra))
    coupling = build_couplings(plant.devices.values(), plant.topologies)[0]

    return {a.port.name: a.unit for a in coupling.attachments}


def test_a_declared_phase_classifies_beside_a_unit_neutral_valve():
    """A valve carries whatever the line carries, so it confirms nothing and
    the declaration is the whole answer — under any domain name.
    """
    for domain in ("liquid", "gas", "process_water", "flare_header", "default"):
        assert units_of(
            vessel_across("control_valve", phase="liquid", domain=domain),
        ) == {"inlet": GPM, "outlet": GPM}

        assert units_of(
            vessel_across("control_valve", phase="vapor", domain=domain),
        ) == {"inlet": SCFM, "outlet": SCFM}


def test_an_unknown_branch_device_still_raises_rather_than_abstaining():
    """Unit-neutral is a property of known equipment. An unrecognised model
    is the case the flow-unit table exists to catch, and a declared phase
    does not excuse it.
    """
    plant = load_plant(
        vessel_across("heat_exchanger", phase="liquid"),
        device_types=types_for(heat_exchanger=MysteryDevice),
    )

    with pytest.raises(ValueError, match="flow unit is not declared"):
        Engine.from_plant(plant)


def test_a_declared_phase_the_machines_contradict_is_refused():
    """A pump is GPM and a compressor is SCFM. Either one beside the opposite
    declaration is a modelling error, named by port.
    """
    liquid_plant = load_plant(
        vessel_across("pump", phase="vapor", design=dict(RUNNING_PUMP)),
        device_types=types_for(),
    )

    with pytest.raises(ValueError, match=r"V-101\.\w+ declares phase 'vapor'"):
        Engine.from_plant(liquid_plant)

    gas_plant = load_plant(
        vessel_across("compressor", phase="liquid", design=dict(LOADED_COMPRESSOR)),
        device_types=types_for(),
    )

    with pytest.raises(ValueError, match=r"V-101\.\w+ declares phase 'liquid'"):
        Engine.from_plant(gas_plant)


def test_an_untyped_port_still_reads_its_unit_off_the_machine_beside_it():
    """The legacy form, unchanged where it was ever unambiguous."""
    assert units_of(
        vessel_across("pump", domain="process_water", design=dict(RUNNING_PUMP)),
    ) == {"inlet": GPM, "outlet": GPM}

    assert units_of(
        vessel_across("compressor", domain="process_water", design=dict(LOADED_COMPRESSOR)),
    ) == {"inlet": SCFM, "outlet": SCFM}


def test_an_untyped_port_with_nothing_to_read_is_refused_and_asks_for_a_phase():
    """A valve-only node and a branchless node both used to be answered by
    the domain name. Neither is any more.

    The branchless case wires the vessel's fixed `inlet` to a node with
    literally no other equipment on it, and its `outlet` to a domain a pump
    confirms — isolating the one ambiguous port without needing a second
    vessel nozzle a configured port set would otherwise be free to name.
    """
    valve_only = load_plant(vessel_across("control_valve", domain="gas"))

    with pytest.raises(ValueError, match="declare phase: 'liquid' or phase: 'vapor'"):
        Engine.from_plant(valve_only)

    config = {
        "nodes": [
            {"id": "N-401", "boundary": True, "pressure": 100.0, "domain": "flare_header"},
            {"id": "N-501", "boundary": True, "pressure": 40.0, "domain": "liquid"},
            {"id": "N-502", "boundary": True, "pressure": 60.0, "domain": "liquid"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-501",
                "node_out": "N-502",
                "design": dict(RUNNING_PUMP),
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-401", "outlet": "N-502"},
                "paths": [],
                "design": {},
            },
        ],
    }

    branchless = load_plant(config)

    with pytest.raises(ValueError, match=r"V-101\.inlet.*declares no phase"):
        Engine.from_plant(branchless)


def test_a_typed_boundary_with_no_branch_at_all_still_couples():
    """A vent header nothing is wired to yet is a vapor connection because
    the port says so. It carries the vessel's pressure and exchanges nothing.
    """
    config = two_vapor_withdrawals()
    config["nodes"] = [
        node for node in config["nodes"] if node["id"] != "N-302"
    ]
    config["equipment"] = [
        item for item in config["equipment"] if item["tag"] != "PV-101"
    ]

    plant, engine = built(config)
    vessel = plant.devices["V-101"]

    engine.step(1.0)

    assert vessel.gas_active is True
    assert plant.nodes["N-301"].pressure == pytest.approx(vessel.pressure)
    assert plant.nodes["N-301"].configured_pressure == pytest.approx(100.0)

    # Only K-101 withdraws; the branchless nozzle adds a clean zero.
    assert vessel.gas_outlet_flow == flow_of(plant, "gas_process", "B-K-101")


# --- R7: purpose and control never reach a balance ------------------------


def test_purpose_and_control_do_not_change_the_balance():
    """Every vapor withdrawal is a vapor withdrawal (ADR 0002, A.3)."""
    variants = (
        ("process", None),
        ("vent", "pressure"),
        ("relief", None),
        ("drain", "level"),
    )

    results = set()

    for purpose, control in variants:
        plant, engine = built(two_vapor_withdrawals(purpose=purpose, control=control))
        vessel = plant.devices["V-101"]

        engine.step(10.0)

        results.add(
            (
                vessel.gas_outlet_flow,
                vessel.gas_inlet_flow,
                vessel.pressure,
                plant.nodes["N-301"].pressure,
            ),
        )

    assert len(results) == 1


# --- R8: boundary pressures are written per node --------------------------


def test_several_gas_nozzles_on_one_node_write_one_pressure():
    config = two_vapor_withdrawals()
    config["equipment"][-1]["ports"]["vapor_b"] = {
        "node": "N-201",
        "phase": "vapor",
        "purpose": "vent",
        "direction": OUTLET,
    }
    config["nodes"] = [
        node for node in config["nodes"] if node["id"] not in ("N-301", "N-302")
    ]
    config["equipment"] = [
        item for item in config["equipment"] if item["tag"] != "PV-101"
    ]

    plant, engine = built(config)
    vessel = plant.devices["V-101"]

    engine.step(1.0)

    assert plant.nodes["N-201"].pressure == vessel.pressure
    # One node, one exchange: the compressor's withdrawal, counted once.
    assert vessel.gas_outlet_flow == flow_of(plant, "gas_process", "B-K-101")


def test_several_liquid_outlets_on_one_node_apply_the_head_once():
    plant, engine = built(shared_liquid_node(TWIN_DRAINS))
    vessel = plant.devices["V-101"]

    engine.step(1.0)

    assert vessel.head > 0.0
    assert plant.nodes["N-102"].pressure == pytest.approx(110.0 + vessel.head)
    assert plant.nodes["N-102"].pressure != pytest.approx(110.0 + 2.0 * vessel.head)
    assert plant.nodes["N-102"].configured_pressure == pytest.approx(110.0)


def test_an_inlet_only_liquid_node_keeps_its_configured_battery_limit():
    """The plant delivers to it; the vessel does not supply it, so no head
    lands however full the vessel is.
    """
    config = shared_liquid_node(FEED_ONLY, vessel_design={"level": 1.0})
    config["nodes"] = [
        node for node in config["nodes"] if node["id"] != "N-103"
    ]
    config["equipment"] = [
        item for item in config["equipment"] if item["tag"] != "LV-101"
    ]

    plant, engine = built(config)
    vessel = plant.devices["V-101"]

    engine.step(1.0)

    assert vessel.head == pytest.approx(20.0)
    assert plant.nodes["N-102"].pressure == 110.0


def test_a_phase_conflict_is_caught_before_any_pressure_is_written():
    """The refusal is at Engine construction, so the contradictory pair never
    reaches the point where one of them would write a boundary — neither the
    vessel pressure a vapor nozzle would replace it with, nor the head a
    liquid one would add.
    """
    plant = load_plant(conflicting_phases(), device_types=types_for())

    with pytest.raises(ValueError, match="one node carries one phase"):
        Engine.from_plant(plant)

    assert plant.nodes["N-101"].pressure == 60.0
    assert plant.nodes["N-102"].pressure == 50.0
