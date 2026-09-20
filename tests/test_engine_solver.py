"""
The Engine driving the network solver — T4-4, Checkpoint B.

test_engine.py covers the Engine as an integrator; this file covers what
T4-4 added: a step that integrates, solves the plant and publishes the
result. The reference plants are the same two in test_reference_plants.py,
solved there standalone and here through the Engine, so a difference
between the two is a wiring fault rather than a solver fault.
"""

import copy
import math
from pathlib import Path

import pytest

from app.engine.engine import Engine
from app.engine.network import NetworkSolver
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant, load_plant_file

PLANTS = Path(__file__).resolve().parent.parent / "config" / "plants"

# Two identical devices in series between boundaries, as in
# test_reference_plants.py: the internal node lands on the mean of the two
# boundary pressures and each branch carries the same flow.
FIXTURES = {
    "liquid_transfer": {
        "tags": ("P-101", "P-102"),
        "internal": "N-102",
        "flow": 816.4965809277261,
        "pressure": 115.00,
        "command": "set_speed_target",
    },
    "gas_compression": {
        "tags": ("K-101", "K-102"),
        "internal": "N-202",
        "flow": 70.71067811865476,
        "pressure": 270.00,
        "command": "set_load_target",
    },
}

MIXED_DOMAIN_PLANT = {
    "nodes": [
        {"id": "N-101", "boundary": True, "pressure": 50.0, "domain": "liquid"},
        {"id": "N-102", "boundary": True, "pressure": 110.0, "domain": "liquid"},
        {"id": "N-201", "boundary": True, "pressure": 60.0, "domain": "gas"},
        {"id": "N-202", "boundary": True, "pressure": 260.0, "domain": "gas"},
    ],
    "equipment": [
        {
            "tag": "P-101",
            "type": "pump",
            "node_in": "N-101",
            "node_out": "N-102",
            "design": {"speed": 1.0},
        },
        {
            "tag": "K-101",
            "type": "compressor",
            "node_in": "N-201",
            "node_out": "N-202",
            "design": {"load": 1.0},
        },
    ],
}


def reference(name):
    return load_plant_file(PLANTS / f"{name}.yaml")


def commanded(name):
    """The reference plant with both machines commanded to the design point
    they were loaded at, so integrating does not ramp them off it.
    """
    plant = reference(name)
    spec = FIXTURES[name]

    for tag in spec["tags"]:
        device = plant.devices[tag]
        getattr(device, spec["command"])(1.0)
        device.start()

    return plant


def only_flow(snapshot):
    flows = {stream["flow"] for stream in snapshot.streams.values()}

    assert len(flows) == 1, f"branches disagree on flow: {flows}"

    return flows.pop()


