"""
Gas-phase pressure accumulation — T5-3.

The vessel's gas space is one lumped, isothermal ideal gas at the standard
temperature, so its pressure obeys

    dP/dt = P_std * (Q_in - Q_out) / V_gas          [psi/min, SCFM, ft^3]

Hand check, done dimensionally rather than pasted: 1 SCFM into 1 ft^3 adds
1 scf, and 1 scf in a 1 ft^3 space is P_std of pressure, so 70.7 SCFM into
100 ft^3 is 14.696 * 70.7 / 100 = 10.39 psi/min.

The reference plant is two gas domains with the vessel between them. K-101
lifts N-S (60 psia) into the vessel, whose pressure is the boundary at N-A,
and the vent valve FV-101 takes gas from the vessel's boundary N-B down to
N-X (P_std). Both nodes carry the vessel's pressure P, so the closed forms are

    feed  Q_in  = sqrt((280 - P) / 0.002)         280 = 60 + 220
    vent  Q_out = C * sqrt(P - P_std)             C = valve capacity, linear, open

The valve is the resistance that gives the vent a system curve; without one a
compressor at equal boundaries runs away. C = 20 puts the balance at
P* = (280 + 0.002 C^2 P_std) / (1 + 0.002 C^2) = 162.1 psia and 242.8 SCFM.
Linearised there, dQin/dP = -1/(2 * 0.002 * Qin) = -1.030 and
dQout/dP = C^2 / (2 * Qout) = +0.824 SCFM/psi, so the time constant of a
100 ft^3 vessel is V / (P_std * 1.853) = 3.67 min, about 220 s.
"""

import copy
import json
import math

import pytest

from app import config
from app.engine.coupling import GPM, SCFM, build_couplings
from app.engine.engine import Engine
from app.engine.network import NetworkSolver
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant
from app.plant.topology import ATMOSPHERIC_PRESSURE

P_STD = config.STANDARD_PRESSURE

SUPPLY = 60.0
SHUTOFF = 220.0
COMPRESSOR_R = 0.002
VALVE_CAPACITY = 20.0

LIQUID_STATE_KEYS = {
    "level",
    "volume",
    "head",
    "carryover",
    "inlet_flow",
    "outlet_flow",
    "residence_time",
}


def feed_flow(pressure):
    return math.sqrt((SUPPLY + SHUTOFF - pressure) / COMPRESSOR_R)


def vent_flow(pressure, capacity=VALVE_CAPACITY):
    return capacity * math.sqrt(pressure - P_STD)


def equilibrium(capacity=VALVE_CAPACITY):
    k = COMPRESSOR_R * capacity ** 2

    return (SUPPLY + SHUTOFF + k * P_STD) / (1.0 + k)


def time_constant(volume, capacity=VALVE_CAPACITY):
    """Linearised at the balance point, in seconds."""
    p = equilibrium(capacity)
    q_in = feed_flow(p)
    q_out = vent_flow(p, capacity)

    stiffness = 1.0 / (2.0 * COMPRESSOR_R * q_in) + capacity ** 2 / (2.0 * q_out)

    return volume / (P_STD * stiffness) * 60.0


COMPRESSOR = {"running": True, "load": 1.0, "load_target": 1.0}


def gas_config(volume=100.0, pressure=SUPPLY, feed=True, vent=True):
    """A blocked side is a boundary node with no branch on it, which is
    exactly a vessel port that is wired and goes nowhere. It sits alone in a
    domain of its own, because a domain must be one connected piece.
    """
    nodes = [
        {"id": "N-S", "boundary": True, "pressure": SUPPLY, "domain": "gas_feed"},
        {"id": "N-A", "boundary": True, "pressure": SUPPLY, "domain": "gas_feed"},
        {"id": "N-B", "boundary": True, "pressure": SUPPLY, "domain": "gas"},
        {"id": "N-X", "boundary": True, "pressure": P_STD, "domain": "gas"},
    ]

    if not feed:
        nodes = [node for node in nodes if node["id"] != "N-S"]
        nodes[0]["domain"] = "gas_dead_end"

    if not vent:
        nodes = [node for node in nodes if node["id"] != "N-X"]
        nodes[-1]["domain"] = "gas_dead_end"

    equipment = [
        {
            "tag": "V-101",
            "type": "vessel",
            "ports": {"inlet": "N-A", "outlet": "N-B"},
            "paths": [],
            "design": {"gas_volume": volume, "initial_pressure": pressure},
        },
    ]

    if feed:
        equipment.append(
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-S",
                "node_out": "N-A",
                "design": dict(COMPRESSOR),
            },
        )

    if vent:
        equipment.append(
            {
                "tag": "FV-101",
                "type": "control_valve",
                "node_in": "N-B",
                "node_out": "N-X",
                "design": {"capacity": VALVE_CAPACITY},
            },
        )

    return {"nodes": nodes, "equipment": equipment}


