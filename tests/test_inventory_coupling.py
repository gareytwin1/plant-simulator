"""
Level to hydraulics coupling — T5-2.

The first time slow state pushes back on the plant. Everything here is one
of four claims:

  * down — a vessel's level offsets the boundary of the domain it supplies,
    as a head added to the as-built pressure and never as the pressure
    itself;
  * up — the vessel's flows are the net signed exchange at its attachment
    nodes, and the sign comes from port direction;
  * the two are connected by one explicit-Euler step with the lag on the
    flows, and the loop is stable through level zero;
  * a port whose flow unit cannot be confirmed GPM is not coupled at all.

The reference plant is two liquid domains with the vessel between them, and
its numbers are closed-form. The feed pump lifts N-201 (60 psia) to N-202
(120 psia), so at full speed it carries sqrt((75 - 60) / 1.5e-5) = 1000 GPM
exactly. The draw pump lifts N-101, which the vessel feeds, to N-102 (110
psia), so it carries sqrt((15 + 20L) / 1.5e-5) — 1000 GPM at level 0 and
1527.5 at level 1. Higher level, higher suction, more flow.
"""

import ast
import copy
import math
from pathlib import Path

import pytest

from app.engine.coupling import FLOW_UNITS, GPM, build_couplings
from app.engine.engine import Engine
from app.engine.network import NetworkSolver
from app.equipment.base import INLET, OUTLET, Equipment, signed_square
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant

ROOT = Path(__file__).resolve().parent.parent

RUNNING = {"running": True, "speed": 1.0, "speed_target": 1.0}

HEAD_AT_FULL = 20.0
CAPACITY = 5000.0

FEED_FLOW = 1000.0


def draw_flow(level, speed=1.0):
    """The closed-form draw at this level: the pump curve met by the boundary
    difference, with the vessel's head standing in the middle of it.
    """
    return math.sqrt(
        (75.0 * speed ** 2 - 110.0 + 50.0 + HEAD_AT_FULL * level) / 1.5e-5,
    )


def coupled_config(level=0.5, draw_speed=1.0, **vessel_design):
    """Two liquid domains, the vessel between them.

    They are two domains rather than one because the loader requires each
    domain to be a single connected piece, and the vessel — which sits in no
    topology — is exactly what disconnects them.
    """
    draw = dict(RUNNING)
    draw["speed"] = draw_speed
    draw["speed_target"] = draw_speed

    design = {
        "head_at_full": HEAD_AT_FULL,
        "capacity": CAPACITY,
        "level": level,
    }
    design.update(vessel_design)

    return {
        "nodes": [
            {"id": "N-201", "boundary": True, "pressure": 60.0, "domain": "liquid_feed"},
            {"id": "N-202", "boundary": True, "pressure": 120.0, "domain": "liquid_feed"},
            {"id": "N-101", "boundary": True, "pressure": 50.0, "domain": "liquid_out"},
            {"id": "N-102", "boundary": True, "pressure": 110.0, "domain": "liquid_out"},
        ],
        "equipment": [
            {
                "tag": "P-201",
                "type": "pump",
                "node_in": "N-201",
                "node_out": "N-202",
                "design": dict(RUNNING),
            },
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": draw,
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-202", "outlet": "N-101"},
                "paths": [],
                "design": design,
            },
        ],
    }


def coupled(level=0.5, draw_speed=1.0, **vessel_design):
    plant = load_plant(coupled_config(level, draw_speed, **vessel_design))

    return plant, Engine.from_plant(plant)


# --- R1: level gives a head contribution, never an absolute pressure ------


def test_an_empty_vessel_leaves_the_boundary_at_its_configured_pressure():
    """The head is zero at level zero, so the battery limit is the as-built
    50 psia — positive, which is what C2 requires of every boundary.
    """
    plant, engine = coupled(level=0.0)
    node = plant.nodes["N-101"]

    assert node.pressure == pytest.approx(50.0)
    assert node.pressure > 0.0
    assert node.configured_pressure == pytest.approx(50.0)


