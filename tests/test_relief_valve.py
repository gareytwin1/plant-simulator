"""
Relief valve - T7-5.

Four kinds of claim, kept apart:

  * the device on its own - the hysteresis band, the closed-valve leak, the
    curve shape, all against the closed form and with no vessel at all;
  * a relief valve and a vessel together, hand-driven - `vessel.gas_outlet_
    flow` computed from the closed form rather than a real solve, and
    `relief.inlet_pressure` copied from `vessel.pressure` by the test. This
    is the same bargain `tests/test_gas_inventory.py` already makes for a
    vessel on its own: a caller writes the plain attributes a coupling would
    otherwise write. It pins the exact closed-form relationship, fast and
    without solver noise;
  * the same pairing through a real `Engine.from_plant()`, with nothing
    hand-driven at all - `app/engine/coupling.py`'s `FLOW_UNITS` lists this
    device as unit-neutral (alongside `ControlValve`), so it no longer gets
    refused sharing a vessel's boundary node, and `VesselCoupling.
    write_boundary_pressures()` now senses `inlet_pressure` for it the same
    step it writes that node's pressure (see `relief.py`'s module
    docstring). Sensing, the lift decision, the branch flow and the vessel's
    `gas_outlet_flow` are all the engine's own doing;
  * that relief and process withdrawals share one balance - `Vessel` has no
    relief-specific attribute to point at, so the claim is demonstrated by
    showing two flows of the same magnitude move a vessel identically
    whichever one produced the number.

The discharge header is a fixed low pressure, so a lifted valve's flow has a
closed form exactly like the vent valve in test_gas_inventory.py:

    flow = effective_capacity * sqrt(vessel_pressure - header_pressure)

which is `characteristic`'s own drop equation solved for flow against a known
boundary on each side - and what the real solver in the third group is
expected to land on, not merely approximate.
"""

import math

import pytest

from app import config
from app.engine.engine import Engine
from app.equipment.base import signed_square
from app.equipment.relief import CLOSED_LEAK_FRACTION, ReliefValve
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant


HEADER_PRESSURE = config.STANDARD_PRESSURE


def _flow_to(vessel, relief, header_pressure=HEADER_PRESSURE):
    return relief.effective_capacity * math.sqrt(
        max(vessel.pressure - header_pressure, 0.0),
    )


def _step(vessel, relief, dt, header_pressure=HEADER_PRESSURE):
    """One hand-driven coupling step: sense, decide, flow, integrate."""
    relief.inlet_pressure = vessel.pressure
    relief.integrate(dt)

    flow = _flow_to(vessel, relief, header_pressure)
    vessel.gas_outlet_flow = flow
    vessel.integrate(dt)

    return flow


# --- the device on its own ------------------------------------------------


def test_a_fresh_valve_is_closed():
    relief = ReliefValve()

    assert relief.lifted is False


def test_lifts_at_set_pressure():
    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0

    relief.inlet_pressure = 259.999
    relief.integrate(1.0)
    assert relief.lifted is False

    relief.inlet_pressure = 260.0
    relief.integrate(1.0)
    assert relief.lifted is True


def test_recloses_at_the_reseat_pressure():
    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0

    relief.inlet_pressure = 260.0
    relief.integrate(1.0)
    assert relief.lifted is True

    relief.inlet_pressure = 250.001
    relief.integrate(1.0)
    assert relief.lifted is True

    relief.inlet_pressure = 250.0
    relief.integrate(1.0)
    assert relief.lifted is False


def test_holds_state_inside_the_hysteresis_band():
    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0

    relief.inlet_pressure = 260.0
    relief.integrate(1.0)
    assert relief.lifted is True

    for pressure in (259.0, 255.0, 251.0):
        relief.inlet_pressure = pressure
        relief.integrate(1.0)
        assert relief.lifted is True

    relief.inlet_pressure = 250.0
    relief.integrate(1.0)
    assert relief.lifted is False

    for pressure in (251.0, 255.0, 259.0):
        relief.inlet_pressure = pressure
        relief.integrate(1.0)
        assert relief.lifted is False


def test_integrating_zero_time_still_evaluates_the_threshold():
    """dt is irrelevant to the decision - it is not a rate. Calling
    integrate(0) twice with an unchanged inlet_pressure is idempotent, which
    is the no-op the contract asks for; it is the *signal*, not the clock,
    that moves this device.
    """
    relief = ReliefValve()
    relief.inlet_pressure = relief.set_pressure

    relief.integrate(0.0)
    assert relief.lifted is True

    relief.integrate(0.0)
    assert relief.lifted is True


def test_a_closed_valve_passes_far_less_flow_than_a_lifted_one():
    relief = ReliefValve()
    relief.capacity = 50.0

    closed_drop = relief.characteristic(10.0)

    relief.lifted = True
    lifted_drop = relief.characteristic(10.0)

    assert closed_drop < lifted_drop < 0.0


