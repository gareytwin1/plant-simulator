"""
The integrated reference plant — T5-5.

`config/plants/olefins_lite.yaml` is two independent hydraulic domains
(liquid through P-101, gas through K-101) coupled only through V-101's
inventory (ADR 0001 D6/D7/D11), never a shared flow variable. The vessel is
the production `Vessel` in configured-port mode (T5-7), wired by the
shared-node idiom (ADR 0002 section 3.8): one node per phase, and V-101
takes exactly one typed port per (phase, direction) — a liquid inlet and a
liquid outlet sharing N-102, and one vapor outlet on N-201, where K-101 and
PV-101 are two branches rather than two vessel nozzles.

The fixture is built to sit exactly at its own design equilibrium the
moment it is loaded (the YAML's own header comment carries the closed-form
derivation), so there is no horizon-bounded conservation test standing in
for missing physics: level and pressure hold their design values for as
long as the plant runs. `test_the_design_point_is_a_genuine_steady_state`
and `test_the_liquid_mass_balance_closes_at_a_genuine_steady_state` check
exactly that, comparing widely separated step counts rather than a single
snapshot — the same shape T5-6's own coupling fixture uses, and for the
same reason: the solver's own tolerance band makes this a bounded sawtooth,
never bit-exact (T5-7 section C.11), so every settled comparison here is
`rel=1e-6`, not equality.

Cold start and K-101's reversal are ADR 0002 section 7.3's, not a numerical
artifact: V-101 has no vapor *inlet* port, so nothing manufactures vapor,
and the only way gas pressure can sit at a true steady state is for K-101
and PV-101 to sum to zero — which means K-101 must draw backward out of its
own discharge header by exactly what PV-101 vents. Both machines' own
resistance (not the class default) is sized so that reversal, cold and
running alike, stays a small, credible number instead of an unbounded one.
"""

import math
from pathlib import Path

import pytest

from app.engine.engine import Engine
from app.plant.loader import load_plant, load_plant_file

PLANT_FILE = Path(__file__).resolve().parent.parent / "config" / "plants" / "olefins_lite.yaml"

STEP = 1.0

# The YAML header's own closed-form derivation, reproduced here as the
# expectation rather than re-derived, so a design-value edit that breaks the
# equilibrium fails loudly instead of silently.
DESIGN_LEVEL = 0.5
DESIGN_PRESSURE = 200.0
DESIGN_FEED_FLOW = 50.0
DESIGN_DRAIN_FLOW = 50.0
DESIGN_K101_FLOW = -10.0   # reversed — see the module docstring
DESIGN_PV101_FLOW = 10.0

COLD_FEED_FLOW = -100.0    # P-101 off: pure resistance against the same 20 psi
COLD_K101_FLOW = -math.sqrt(105.0 / 0.05)   # K-101 off: pure resistance, 105 psi gap


def load():
    return load_plant_file(PLANT_FILE)


def flow(plant, tag, domain):
    return plant.topologies[domain].branches[f"B-{tag}"].flow


def state(plant):
    vessel = plant.devices["V-101"]

    return (
        vessel.level,
        vessel.pressure,
        flow(plant, "P-101", "liquid"),
        flow(plant, "LV-101", "liquid"),
        flow(plant, "K-101", "gas"),
        flow(plant, "PV-101", "gas"),
    )


def start(plant):
    pump = plant.devices["P-101"]
    pump.start()
    pump.set_speed_target(1.0)

    compressor = plant.devices["K-101"]
    compressor.start()
    compressor.set_load_target(1.0)


def run(engine, steps):
    snapshot = engine.snapshot()

    for _ in range(steps):
        snapshot = engine.step(STEP)

    return snapshot


def run_to(plant, engine, steps):
    """Advance `steps` and read state straight off the live devices.

    `state()` takes a `Plant`, not a `Snapshot` — this is the one place
    that combination is needed, so it is spelled out once here.
    """
    run(engine, steps)

    return state(plant)


# --------------------------------------------------------------------------
# 1. Structure
# --------------------------------------------------------------------------