def test_a_full_vessel_adds_exactly_head_at_full():
    plant, engine = coupled(level=1.0)

    assert plant.nodes["N-101"].pressure == pytest.approx(50.0 + HEAD_AT_FULL)


def test_the_boundary_is_monotonic_in_level():
    seen = []

    for level in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        plant, engine = coupled(level=level)

        seen.append(plant.nodes["N-101"].pressure)

        assert plant.nodes["N-101"].pressure == pytest.approx(
            50.0 + HEAD_AT_FULL * level,
        )

    assert seen == sorted(seen)


def test_the_head_never_accumulates():
    """A hundred steps at a level that cannot move must leave the boundary
    exactly where one step did. The base is read off configured_pressure
    every step, so the same level always produces the same boundary.
    """
    plant, engine = coupled(level=0.5)
    engine.stop()

    before = plant.nodes["N-101"].pressure

    for _ in range(100):
        engine.step(1.0)

    assert plant.nodes["N-101"].pressure == pytest.approx(before)
    assert plant.nodes["N-101"].pressure == pytest.approx(60.0)


def test_the_head_lands_on_the_outlet_attachment_only():
    """The node the vessel supplies carries the head. The node the plant
    delivers into keeps the battery limit its config gave it.
    """
    plant, engine = coupled(level=1.0)

    assert plant.nodes["N-101"].pressure == pytest.approx(70.0)
    assert plant.nodes["N-202"].pressure == pytest.approx(120.0)


# --- the acceptance criteria ---------------------------------------------


def test_draining_a_vessel_drops_pump_suction_pressure_and_flow():
    plant, engine = coupled(level=1.0)

    suction = []
    flow = []

    for _ in range(60):
        snapshot = engine.step(60.0)

        suction.append(snapshot.nodes["N-101"]["pressure"])
        flow.append(snapshot.streams["B-P-101"]["flow"])

    assert plant.devices["V-101"].level < 1.0
    assert suction == sorted(suction, reverse=True)
    assert flow == sorted(flow, reverse=True)
    assert suction[-1] < suction[0]
    assert flow[-1] < flow[0]


def test_the_carryover_flag_fires_at_the_configured_level():
    """Filling: the draw pump is slowed until the feed outruns it."""
    plant, engine = coupled(level=0.2, draw_speed=0.9, carryover_level=0.6)
    vessel = plant.devices["V-101"]

    assert vessel.carryover is False

    fired = None

    for step in range(400):
        snapshot = engine.step(60.0)

        if snapshot.equipment["V-101"]["carryover"] and fired is None:
            fired = snapshot.equipment["V-101"]["level"]

    assert fired is not None
    assert fired >= 0.6
    assert vessel.level > 0.6


def test_there_is_no_instability_as_level_crosses_zero():
    """The draw outruns the feed until the vessel empties, and at level zero
    the two are equal by construction — the crossing is a fixed point sat on
    exactly, not oscillated around.
    """
    plant, engine = coupled(level=0.5)
    vessel = plant.devices["V-101"]

    levels = []

    for _ in range(3000):
        snapshot = engine.step(60.0)

        levels.append(vessel.level)

        assert snapshot.solver["converged"] is True
        assert 0.0 <= vessel.level <= 1.0

        for node in snapshot.nodes.values():
            assert math.isfinite(node["pressure"])
            assert node["pressure"] > 0.0

    # Monotone all the way down — no overshoot, no ringing at the bottom.
    assert levels == sorted(levels, reverse=True)
    assert vessel.level == pytest.approx(0.0)
    assert plant.nodes["N-101"].pressure == pytest.approx(50.0)
    assert engine.snapshot().streams["B-P-101"]["flow"] == pytest.approx(
        FEED_FLOW,
        rel=1e-6,
    )