def test_the_lifted_curve_matches_the_textbook_drop():
    relief = ReliefValve()
    relief.capacity = 50.0
    relief.lifted = True

    assert relief.characteristic(100.0) == pytest.approx(
        -signed_square(100.0) / 50.0 ** 2,
    )


def test_the_closed_leak_is_a_fixed_fraction_of_capacity():
    relief = ReliefValve()
    relief.capacity = 50.0

    assert relief.effective_capacity == pytest.approx(50.0 * CLOSED_LEAK_FRACTION)

    relief.lifted = True
    assert relief.effective_capacity == pytest.approx(50.0)


def test_the_curve_is_never_zero_at_a_nonzero_flow_when_closed():
    relief = ReliefValve()

    assert relief.characteristic(1.0) < 0.0
    assert math.isfinite(relief.characteristic(1.0))


def test_set_pressure_must_be_positive():
    relief = ReliefValve()

    with pytest.raises(ValueError):
        relief.set_pressure = 0.0


def test_capacity_must_be_positive():
    relief = ReliefValve()

    with pytest.raises(ValueError):
        relief.capacity = 0.0


def test_blowdown_must_be_less_than_set_pressure():
    relief = ReliefValve()
    relief.set_pressure = 260.0

    with pytest.raises(ValueError):
        relief.blowdown = 260.0

    with pytest.raises(ValueError):
        relief.blowdown = 300.0


def test_lowering_set_pressure_below_the_existing_blowdown_is_refused():
    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 50.0

    with pytest.raises(ValueError):
        relief.set_pressure = 40.0


def test_the_reseat_pressure_is_set_pressure_minus_blowdown():
    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 15.0

    assert relief.reseat_pressure == pytest.approx(245.0)


def test_get_state_reports_the_lift_decision_and_the_design_values():
    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0
    relief.inlet_pressure = 260.0
    relief.integrate(1.0)

    state = relief.get_state()

    assert state["lifted"] is True
    assert state["inlet_pressure"] == pytest.approx(260.0)
    assert state["set_pressure"] == pytest.approx(260.0)
    assert state["reseat_pressure"] == pytest.approx(250.0)
    assert state["effective_capacity"] == pytest.approx(relief.capacity)


# --- a relief valve and a vessel, hand-driven ------------------------------


def _lifted_vessel_and_relief():
    vessel = Vessel()
    vessel.gas_volume = 100.0
    vessel.initial_pressure = 265.0
    vessel.activate_gas()

    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0
    relief.capacity = 50.0

    return vessel, relief


def test_a_relief_valve_stays_shut_below_set_pressure():
    vessel = Vessel()
    vessel.gas_volume = 100.0
    vessel.initial_pressure = 200.0
    vessel.activate_gas()

    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0
    relief.capacity = 50.0

    before = vessel.pressure

    for _ in range(60):
        _step(vessel, relief, 1.0)

    assert relief.lifted is False
    # Only the closed leak has passed, and it is small: barely off the seed.
    assert vessel.pressure == pytest.approx(before, abs=0.5)


def test_a_lifted_relief_valve_depressures_the_vessel_at_the_expected_rate():
    vessel, relief = _lifted_vessel_and_relief()

    # Sync the lift decision without advancing time, then take one real step.
    relief.inlet_pressure = vessel.pressure
    relief.integrate(0.0)
    assert relief.lifted is True

    outflow = _flow_to(vessel, relief)
    assert outflow == pytest.approx(
        relief.capacity * math.sqrt(265.0 - HEADER_PRESSURE),
    )

    before = vessel.pressure
    vessel.gas_outlet_flow = outflow
    vessel.integrate(10.0)

    expected_drop = (
        config.STANDARD_PRESSURE * outflow * 10.0 / 60.0 / vessel.gas_volume
    )
    assert before - vessel.pressure == pytest.approx(expected_drop)


def test_relief_lifts_and_then_recloses_as_the_vessel_depressures():
    vessel, relief = _lifted_vessel_and_relief()

    assert vessel.pressure > relief.set_pressure

    seen_lifted = False
    previous = vessel.pressure

    for _ in range(600):
        _step(vessel, relief, 1.0)

        if relief.lifted:
            seen_lifted = True
            assert vessel.pressure <= previous + 1e-9

        previous = vessel.pressure

    assert seen_lifted
    assert relief.lifted is False
    assert vessel.pressure <= relief.reseat_pressure


def test_once_reclosed_the_vessel_stops_falling_in_any_meaningful_way():
    vessel, relief = _lifted_vessel_and_relief()

    for _ in range(600):
        _step(vessel, relief, 1.0)

    assert relief.lifted is False
    settled = vessel.pressure

    for _ in range(120):
        _step(vessel, relief, 1.0)

    # What is left is the closed leak, not the relief flow: a small,
    # bounded further drop rather than a continued relief-rate fall.
    assert settled - vessel.pressure < 1.0


# --- relief and process withdrawals share one balance ----------------------


