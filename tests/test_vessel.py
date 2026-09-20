import json

import pytest

from app.equipment.base import INLET, OUTLET
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant


def make_vessel(level=0.5, capacity=1000.0):
    vessel = Vessel()
    vessel.capacity = capacity
    vessel.level = level

    return vessel


def test_level_rises_when_inlet_exceeds_outlet():
    vessel = make_vessel(level=0.5)
    vessel.inlet_flow = 150.0
    vessel.outlet_flow = 90.0

    vessel.integrate(10.0)

    # 60 GPM net for 10 s is 10 gal, or 0.01 of a 1000 gal vessel.
    assert vessel.level == pytest.approx(0.51)


def test_level_falls_when_outlet_exceeds_inlet():
    vessel = make_vessel(level=0.5)
    vessel.inlet_flow = 30.0
    vessel.outlet_flow = 90.0

    vessel.integrate(10.0)

    assert vessel.level == pytest.approx(0.49)


def test_level_is_unchanged_with_balanced_flow():
    vessel = make_vessel(level=0.4)
    vessel.inlet_flow = 120.0
    vessel.outlet_flow = 120.0

    vessel.integrate(60.0)

    assert vessel.level == pytest.approx(0.4)


def test_level_clamps_at_full_and_stays():
    vessel = make_vessel(level=0.99)
    vessel.inlet_flow = 500.0

    for _ in range(1000):
        vessel.integrate(1.0)

        assert vessel.level <= 1.0

    assert vessel.level == pytest.approx(1.0)


def test_level_clamps_at_empty_and_stays():
    vessel = make_vessel(level=0.01)
    vessel.outlet_flow = 500.0

    for _ in range(1000):
        vessel.integrate(1.0)

        assert vessel.level >= 0.0

    assert vessel.level == pytest.approx(0.0)


def test_level_leaves_a_clamp_immediately_when_flow_reverses():
    vessel = make_vessel(level=1.0)
    vessel.inlet_flow = 500.0

    for _ in range(100):
        vessel.integrate(1.0)

    vessel.inlet_flow = 0.0
    vessel.outlet_flow = 60.0

    vessel.integrate(10.0)

    # No windup: the first step off the clamp moves by exactly one step's worth.
    assert vessel.level == pytest.approx(1.0 - 0.01)


def test_level_is_stable_over_a_long_alternating_run():
    vessel = make_vessel(level=0.5)

    for step in range(20000):
        vessel.inlet_flow = 400.0 if (step // 500) % 2 == 0 else 0.0
        vessel.outlet_flow = 100.0

        vessel.integrate(0.5)

        assert 0.0 <= vessel.level <= 1.0


def test_residence_time_for_a_known_geometry():
    vessel = make_vessel(level=0.5, capacity=2000.0)
    vessel.outlet_flow = 200.0

    # 1000 gal at 200 GPM is 5 minutes.
    assert vessel.volume == pytest.approx(1000.0)
    assert vessel.residence_time == pytest.approx(300.0)


def test_residence_time_is_none_without_outflow():
    vessel = make_vessel()

    assert vessel.residence_time is None


def test_integrate_zero_is_a_no_op():
    vessel = make_vessel(level=0.5)
    vessel.inlet_flow = 300.0
    vessel.outlet_flow = 10.0

    before = vessel.get_state()

    vessel.integrate(0.0)

    assert vessel.get_state() == before


def test_reset_restores_construction_state():
    vessel = Vessel()
    fresh = vessel.get_state()

    vessel.inlet_flow = 300.0
    vessel.integrate(30.0)
    vessel.reset()

    assert vessel.get_state() == fresh
    assert vessel.level == pytest.approx(0.5)


def test_get_state_is_json_safe():
    vessel = make_vessel()
    vessel.outlet_flow = 100.0

    state = vessel.get_state()

    assert json.loads(json.dumps(state)) == state
    assert state["level"] == pytest.approx(0.5)


def test_characteristic_is_zero_at_any_flow():
    vessel = make_vessel()

    for flow in (-100.0, 0.0, 100.0):
        assert vessel.characteristic(flow) == pytest.approx(0.0)


def test_vessel_declares_one_inlet_and_one_outlet():
    vessel = Vessel()

    assert vessel.tag == "V-101"
    assert {name: port.direction for name, port in vessel.ports.items()} == {
        "inlet": INLET,
        "outlet": OUTLET,
    }


def test_vessel_wired_through_load_plant_is_a_coupling_device():
    config = {
        "nodes": [
            {"id": "L-01", "boundary": True, "pressure": 50.0, "domain": "liquid"},
            {"id": "L-02", "boundary": False, "pressure": 60.0, "domain": "liquid"},
            {"id": "G-01", "boundary": True, "pressure": 100.0, "domain": "gas"},
            {"id": "G-02", "boundary": True, "pressure": 900.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "L-01",
                "node_out": "L-02",
                "design": {},
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "G-01",
                "node_out": "G-02",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "L-02", "outlet": "G-01"},
                "paths": [],
                "design": {"capacity": 2500.0},
            },
        ],
    }

    plant = load_plant(
        config,
        device_types={
            "pump": CentrifugalPump,
            "compressor": GasCompressor,
            "vessel": Vessel,
        },
    )

    vessel = plant.devices["V-101"]

    assert isinstance(vessel, Vessel)
    assert vessel.capacity == pytest.approx(2500.0)
    assert vessel.port("inlet").node.id == "L-02"
    assert vessel.port("outlet").node.id == "G-01"

    for topology in plant.topologies.values():
        assert "V-101" not in topology.devices