def gas_plant(**kwargs):
    plant = load_plant(gas_config(**kwargs))

    return plant, Engine.from_plant(plant)


# --- the constant and the rate law ----------------------------------------


def test_the_standard_pressure_is_one_atmosphere_in_psia():
    assert P_STD == pytest.approx(14.696)
    assert P_STD == ATMOSPHERIC_PRESSURE


def test_the_verified_hand_calculation_seventy_scfm_into_a_hundred_cubic_feet():
    vessel = Vessel()
    vessel.gas_volume = 100.0
    vessel.initial_pressure = 100.0
    vessel.activate_gas()
    vessel.gas_inlet_flow = 70.7

    vessel.integrate(60.0)

    assert vessel.pressure - 100.0 == pytest.approx(14.696 * 70.7 / 100.0)
    assert vessel.pressure - 100.0 == pytest.approx(10.3901, abs=1e-4)


def test_the_standard_volume_inventory_changes_by_exactly_the_net_scfm():
    vessel = Vessel()
    vessel.gas_volume = 37.0
    vessel.initial_pressure = 80.0
    vessel.activate_gas()
    vessel.gas_inlet_flow = 50.0
    vessel.gas_outlet_flow = 12.0

    before = vessel.gas_inventory

    vessel.integrate(30.0)

    # 38 SCFM for half a minute is 19 scf.
    assert vessel.gas_inventory - before == pytest.approx(19.0)


# --- the vessel on its own ------------------------------------------------


def test_gas_volume_must_be_finite_and_strictly_positive():
    vessel = Vessel()

    for bad in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="gas_volume"):
            vessel.gas_volume = bad

    with pytest.raises(ValueError, match="gas_volume"):
        vessel.gas_volume = "big"


def test_pressure_must_be_finite_and_strictly_positive():
    vessel = Vessel()

    for bad in (0.0, -5.0, math.inf):
        with pytest.raises(ValueError, match="initial_pressure"):
            vessel.initial_pressure = bad

        with pytest.raises(ValueError, match="pressure"):
            vessel.pressure = bad


def test_a_step_that_would_reach_zero_pressure_raises_rather_than_clamps():
    vessel = Vessel()
    vessel.gas_volume = 1.0
    vessel.initial_pressure = 10.0
    vessel.activate_gas()
    vessel.gas_outlet_flow = 1000.0

    with pytest.raises(ValueError, match="pressure"):
        vessel.integrate(60.0)


def test_the_seed_pressure_is_the_starting_pressure():
    vessel = Vessel()

    assert vessel.pressure == pytest.approx(P_STD)

    vessel.initial_pressure = 250.0

    assert vessel.pressure == pytest.approx(250.0)


def test_integrate_zero_leaves_the_gas_phase_alone():
    vessel = Vessel()
    vessel.activate_gas()
    vessel.gas_inlet_flow = 300.0
    vessel.gas_outlet_flow = 10.0

    before = vessel.get_state()

    vessel.integrate(0.0)

    assert vessel.get_state() == before


def test_reset_restores_the_gas_state():
    vessel = Vessel()
    vessel.initial_pressure = 90.0
    vessel.gas_volume = 20.0
    vessel.activate_gas()
    fresh = vessel.get_state()

    vessel.gas_inlet_flow = 300.0
    vessel.integrate(10.0)

    assert vessel.pressure != pytest.approx(90.0)

    vessel.reset()

    assert vessel.gas_inlet_flow == pytest.approx(0.0)
    assert vessel.gas_active is False
    assert vessel.pressure == pytest.approx(P_STD)  # the construction default

    vessel.activate_gas()

    assert set(vessel.get_state()) == set(fresh)