def test_the_fixture_loads_as_two_independent_domains():
    plant = load()

    assert set(plant.topologies) == {"liquid", "gas"}
    assert set(plant.topologies["liquid"].devices) == {"P-101", "LV-101"}
    assert set(plant.topologies["gas"].devices) == {"K-101", "PV-101"}

    # V-101 is a coupling device: it has no Branch and sits in no Topology.
    assert "V-101" not in plant.topologies["liquid"].devices
    assert "V-101" not in plant.topologies["gas"].devices
    assert "V-101" in plant.devices


def test_v_101_takes_exactly_three_typed_ports_on_the_shared_node_idiom():
    vessel = load().devices["V-101"]

    assert set(vessel.ports) == {"feed", "drain", "vapor_out"}
    assert vessel.ports["feed"].node.id == "N-102"
    assert vessel.ports["drain"].node.id == "N-102"
    assert vessel.ports["vapor_out"].node.id == "N-201"
    assert vessel.ports["feed"].phase == "liquid"
    assert vessel.ports["drain"].phase == "liquid"
    assert vessel.ports["vapor_out"].phase == "vapor"


def test_k_101_and_pv_101_are_two_branches_on_the_one_vapor_node():
    plant = load()

    on_vapor_node = {
        branch.device.tag
        for branch in plant.topologies["gas"].branches_at("N-201")
    }

    assert on_vapor_node == {"K-101", "PV-101"}


def test_pv_101_and_lv_101_are_manual_with_no_controller():
    plant = load()

    # Only a start/stop-able machine has a controller to wait for; a valve's
    # position is manual by construction, and this fixture ships no
    # controller object at all (M8 owns closed-loop control, ADR 0002 3.7).
    assert not hasattr(plant.devices["LV-101"], "start")
    assert not hasattr(plant.devices["PV-101"], "start")


def test_round_trip_plant_config_plant_is_identical():
    plant = load()

    config = plant.to_config()
    again = load_plant(config)

    assert again.to_config() == config


# --------------------------------------------------------------------------
# 2. Cold start — credible, not a runaway (ADR 0002 section 7.3)
# --------------------------------------------------------------------------


def test_a_cold_plant_backflows_by_a_bounded_credible_amount():
    """P-101 and K-101 are loaded stopped — the loader's own convention — so
    their branches are pure resistance against the same pressure gap the
    running machine would otherwise lift. That gap does not vanish just
    because the machine is off, so both branches run backward; the point of
    sizing each machine's own resistance for this plant is that the
    backflow is a small multiple of the running flow, never a runaway.
    """
    plant = load()
    engine = Engine.from_plant(plant)

    cold = engine.step(STEP)

    assert flow(plant, "P-101", "liquid") == pytest.approx(COLD_FEED_FLOW, abs=0.1)
    assert flow(plant, "K-101", "gas") == pytest.approx(COLD_K101_FLOW, abs=0.1)

    # Bounded by each machine's own rating — not thousands of GPM or SCFM.
    assert abs(flow(plant, "P-101", "liquid")) < plant.devices["P-101"].max_flow
    assert abs(flow(plant, "K-101", "gas")) < plant.devices["K-101"].max_flow

    assert cold.solver["converged"] is True


def test_the_drain_and_vent_valves_are_unaffected_by_the_machines_being_off():
    """LV-101 and PV-101 have no running state — they are already at their
    design position, cold or hot — so their cold flow equals their design
    flow exactly, unlike the two machines.
    """
    plant = load()
    engine = Engine.from_plant(plant)

    engine.step(STEP)

    assert flow(plant, "LV-101", "liquid") == pytest.approx(DESIGN_DRAIN_FLOW, abs=0.1)
    assert flow(plant, "PV-101", "gas") == pytest.approx(DESIGN_PV101_FLOW, abs=0.1)


def test_p_101_and_k_101_are_loaded_stopped():
    plant = load()

    assert plant.devices["P-101"].running is False
    assert plant.devices["P-101"].speed == 0.0
    assert plant.devices["K-101"].running is False
    assert plant.devices["K-101"].load == 0.0


# --------------------------------------------------------------------------
# 3. The design point is a genuine steady state, not a bounded horizon
# --------------------------------------------------------------------------


