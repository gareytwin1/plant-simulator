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


# T5-2 — the head the inventory offers, the carryover flag, and the guards
# that keep a design value from reaching the coupling as nonsense.


def test_head_is_linear_in_level():
    vessel = make_vessel(level=0.0)
    vessel.head_at_full = 20.0

    assert vessel.head == pytest.approx(0.0)

    vessel.level = 0.25
    assert vessel.head == pytest.approx(5.0)

    vessel.level = 1.0
    assert vessel.head == pytest.approx(20.0)


def test_head_is_zero_not_a_pressure_when_empty():
    """An empty vessel offers no head. It does not offer 0 psia — the
    absolute base belongs to the node, not the vessel.
    """
    vessel = make_vessel(level=0.0)

    assert vessel.head == pytest.approx(0.0)


def test_carryover_fires_at_the_configured_level():
    vessel = make_vessel(level=0.5)
    vessel.carryover_level = 0.8

    assert vessel.carryover is False

    vessel.level = 0.8
    assert vessel.carryover is True

    vessel.level = 0.95
    assert vessel.carryover is True

    vessel.level = 0.79
    assert vessel.carryover is False


def test_carryover_is_reported_in_state():
    vessel = make_vessel(level=0.95)

    assert vessel.get_state()["carryover"] is True
    assert vessel.get_state()["head"] == pytest.approx(vessel.head_at_full * 0.95)


def test_capacity_must_be_positive():
    vessel = Vessel()

    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="capacity"):
            vessel.capacity = bad


def test_level_must_be_a_fraction():
    vessel = Vessel()

    for bad in (-0.001, 1.001, float("nan")):
        with pytest.raises(ValueError, match="level"):
            vessel.level = bad

    vessel.level = 0.0
    vessel.level = 1.0


def test_head_at_full_must_be_non_negative():
    vessel = Vessel()

    with pytest.raises(ValueError, match="head_at_full"):
        vessel.head_at_full = -0.1

    vessel.head_at_full = 0.0


def test_carryover_level_must_be_a_fraction():
    vessel = Vessel()

    with pytest.raises(ValueError, match="carryover_level"):
        vessel.carryover_level = 1.5


def test_head_cannot_be_set_directly():
    vessel = Vessel()

    with pytest.raises(AttributeError):
        vessel.head = 10.0


def vessel_plant_config(design):
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 50.0},
            {"id": "N-02", "boundary": True, "pressure": 110.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-02", "outlet": "N-01"},
                "paths": [],
                "design": design,
            },
        ],
    }


def test_a_vessel_loads_from_the_default_device_table():
    plant = load_plant(vessel_plant_config({"head_at_full": 18.0, "level": 0.8}))
    vessel = plant.devices["V-101"]

    assert isinstance(vessel, Vessel)
    assert vessel.head == pytest.approx(14.4)


def test_a_design_value_out_of_range_is_a_config_error_naming_the_path():
    from app.plant.loader import PlantConfigError

    with pytest.raises(PlantConfigError) as raised:
        load_plant(vessel_plant_config({"capacity": 0.0}))

    assert any(
        error.startswith("$.equipment[1].design.capacity:")
        and "must be above 0.0" in error
        for error in raised.value.errors
    )


def test_a_derived_attribute_is_refused_as_a_design_value():
    from app.plant.loader import PlantConfigError

    with pytest.raises(PlantConfigError) as raised:
        load_plant(vessel_plant_config({"head": 10.0}))

    assert any("is derived and cannot be set" in error for error in raised.value.errors)


# T5-7 — configured ports (ADR 0002, Amendment 3)


def test_reset_preserves_a_configured_port_set():
    config = {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 100.0, "domain": "liquid"},
            {"id": "N-02", "boundary": True, "pressure": 50.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "feed": {
                        "node": "N-01",
                        "phase": "liquid",
                        "purpose": "process",
                        "direction": INLET,
                    },
                    "drain": {
                        "node": "N-01",
                        "phase": "liquid",
                        "purpose": "drain",
                        "direction": OUTLET,
                    },
                    "vapor_out": {
                        "node": "N-02",
                        "phase": "vapor",
                        "purpose": "process",
                        "direction": OUTLET,
                    },
                },
                "paths": [],
                "design": {},
            },
        ],
    }

    vessel = load_plant(config).devices["V-101"]

    order_before = list(vessel.ports)
    descriptors_before = {
        name: (port.direction, port.phase, port.purpose, port.control)
        for name, port in vessel.ports.items()
    }

    vessel.level = 0.9
    vessel.reset()

    assert list(vessel.ports) == order_before
    assert {
        name: (port.direction, port.phase, port.purpose, port.control)
        for name, port in vessel.ports.items()
    } == descriptors_before
    assert vessel.level == pytest.approx(0.5)