def test_a_vessel_no_coupling_has_touched_is_liquid_only():
    vessel = Vessel()
    vessel.gas_inlet_flow = 500.0

    vessel.integrate(60.0)

    assert vessel.pressure == pytest.approx(P_STD)
    assert set(vessel.get_state()) == LIQUID_STATE_KEYS


def test_the_gas_state_is_json_safe_and_in_the_stated_units():
    vessel = Vessel()
    vessel.activate_gas()
    vessel.gas_inlet_flow = 10.0

    state = vessel.get_state()

    assert set(state) - LIQUID_STATE_KEYS == {
        "pressure",
        "gas_volume",
        "gas_inventory",
        "gas_inlet_flow",
        "gas_outlet_flow",
    }
    assert json.loads(json.dumps(state)) == state


def test_the_gas_fields_can_be_configured_through_the_loader():
    plant = load_plant(gas_config(volume=42.0, pressure=75.0))
    vessel = plant.devices["V-101"]

    assert vessel.gas_volume == pytest.approx(42.0)
    assert vessel.pressure == pytest.approx(75.0)

    config_dict = gas_config(volume=-3.0)

    with pytest.raises(ValueError, match="gas_volume"):
        load_plant(config_dict)


# --- criterion 1: a blocked outlet raises pressure at the expected rate ----


def test_a_blocked_outlet_raises_pressure_at_the_expected_rate():
    plant, engine = gas_plant(volume=100.0, pressure=100.0, vent=False)
    vessel = plant.devices["V-101"]

    inflow = feed_flow(100.0)

    # The fresh engine has already solved against the seed pressure.
    assert vessel.gas_inlet_flow == pytest.approx(inflow)
    assert vessel.gas_outlet_flow == pytest.approx(0.0)

    engine.step(10.0)

    assert vessel.pressure - 100.0 == pytest.approx(P_STD * inflow * 10.0 / 60.0 / 100.0)


def test_a_blocked_outlet_settles_toward_the_compressor_shutoff():
    plant, engine = gas_plant(volume=100.0, pressure=100.0, vent=False)
    vessel = plant.devices["V-101"]

    previous = vessel.pressure

    for _ in range(3000):
        engine.step(1.0)

        # Rising the whole way except within the last hair of shutoff, where
        # the curve's square-root shape makes Euler cross it (see the
        # stability section below).
        if previous < SUPPLY + SHUTOFF - 0.01:
            assert vessel.pressure > previous

        previous = vessel.pressure

    assert vessel.pressure == pytest.approx(SUPPLY + SHUTOFF, abs=0.01)
    assert vessel.gas_inlet_flow == pytest.approx(0.0, abs=1.0)


def test_the_inlet_machine_sees_the_vessel_back_pressure():
    plant, engine = gas_plant(volume=100.0, pressure=60.0, vent=False)
    vessel = plant.devices["V-101"]

    first = vessel.gas_inlet_flow

    for _ in range(120):
        engine.step(1.0)

    assert vessel.gas_inlet_flow < first
    assert vessel.gas_inlet_flow == pytest.approx(feed_flow(vessel.pressure), rel=0.02)


# --- criterion 2: pressure falls on venting -------------------------------


def test_venting_lowers_pressure_at_the_expected_rate():
    plant, engine = gas_plant(volume=100.0, pressure=100.0, feed=False)
    vessel = plant.devices["V-101"]

    outflow = vent_flow(100.0)

    assert vessel.gas_inlet_flow == pytest.approx(0.0)
    assert vessel.gas_outlet_flow == pytest.approx(outflow)

    engine.step(10.0)

    assert 100.0 - vessel.pressure == pytest.approx(P_STD * outflow * 10.0 / 60.0 / 100.0)


def test_venting_falls_toward_the_vent_and_stays_positive():
    plant, engine = gas_plant(volume=100.0, pressure=100.0, feed=False)
    vessel = plant.devices["V-101"]

    previous = vessel.pressure

    for _ in range(3000):
        engine.step(1.0)

        assert vessel.pressure > 0.0

        if previous > P_STD + 0.01:
            assert vessel.pressure < previous

        previous = vessel.pressure

    assert vessel.pressure == pytest.approx(P_STD, abs=0.01)