# --- R3: the exchange is net signed flow at the attachment node -----------


def test_the_flows_are_the_net_exchange_at_each_attachment():
    plant, engine = coupled(level=0.5)
    vessel = plant.devices["V-101"]

    assert vessel.inlet_flow == pytest.approx(FEED_FLOW)
    assert vessel.outlet_flow == pytest.approx(draw_flow(0.5))


def test_two_branches_at_one_attachment_node_are_summed():
    config = coupled_config(level=0.5)
    config["nodes"].append(
        {"id": "N-103", "boundary": True, "pressure": 110.0, "domain": "liquid_out"},
    )
    config["equipment"].insert(
        2,
        {
            "tag": "P-102",
            "type": "pump",
            "node_in": "N-101",
            "node_out": "N-103",
            "design": dict(RUNNING),
        },
    )

    plant = load_plant(config)
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]

    assert len(plant.topologies["liquid_out"].branches_from("N-101")) == 2
    assert vessel.outlet_flow == pytest.approx(2.0 * draw_flow(0.5))


class CrossNamedVessel(Vessel):
    """A vessel whose port names say the opposite of their directions.

    Nothing in a plant should be built this way. It exists so that a sign
    read off a port's name instead of its direction fails here rather than
    in a plant where the two happen to agree.
    """

    def __init__(self, tag: str = "V-101") -> None:
        super().__init__(tag)

        self.ports = {}

        self.add_port("outlet", INLET)
        self.add_port("inlet", OUTLET)


def test_the_sign_comes_from_port_direction_not_port_name():
    config = coupled_config(level=0.5)
    config["equipment"][2]["ports"] = {"outlet": "N-202", "inlet": "N-101"}

    plant = load_plant(
        config,
        device_types={"pump": CentrifugalPump, "vessel": CrossNamedVessel},
    )
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]

    # The port named "outlet" is an INLET, so it fills the vessel; the head
    # went to the node the OUTLET-direction port attaches to.
    assert vessel.inlet_flow == pytest.approx(FEED_FLOW)
    assert vessel.outlet_flow == pytest.approx(draw_flow(0.5))
    assert plant.nodes["N-101"].pressure == pytest.approx(60.0)


# --- R4: the vessel reads no plant state ----------------------------------


def test_the_vessel_module_imports_nothing_from_the_plant_or_the_engine():
    """Structural, not conventional: the coupling reaches into the graph, and
    the device it couples cannot reach back.
    """
    tree = ast.parse((ROOT / "app" / "equipment" / "vessel.py").read_text())

    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not [
        module
        for module in imported
        if module.startswith(("app.plant", "app.engine"))
    ]


def test_a_coupled_vessel_is_a_function_of_its_own_flows_alone():
    """Replay the flows the engine wrote into a standalone Vessel wired to
    nothing. Same level, step for step — so no plant state reached it by any
    route other than those two attributes.
    """
    plant, engine = coupled(level=0.8)
    coupled_vessel = plant.devices["V-101"]

    standalone = Vessel()
    standalone.capacity = CAPACITY
    standalone.level = 0.8

    for _ in range(50):
        standalone.inlet_flow = coupled_vessel.inlet_flow
        standalone.outlet_flow = coupled_vessel.outlet_flow

        standalone.integrate(30.0)
        engine.step(30.0)

        assert standalone.level == pytest.approx(coupled_vessel.level)


# --- the step order and its one-step lag ----------------------------------


def test_the_level_advances_on_last_steps_flows():
    """Explicit Euler with the lag on the flows. The level a step publishes
    was integrated from the flows solved before it, which are not the flows
    that step went on to publish.
    """
    plant, engine = coupled(level=0.5)
    vessel = plant.devices["V-101"]

    net_before = FEED_FLOW - draw_flow(0.5)

    snapshot = engine.step(60.0)

    assert vessel.level == pytest.approx(0.5 + net_before / CAPACITY)
    # The flow this same step published is not the one that moved the level.
    assert snapshot.streams["B-P-101"]["flow"] < draw_flow(0.5)
    assert snapshot.streams["B-P-101"]["flow"] == pytest.approx(
        draw_flow(vessel.level),
    )