def test_the_design_point_is_a_genuine_steady_state():
    """Level and pressure are loaded at their own equilibrium values, so
    starting the machines should not move the plant at all — feed already
    equals drain, and K-101's reversal already equals PV-101's vent. This is
    the T5-5 acceptance criterion (ADR 0002, replacing T3-4's horizon-bounded
    one): mass balance closes at a genuine steady state.
    """
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    at_5000 = run_to(plant, engine, 5000)
    at_50000 = run_to(plant, engine, 45000)

    level, pressure, feed, drain, k101, pv101 = at_50000

    assert level == pytest.approx(DESIGN_LEVEL, rel=1e-6)
    assert pressure == pytest.approx(DESIGN_PRESSURE, rel=1e-6)
    assert feed == pytest.approx(DESIGN_FEED_FLOW, rel=1e-6)
    assert drain == pytest.approx(DESIGN_DRAIN_FLOW, rel=1e-6)
    assert k101 == pytest.approx(DESIGN_K101_FLOW, rel=1e-6)
    assert pv101 == pytest.approx(DESIGN_PV101_FLOW, rel=1e-6)

    # No horizon-bounded trick: step 5 000 and step 50 000 agree tightly,
    # rather than one of them being where the test stops looking. Absolute,
    # not relative — K-101's own flow sits near zero, where a relative bound
    # is meaningless.
    for early, late in zip(at_5000, at_50000):
        assert early == pytest.approx(late, abs=0.03)


def test_the_liquid_mass_balance_closes_at_a_genuine_steady_state():
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    run(engine, 20000)
    vessel = plant.devices["V-101"]

    assert vessel.inlet_flow == pytest.approx(vessel.outlet_flow, rel=1e-5)


def test_both_domains_converge_throughout_the_run():
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    for _ in range(500):
        snapshot = engine.step(STEP)

        assert snapshot.solver["converged"] is True


def test_two_identical_runs_are_bit_identical():
    a = load()
    engine_a = Engine.from_plant(a)
    start(a)

    b = load()
    engine_b = Engine.from_plant(b)
    start(b)

    for _ in range(500):
        engine_a.step(STEP)
        engine_b.step(STEP)

    assert state(a) == state(b)


# --------------------------------------------------------------------------
# 4. Stroking a manual valve moves the vessel in the expected direction
# --------------------------------------------------------------------------


def test_opening_lv_101_lowers_the_level():
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    run(engine, 6000)
    before = plant.devices["V-101"].level

    plant.devices["LV-101"].set_position_target(0.6)
    run(engine, 6000)
    after = plant.devices["V-101"].level

    assert after < before


def test_closing_lv_101_raises_the_level():
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    run(engine, 6000)
    before = plant.devices["V-101"].level

    plant.devices["LV-101"].set_position_target(0.35)
    run(engine, 6000)
    after = plant.devices["V-101"].level

    assert after > before


def test_opening_pv_101_lowers_the_pressure():
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    run(engine, 6000)
    before = plant.devices["V-101"].pressure

    plant.devices["PV-101"].set_position_target(0.8)
    run(engine, 6000)
    after = plant.devices["V-101"].pressure

    assert after < before


def test_closing_pv_101_raises_the_pressure():
    plant = load()
    engine = Engine.from_plant(plant)
    start(plant)

    run(engine, 6000)
    before = plant.devices["V-101"].pressure

    plant.devices["PV-101"].set_position_target(0.35)
    run(engine, 6000)
    after = plant.devices["V-101"].pressure

    assert after > before


# --------------------------------------------------------------------------
# 5. No domain mixes flow units
# --------------------------------------------------------------------------


def test_the_liquid_domain_carries_only_gpm_and_the_gas_domain_only_scfm():
    plant = load()
    Engine.from_plant(plant)  # raises at construction if any node disagrees
    vessel = plant.devices["V-101"]

    assert vessel.gas_active is True

    # A gross error here would be a phase/unit mismatch slipping through,
    # not a design-value quirk: liquid flows in the hundreds of GPM, gas
    # flows in the tens of SCFM, and neither is ever added to the other.
    assert math.isfinite(vessel.inlet_flow)
    assert math.isfinite(vessel.gas_outlet_flow)