# --- criterion 3: mass over a closed cycle --------------------------------


def test_mass_is_conserved_over_a_fill_and_vent_cycle():
    """Every step's flows are the ones the integrator used, so the scf that
    entered less the scf that left is the whole change in inventory. The
    cycle fills against a throttled vent, then stops the machine and vents
    down, and the ledger has to close across both.
    """
    plant, engine = gas_plant(volume=50.0, pressure=60.0)
    vessel = plant.devices["V-101"]
    machine = plant.devices["K-101"]

    start = vessel.gas_inventory
    ledger = 0.0
    waypoints = []

    def run(seconds):
        nonlocal ledger

        for _ in range(seconds):
            ledger += (vessel.gas_inlet_flow - vessel.gas_outlet_flow) * 1.0 / 60.0
            engine.step(1.0)

        waypoints.append(vessel.gas_inventory)

    run(300)

    machine.stop()

    run(600)

    assert waypoints[0] > start
    assert waypoints[1] < waypoints[0]
    assert vessel.gas_inventory - start == pytest.approx(ledger, abs=1e-9)


def test_the_ledger_closes_through_a_transient_at_every_step():
    plant, engine = gas_plant(volume=25.0, pressure=80.0)
    vessel = plant.devices["V-101"]

    for _ in range(200):
        inventory = vessel.gas_inventory
        net = vessel.gas_inlet_flow - vessel.gas_outlet_flow

        engine.step(1.0)

        assert vessel.gas_inventory - inventory == pytest.approx(net / 60.0)


# --- the plausible time constant ------------------------------------------


def test_the_reference_vessel_settles_at_the_closed_form_balance_point():
    plant, engine = gas_plant(volume=100.0, pressure=60.0)
    vessel = plant.devices["V-101"]

    for _ in range(3000):
        engine.step(1.0)

    assert vessel.pressure == pytest.approx(equilibrium(), abs=0.05)
    assert equilibrium() == pytest.approx(162.1, abs=0.1)
    assert vessel.gas_inlet_flow == pytest.approx(vessel.gas_outlet_flow, abs=0.5)


def test_the_reference_vessel_responds_on_a_timescale_of_minutes():
    """Neither instantaneous nor glacial: the 100 ft^3 vessel closes 63.2% of
    its way to the balance point in about the linearised 220 s.
    """
    plant, engine = gas_plant(volume=100.0, pressure=60.0)
    vessel = plant.devices["V-101"]

    target = 60.0 + (1.0 - math.exp(-1.0)) * (equilibrium() - 60.0)
    elapsed = 0

    while vessel.pressure < target:
        engine.step(1.0)
        elapsed += 1

    assert time_constant(100.0) == pytest.approx(220.0, abs=2.0)
    assert elapsed == pytest.approx(time_constant(100.0), rel=0.10)
    assert 60.0 < elapsed < 1800.0


# --- stability at the project's step --------------------------------------


def test_a_stiff_small_vessel_is_stable_at_a_one_second_step():
    """1 ft^3 is 100x stiffer than the reference. Euler is stable while
    dt * P_std * (dQin/dP + dQout/dP) / V < 2, and at the balance point that
    is 0.45.
    """
    volume = 1.0
    ratio = 1.0 / 60.0 * P_STD * 1.853 / volume

    assert ratio == pytest.approx(0.454, abs=0.001)

    plant, engine = gas_plant(volume=volume, pressure=60.0)
    vessel = plant.devices["V-101"]

    previous = vessel.pressure

    for _ in range(300):
        engine.step(1.0)

        assert vessel.pressure > 0.0
        # Monotone approach: an unstable loop would alternate the sign.
        assert vessel.pressure - previous >= -1e-6

        previous = vessel.pressure

    assert vessel.pressure == pytest.approx(equilibrium(), abs=0.05)