def test_relief_flow_moves_the_vessel_exactly_as_an_equal_process_flow_would():
    relief_driven = Vessel()
    relief_driven.gas_volume = 100.0
    relief_driven.initial_pressure = 265.0
    relief_driven.activate_gas()

    relief = ReliefValve()
    relief.set_pressure = 260.0
    relief.blowdown = 10.0
    relief.capacity = 50.0

    outflow = _step(relief_driven, relief, 10.0)

    process_driven = Vessel()
    process_driven.gas_volume = 100.0
    process_driven.initial_pressure = 265.0
    process_driven.activate_gas()
    process_driven.gas_outlet_flow = outflow
    process_driven.integrate(10.0)

    assert relief_driven.pressure == pytest.approx(process_driven.pressure)


# --- wiring into a real plant ----------------------------------------------


def _standalone_config():
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 300.0},
            {"id": "N-02", "boundary": True, "pressure": 14.696},
        ],
        "equipment": [
            {
                "tag": "PSV-101",
                "type": "relief_valve",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {"set_pressure": 260.0, "blowdown": 10.0, "capacity": 50.0},
            },
        ],
    }


def test_the_loader_recognises_the_relief_valve_type():
    plant = load_plant(_standalone_config())
    engine = Engine.from_plant(plant)

    relief = plant.devices["PSV-101"]

    assert relief.set_pressure == pytest.approx(260.0)
    assert engine.equipment["PSV-101"] is relief


def _vessel_and_relief_config():
    """The vessel's `inlet` is a dead end - its own single-node domain with no
    branch on it, exactly the idiom `tests/test_gas_inventory.py` uses for a
    blocked side - so the only real path is `outlet` -> N-A -> the relief
    branch -> N-B, and the vessel's `gas_outlet_flow` is unambiguously the
    relief flow.
    """
    return {
        "nodes": [
            {"id": "N-DEAD", "boundary": True, "pressure": 265.0, "domain": "relief_dead_end"},
            {"id": "N-A", "boundary": True, "pressure": 265.0, "domain": "relief"},
            {"id": "N-B", "boundary": True, "pressure": 14.696, "domain": "relief"},
        ],
        "equipment": [
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "inlet": {"node": "N-DEAD", "phase": "vapor", "purpose": "process"},
                    "outlet": {"node": "N-A", "phase": "vapor", "purpose": "relief"},
                },
                "paths": [],
                "design": {"gas_volume": 100.0, "initial_pressure": 265.0},
            },
            {
                "tag": "PSV-101",
                "type": "relief_valve",
                "node_in": "N-A",
                "node_out": "N-B",
                "design": {"set_pressure": 260.0, "blowdown": 10.0, "capacity": 50.0},
            },
        ],
    }


def test_a_relief_valve_beside_a_vessel_no_longer_gets_refused():
    """`FLOW_UNITS` now lists `ReliefValve` as unit-neutral, so building the
    engine - which used to raise "flow unit is not declared" - succeeds.
    """
    plant = load_plant(_vessel_and_relief_config())
    engine = Engine.from_plant(plant)

    assert "PSV-101" in engine.equipment
    assert "V-101" in engine.equipment


def test_the_engine_senses_the_vessel_pressure_without_being_told():
    """`VesselCoupling.write_boundary_pressures()` writes `inlet_pressure`
    onto a relief valve whose inlet faces one of its gas nodes - the
    constructor's own `_couple()` already ran, before any test lifts a
    finger.
    """
    plant = load_plant(_vessel_and_relief_config())
    Engine.from_plant(plant)

    vessel = plant.devices["V-101"]
    relief = plant.devices["PSV-101"]

    assert relief.inlet_pressure == pytest.approx(vessel.pressure)


def test_a_lifted_relief_valve_solves_the_expected_flow_through_a_real_engine():
    plant = load_plant(_vessel_and_relief_config())
    engine = Engine.from_plant(plant)

    vessel = plant.devices["V-101"]
    relief = plant.devices["PSV-101"]

    # No hand-driving anywhere: sensing, the lift decision and the branch
    # flow are all the engine's own doing now.
    engine.step(0.0)

    assert relief.lifted is True
    assert vessel.gas_outlet_flow == pytest.approx(
        relief.capacity * math.sqrt(265.0 - HEADER_PRESSURE),
        rel=1e-4,
    )


def test_relief_lifts_and_recloses_through_a_real_engines_own_solver():
    plant = load_plant(_vessel_and_relief_config())
    engine = Engine.from_plant(plant)

    vessel = plant.devices["V-101"]
    relief = plant.devices["PSV-101"]

    assert vessel.pressure > relief.set_pressure

    seen_lifted = False
    previous = vessel.pressure

    for _ in range(600):
        engine.step(1.0)

        if relief.lifted:
            seen_lifted = True
            assert vessel.pressure <= previous + 1e-9

        previous = vessel.pressure

    assert seen_lifted
    assert relief.lifted is False
    assert vessel.pressure <= relief.reseat_pressure