def test_a_paused_engine_moves_nothing():
    """Every step of a paused engine reproduces the one before it, at any
    dt. The baseline is taken from a paused step rather than from snapshot()
    because the solver reports its own effort honestly: the first solve after
    a level change needs iterations and the ones after it need none, which is
    a difference in the diagnostic, not in the plant.
    """
    plant, engine = coupled(level=0.5)
    engine.step(1.0)
    engine.stop()

    before = engine.step(1.0).as_dict()

    assert engine.step(1.0).as_dict() == before
    assert engine.step(3600.0).as_dict() == before
    assert before["solver"]["converged"] is True


def test_two_identical_runs_are_bit_identical():
    def run():
        plant, engine = coupled(level=0.7)

        return [engine.step(30.0).as_dict() for _ in range(40)]

    assert run() == run()


# --- R2: the round trip a live head would otherwise break -----------------


def test_a_stepped_plant_round_trips_to_its_configured_pressures():
    config = coupled_config(level=0.5)
    plant = load_plant(copy.deepcopy(config))
    engine = Engine.from_plant(plant)

    for _ in range(20):
        engine.step(60.0)

    assert plant.nodes["N-101"].pressure != pytest.approx(50.0)
    assert plant.to_config()["nodes"] == config["nodes"]


# --- partial non-convergence ----------------------------------------------


def test_a_domain_that_did_not_converge_writes_no_vessel_flow():
    plant, engine = coupled(level=0.5)
    vessel = plant.devices["V-101"]

    held_inlet = vessel.inlet_flow

    engine.solvers["liquid_feed"] = NetworkSolver(
        plant.topologies["liquid_feed"],
        max_iterations=1,
    )

    for branch in plant.topologies["liquid_feed"].branches.values():
        branch.set_flow(0.0)

    snapshot = engine.step(60.0)

    assert engine.solver_results["liquid_feed"].converged is False
    assert engine.solver_results["liquid_out"].converged is True
    # The failed domain's flow is untouched, not the zero its branches hold.
    assert vessel.inlet_flow == pytest.approx(held_inlet)
    # The domain that did converge wrote its own.
    assert vessel.outlet_flow == pytest.approx(draw_flow(vessel.level))
    assert snapshot.solver["converged"] is False


# --- units, and the attachments that are refused or left alone ------------


def gas_side_config():
    """The ADR's own figure: V-101 between a liquid domain and a gas one."""
    return {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 60.0, "domain": "liquid"},
            {"id": "N-102", "boundary": True, "pressure": 120.0, "domain": "liquid"},
            {"id": "N-201", "boundary": True, "pressure": 100.0, "domain": "gas"},
            {"id": "N-202", "boundary": True, "pressure": 400.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": dict(RUNNING),
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-201",
                "node_out": "N-202",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-102", "outlet": "N-201"},
                "paths": [],
                "design": {"head_at_full": HEAD_AT_FULL},
            },
        ],
    }


def test_a_gas_attachment_is_left_alone_rather_than_read_as_gpm():
    """SCFM must not reach a GPM attribute, and a liquid head must not reach
    a gas boundary. The gas side is T5-3's; until then this port is simply
    not coupled.
    """
    plant = load_plant(gas_side_config())
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]

    engine.step(60.0)

    assert vessel.outlet_flow == pytest.approx(0.0)
    assert plant.nodes["N-201"].pressure == pytest.approx(100.0)
    # The liquid side is coupled normally.
    assert vessel.inlet_flow == pytest.approx(FEED_FLOW)