def test_crossing_a_zero_flow_point_overshoots_by_a_predictable_hair():
    """The vent and the compressor are square-root laws, so the flow's slope
    is infinite where it reaches zero (P at the sink, P at shutoff). Explicit
    Euler steps across that point by at most (a * dt)^2 / 4, with
    a = P_std * C / (60 * V) for the vent. It is negligible for the reference
    vessel and grows as 1/V^2 for a small one. Nothing clamps it.
    """
    dt = 1.0

    for volume in (100.0, 30.0, 10.0):
        plant, engine = gas_plant(volume=volume, pressure=100.0, feed=False)
        vessel = plant.devices["V-101"]

        lowest = vessel.pressure

        for _ in range(1500):
            engine.step(dt)
            lowest = min(lowest, vessel.pressure)

        a = P_STD * VALVE_CAPACITY / (60.0 * volume)
        overshoot = P_STD - lowest

        assert 0.0 < overshoot <= (a * dt) ** 2 / 4.0 * 1.01


def test_a_vessel_too_small_for_the_step_refuses_instead_of_going_negative():
    """The limit of the supported range, stated as behaviour: at 0.3 ft^3 the
    vent's own overshoot would carry the pressure below zero, and the
    strictly-positive guard raises. Supported volumes are 10 ft^3 and up at
    a one-second step.
    """
    plant, engine = gas_plant(volume=0.3, pressure=100.0, feed=False)

    with pytest.raises(ValueError, match="pressure must be above 0.0"):
        for _ in range(1500):
            engine.step(1.0)


# --- boundaries: where the pressure lands ---------------------------------


def test_the_same_pressure_is_written_to_every_gas_attached_boundary():
    plant, engine = gas_plant(volume=100.0, pressure=100.0)
    vessel = plant.devices["V-101"]

    for _ in range(30):
        engine.step(1.0)

        assert plant.nodes["N-A"].pressure == vessel.pressure
        assert plant.nodes["N-B"].pressure == vessel.pressure

    assert vessel.pressure != pytest.approx(100.0)


def test_a_fresh_engine_already_reports_the_seed_pressure_at_the_boundaries():
    plant, engine = gas_plant(volume=100.0, pressure=100.0)

    assert plant.nodes["N-A"].pressure == pytest.approx(100.0)
    assert plant.nodes["N-B"].pressure == pytest.approx(100.0)


def test_the_as_built_pressure_survives_and_the_plant_round_trips():
    config_dict = gas_config(volume=100.0, pressure=100.0)
    plant = load_plant(copy.deepcopy(config_dict))
    engine = Engine.from_plant(plant)

    for _ in range(60):
        engine.step(1.0)

    assert plant.nodes["N-A"].configured_pressure == pytest.approx(SUPPLY)
    assert plant.nodes["N-B"].configured_pressure == pytest.approx(SUPPLY)
    assert plant.nodes["N-A"].pressure != pytest.approx(SUPPLY)
    assert plant.to_config()["nodes"] == config_dict["nodes"]


def test_a_blocked_side_is_not_written_and_only_the_attached_node_moves():
    plant, engine = gas_plant(volume=100.0, pressure=100.0, vent=False)

    coupling = build_couplings(plant.devices.values(), plant.topologies)[0]

    assert [attachment.port.name for attachment in coupling.attachments] == ["inlet"]

    engine.step(30.0)

    assert plant.nodes["N-A"].pressure == plant.devices["V-101"].pressure
    assert plant.nodes["N-B"].pressure == pytest.approx(SUPPLY)


# --- the two phases stay apart --------------------------------------------


def mixed_config():
    """The ADR's own figure: a liquid inlet and a gas outlet on one vessel."""
    return {
        "nodes": [
            {"id": "N-L1", "boundary": True, "pressure": 60.0, "domain": "liquid"},
            {"id": "N-L2", "boundary": True, "pressure": 120.0, "domain": "liquid"},
            {"id": "N-B", "boundary": True, "pressure": SUPPLY, "domain": "gas"},
            {"id": "N-X", "boundary": True, "pressure": P_STD, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-L1",
                "node_out": "N-L2",
                "design": {"running": True, "speed": 1.0, "speed_target": 1.0},
            },
            {
                "tag": "FV-101",
                "type": "control_valve",
                "node_in": "N-B",
                "node_out": "N-X",
                "design": {"capacity": VALVE_CAPACITY},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-L2", "outlet": "N-B"},
                "paths": [],
                "design": {
                    "head_at_full": 20.0,
                    "gas_volume": 100.0,
                    "initial_pressure": 100.0,
                },
            },
        ],
    }