@pytest.mark.parametrize("name", FIXTURES)
def test_engine_publishes_the_closed_form_solution(name):
    engine = Engine.from_plant(reference(name))
    spec = FIXTURES[name]

    snapshot = engine.snapshot()

    assert snapshot.solver["converged"] is True
    assert only_flow(snapshot) == pytest.approx(spec["flow"])
    assert snapshot.nodes[spec["internal"]]["pressure"] == pytest.approx(
        spec["pressure"],
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_a_step_holds_the_design_point_it_was_commanded_to(name):
    engine = Engine.from_plant(commanded(name))
    spec = FIXTURES[name]

    snapshot = engine.step(1.0)

    assert snapshot.sim_time == pytest.approx(1.0)
    assert snapshot.solver["converged"] is True
    assert only_flow(snapshot) == pytest.approx(spec["flow"])
    assert snapshot.nodes[spec["internal"]]["pressure"] == pytest.approx(
        spec["pressure"],
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_the_solve_sees_the_slow_state_the_same_step_integrated(name):
    """Integrate then solve, not the other way round: a machine ramping down
    must show less flow on the very step its load fell, not one step later.
    """
    engine = Engine.from_plant(commanded(name))
    spec = FIXTURES[name]

    before = only_flow(engine.step(1.0))

    for tag in spec["tags"]:
        engine.equipment[tag].stop()

    after = only_flow(engine.step(1.0))

    assert after < before


@pytest.mark.parametrize("name", FIXTURES)
def test_two_identical_runs_are_bit_identical(name):
    def run():
        engine = Engine.from_plant(commanded(name))
        states = []

        for step_num in range(10):
            if step_num == 5:
                for tag in FIXTURES[name]["tags"]:
                    engine.equipment[tag].stop()

            states.append(engine.step(1.0).as_dict())

        return states

    assert run() == run()


def test_a_coupling_device_outside_the_topology_is_still_integrated():
    """A Vessel sits in Plant.devices and in no Topology (ADR 0001, 12.6).
    Building the Engine from Topology.devices would silently never integrate
    it, which is the bug this test exists to catch.
    """
    plant = reference("liquid_transfer")
    vessel = Vessel()
    plant.devices[vessel.tag] = vessel

    assert vessel.tag not in plant.topology.devices

    engine = Engine.from_plant(plant)
    vessel.inlet_flow = 120.0

    snapshot = engine.step(60.0)

    assert vessel.level == pytest.approx(0.62)
    assert snapshot.equipment[vessel.tag]["level"] == pytest.approx(0.62)


def test_a_paused_engine_moves_nothing():
    engine = Engine.from_plant(commanded("liquid_transfer"))
    engine.step(1.0)
    engine.stop()

    before = engine.snapshot().as_dict()
    after = engine.step(1.0).as_dict()

    assert after == before


def stalled(engine, plant):
    """An engine whose next solve cannot converge.

    One Newton iteration from a plant knocked far off its solution is the
    cheapest honest way to hit the iteration cap: nothing about the solver
    or the devices is faked.
    """
    engine.solver = NetworkSolver(plant.topology, max_iterations=1)

    for branch in plant.topology.branches.values():
        branch.set_flow(0.0)

    for node in plant.topology.internal_nodes.values():
        node.set_pressure(50.0)

    return engine


def test_a_solve_that_does_not_converge_is_reported_not_raised():
    plant = commanded("liquid_transfer")
    engine = stalled(Engine.from_plant(plant), plant)

    snapshot = engine.step(1.0)

    assert snapshot.solver["converged"] is False
    assert snapshot.solver["iterations"] == 1
    assert snapshot.solver["residual"] > 0.0


def test_a_failed_solve_leaves_the_plant_exactly_as_it_was():
    plant = commanded("liquid_transfer")
    engine = stalled(Engine.from_plant(plant), plant)

    held = engine.snapshot().as_dict()
    snapshot = engine.step(1.0)

    assert snapshot.solver["converged"] is False
    assert snapshot.as_dict()["nodes"] == held["nodes"]
    assert snapshot.as_dict()["streams"] == held["streams"]
    # Time and slow state still advanced: only the algebraic solve failed.
    assert snapshot.sim_time == pytest.approx(1.0)


def test_a_multi_domain_plant_is_refused_at_wiring_time():
    plant = load_plant(copy.deepcopy(MIXED_DOMAIN_PLANT))

    assert len(plant.topologies) == 2

    with pytest.raises(ValueError, match="spans 2 flow domains"):
        Engine.from_plant(plant)


def test_an_engine_with_no_topology_integrates_and_reports_the_placeholder():
    """The unconnected form the Engine has had since T2-3 still works: it
    integrates, and says plainly that nothing solved.
    """
    vessel = Vessel()
    vessel.inlet_flow = 60.0

    engine = Engine([vessel])
    snapshot = engine.step(60.0)

    assert vessel.level == pytest.approx(0.56)
    assert snapshot.nodes == {}
    assert snapshot.streams == {}
    assert snapshot.solver["converged"] is True
    assert snapshot.solver["iterations"] == 0


def test_the_reference_flow_is_the_closed_form_and_not_a_recorded_number():
    """Guards the constants above: they are the algebra, not a paste of
    whatever the solver happened to print.
    """
    plant = reference("liquid_transfer")
    resistance = plant.topology.devices["P-101"].pump_resistance

    assert FIXTURES["liquid_transfer"]["flow"] == pytest.approx(
        math.sqrt((2.0 * 75.0 - (180.0 - 50.0)) / (2.0 * resistance)),
    )