def test_only_the_confirmed_liquid_port_becomes_an_attachment():
    plant = load_plant(gas_side_config())

    coupling = build_couplings(plant.devices.values(), plant.topologies)[0]

    assert [attachment.port.name for attachment in coupling.attachments] == ["inlet"]
    assert coupling.attachments[0].domain == "liquid"


def test_a_node_whose_branches_disagree_on_flow_unit_is_refused():
    config = {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 60.0},
            {"id": "N-102", "boundary": True, "pressure": 120.0},
            {"id": "N-103", "boundary": True, "pressure": 400.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": dict(RUNNING),
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-101",
                "node_out": "N-103",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-102", "outlet": "N-101"},
                "paths": [],
                "design": {},
            },
        ],
    }

    plant = load_plant(config)

    with pytest.raises(ValueError, match="disagree on flow unit"):
        Engine.from_plant(plant)


class UnclassifiedDevice(Equipment):
    """A device model with no entry in FLOW_UNITS — a control valve before
    T7-1 decides what service it is on."""

    def __init__(self, tag: str = "FV-101") -> None:
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

    def integrate(self, dt: float) -> None:
        pass

    def characteristic(self, flow: float) -> float:
        return -0.0001 * signed_square(flow)

    def get_state(self):
        return {}


def test_a_device_with_no_declared_flow_unit_is_refused_at_construction():
    config = {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 60.0},
            {"id": "N-102", "boundary": True, "pressure": 50.0},
        ],
        "equipment": [
            {
                "tag": "FV-101",
                "type": "control_valve",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-102", "outlet": "N-101"},
                "paths": [],
                "design": {},
            },
        ],
    }

    plant = load_plant(
        config,
        device_types={"control_valve": UnclassifiedDevice, "vessel": Vessel},
    )

    with pytest.raises(ValueError, match="flow unit is not declared"):
        Engine.from_plant(plant)


def test_an_attachment_with_no_branches_to_read_is_not_coupled():
    """A domain may hold nodes and no branches (ADR 0001, A8). With nothing
    to read a unit from, the port is left uncoupled rather than assumed
    liquid — which is what keeps a gas vent with no compressor yet from
    quietly taking a liquid head.
    """
    config = coupled_config(level=0.5)
    config["nodes"].append(
        {"id": "N-301", "boundary": True, "pressure": 30.0, "domain": "vent"},
    )
    config["equipment"][2]["ports"] = {"inlet": "N-202", "outlet": "N-301"}

    plant = load_plant(config)
    engine = Engine.from_plant(plant)

    engine.step(60.0)

    assert plant.nodes["N-301"].pressure == pytest.approx(30.0)
    assert plant.devices["V-101"].outlet_flow == pytest.approx(0.0)


def test_a_liquid_attachment_on_an_internal_node_is_refused():
    """ADR 0001 A7, deferred to this task: a coupling device attaches to a
    boundary of the domain it terminates. An internal node's pressure
    belongs to the solver.
    """
    config = {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 60.0},
            {"id": "N-150", "boundary": False, "pressure": 90.0},
            {"id": "N-102", "boundary": True, "pressure": 120.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-101",
                "node_out": "N-150",
                "design": dict(RUNNING),
            },
            {
                "tag": "P-102",
                "type": "pump",
                "node_in": "N-150",
                "node_out": "N-102",
                "design": dict(RUNNING),
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-102", "outlet": "N-150"},
                "paths": [],
                "design": {},
            },
        ],
    }

    plant = load_plant(config)

    with pytest.raises(ValueError, match="which is internal"):
        Engine.from_plant(plant)


def test_the_flow_unit_table_covers_every_device_a_branch_can_hold():
    """A new device model added to the plant without a unit would otherwise
    only fail when someone attached a vessel next to it.
    """
    assert FLOW_UNITS[CentrifugalPump] == GPM
    assert set(FLOW_UNITS) == {CentrifugalPump, GasCompressor}