def test_scfm_never_reaches_a_gpm_attribute_nor_gpm_a_gas_one():
    plant = load_plant(mixed_config())
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]

    coupling = build_couplings(plant.devices.values(), plant.topologies)[0]

    assert {a.port.name: a.unit for a in coupling.attachments} == {
        "inlet": GPM,
        "outlet": SCFM,
    }

    engine.step(10.0)

    assert vessel.inlet_flow == pytest.approx(1000.0)
    assert vessel.outlet_flow == pytest.approx(0.0)
    assert vessel.gas_inlet_flow == pytest.approx(0.0)
    assert vessel.gas_outlet_flow == pytest.approx(vent_flow(vessel.pressure), rel=0.01)


def test_a_liquid_head_never_reaches_a_gas_boundary():
    plant = load_plant(mixed_config())
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]

    for _ in range(60):
        engine.step(10.0)

    assert vessel.head > 0.0
    assert plant.nodes["N-B"].pressure == vessel.pressure
    # And the liquid boundary is untouched: it is an inlet, so no head lands.
    assert plant.nodes["N-L2"].pressure == pytest.approx(120.0)


def test_a_liquid_only_vessel_never_activates_the_gas_phase():
    config_dict = {
        "nodes": [
            {"id": "N-L1", "boundary": True, "pressure": 60.0, "domain": "liquid"},
            {"id": "N-L2", "boundary": True, "pressure": 120.0, "domain": "liquid"},
            {"id": "N-L3", "boundary": True, "pressure": 50.0, "domain": "liquid_out"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-L1",
                "node_out": "N-L2",
                "design": {"running": True, "speed": 1.0, "speed_target": 1.0},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {"inlet": "N-L2", "outlet": "N-L3"},
                "paths": [],
                "design": {"capacity": 5000.0, "head_at_full": 20.0, "level": 0.5},
            },
        ],
    }
    plant = load_plant(config_dict)
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]

    snapshot = None

    for _ in range(20):
        snapshot = engine.step(10.0)

    assert vessel.gas_active is False
    assert vessel.pressure == pytest.approx(P_STD)
    assert set(snapshot.equipment["V-101"]) == LIQUID_STATE_KEYS
    assert vessel.level > 0.5
    assert vessel.inlet_flow == pytest.approx(1000.0)
    assert vessel.gas_inlet_flow == pytest.approx(0.0)


# --- engine behaviour that must hold for gas too --------------------------


def test_a_paused_engine_moves_no_gas_state():
    plant, engine = gas_plant(volume=100.0, pressure=100.0)
    vessel = plant.devices["V-101"]

    engine.step(5.0)
    engine.stop()

    held = (
        vessel.pressure,
        vessel.gas_inlet_flow,
        vessel.gas_outlet_flow,
        plant.nodes["N-A"].pressure,
        plant.nodes["N-B"].pressure,
    )

    for dt in (1.0, 60.0, 3600.0):
        engine.step(dt)

        assert (
            vessel.pressure,
            vessel.gas_inlet_flow,
            vessel.gas_outlet_flow,
            plant.nodes["N-A"].pressure,
            plant.nodes["N-B"].pressure,
        ) == held


def test_a_gas_domain_that_did_not_converge_writes_no_gas_flow():
    plant, engine = gas_plant(volume=100.0, pressure=100.0)
    vessel = plant.devices["V-101"]

    held_inlet = vessel.gas_inlet_flow

    engine.solvers["gas_feed"] = NetworkSolver(
        plant.topologies["gas_feed"],
        max_iterations=1,
    )

    for branch in plant.topologies["gas_feed"].branches.values():
        branch.set_flow(0.0)

    engine.step(10.0)

    assert engine.solver_results["gas_feed"].converged is False
    assert engine.solver_results["gas"].converged is True
    assert vessel.gas_inlet_flow == pytest.approx(held_inlet)
    assert vessel.gas_outlet_flow == pytest.approx(vent_flow(vessel.pressure), rel=0.01)


def test_two_identical_gas_runs_are_bit_identical():
    def run():
        plant, engine = gas_plant(volume=30.0, pressure=70.0)

        return [engine.step(2.0).as_dict() for _ in range(150)]

    assert run() == run()
