"""
The mass balance conservation suite — T5-4.

Owns the conservation identity ADR 0002 section 7.1 found and assigned here.
`Engine._couple()` runs in the constructor, so a flow already exists before
the first `step()`, and each `integrate` consumes the flow the *previous*
pass solved — never the one that same step goes on to publish. The correct
ledger is therefore

    Δinventory  =  Σ (n = 0 … N-1)  q_n · dt / 60

with q_0 read before the first step is ever taken. Summing the N
*published* flows instead — read after each `step()` returns rather than
before it is called — silently shifts every term by one step, and the two
sums differ by exactly one telescoped term:

    naive - correct  =  (q_N - q_0) · dt / 60

That is an algebraic identity, not an approximation, and it is proved
directly below rather than only observed. It vanishes where flow never
changes across the run, which is exactly why the bug hides completely on a
plant already sitting at steady state and only shows up on a transient —
the reason this suite exists as its own task rather than folding into a
steady-state fixture test.

`config/plants/olefins_lite.yaml` (T5-5) is the fixture: the first plant
whose steady state is a genuine equilibrium rather than a vessel that fills
until a test stops looking (ADR 0002 section 8.2). It is loaded cold — P-101
and K-101 both start stopped, so their branches are pure resistance against
the same boundary differential the running machine would otherwise lift —
so starting them with `start()` ramps flow away from, and back to, the
design point. That ramp is real mass in motion, which is exactly what a
conservation ledger needs to be worth writing. Liquid (P-101 / LV-101, GPM)
and gas (K-101 / PV-101, SCFM) are two independent flow domains coupled only
through V-101's inventory (ADR 0001), never a shared flow variable, so each
is checked on its own terms and the two ledgers are never added together.
"""

from pathlib import Path

import pytest

from app.engine.engine import Engine
from app.plant.loader import load_plant_file

PLANT_FILE = Path(__file__).resolve().parent.parent / "config" / "plants" / "olefins_lite.yaml"

STEP = 1.0


def load():
    return load_plant_file(PLANT_FILE)


def start(plant):
    pump = plant.devices["P-101"]
    pump.start()
    pump.set_speed_target(1.0)

    compressor = plant.devices["K-101"]
    compressor.start()
    compressor.set_load_target(1.0)


def net_liquid(vessel):
    return vessel.inlet_flow - vessel.outlet_flow          # GPM


def net_gas(vessel):
    return vessel.gas_inlet_flow - vessel.gas_outlet_flow  # SCFM


def ledgers(engine, vessel, steps, dt=STEP):
    """Advance `steps` steps, accumulating both conservation ledgers from
    one physical trajectory rather than two separately-run ones.

    `correct` sums the flow standing *before* each step — the documented
    identity. `naive` sums the flow published *after* each step — the wrong
    convention the identity warns against. The two are therefore built from
    exactly the same sequence of steps and can only differ by the telescoped
    boundary term, never by simulation noise between separate runs.
    """
    correct_liquid = 0.0
    correct_gas = 0.0
    naive_liquid = 0.0
    naive_gas = 0.0

    for _ in range(steps):
        correct_liquid += net_liquid(vessel) * dt / 60.0
        correct_gas += net_gas(vessel) * dt / 60.0

        engine.step(dt)

        naive_liquid += net_liquid(vessel) * dt / 60.0
        naive_gas += net_gas(vessel) * dt / 60.0

    return correct_liquid, correct_gas, naive_liquid, naive_gas


# --------------------------------------------------------------------------
# 1. The documented identity closes tightly through a real transient
# --------------------------------------------------------------------------


def test_the_correct_ledger_closes_through_the_cold_start_transient():
    """Loaded cold, starting P-101 and K-101 is what moves real mass: level
    and pressure move away from their design values and back (T5-5's own
    steady-state tests confirm the return). This is the ledger that
    accounts for how much moved along the way, closed against the actual
    change in each inventory.
    """
    plant = load()
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]
    start(plant)

    start_volume = vessel.volume
    start_gas_inventory = vessel.gas_inventory

    correct_liquid, correct_gas, _, _ = ledgers(engine, vessel, 10_000)

    assert vessel.volume - start_volume == pytest.approx(correct_liquid, abs=1e-6)
    assert vessel.gas_inventory - start_gas_inventory == pytest.approx(correct_gas, abs=1e-6)


def test_no_drift_accumulates_over_a_long_run_once_settled():
    """T5-5 reaches a genuine steady state (ADR 0002 section 8.2), so a
    longer horizon must not make the ledger's own error grow. This is the
    replacement for the retired "10,000 steps at steady state" criterion,
    checked honestly against a fixture that never needs to clamp, rather
    than by stopping the run before one would.
    """
    plant = load()
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]
    start(plant)

    start_volume = vessel.volume
    start_gas_inventory = vessel.gas_inventory
    running_liquid = 0.0
    running_gas = 0.0
    errors = []

    for _ in range(4):
        correct_liquid, correct_gas, _, _ = ledgers(engine, vessel, 5_000)
        running_liquid += correct_liquid
        running_gas += correct_gas

        errors.append(
            (
                (vessel.volume - start_volume) - running_liquid,
                (vessel.gas_inventory - start_gas_inventory) - running_gas,
            ),
        )

    # Bounded well above the float noise actually observed (~1e-10 over
    # 20,000 steps) and far below anything that would read as real drift —
    # not shrinking toward zero is what "no drift" means here, since the
    # plant itself is settled well before the first checkpoint.
    for liquid_error, gas_error in errors:
        assert abs(liquid_error) < 1e-6
        assert abs(gas_error) < 1e-6


# --------------------------------------------------------------------------
# 2. Summing published flow instead is wrong by (q_N - q_0) * dt / 60
# --------------------------------------------------------------------------


def test_summing_published_flow_is_off_by_exactly_one_steps_worth():
    """The identity's boundary term, proved directly rather than only
    observed: naive - correct telescopes to the one term that differs
    between the two sums — q_N, read after the loop, less q_0, the flow
    standing before the very first step.
    """
    plant = load()
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]
    start(plant)

    q0_liquid = net_liquid(vessel)
    q0_gas = net_gas(vessel)

    correct_liquid, correct_gas, naive_liquid, naive_gas = ledgers(engine, vessel, 10_000)

    qN_liquid = net_liquid(vessel)
    qN_gas = net_gas(vessel)

    assert naive_liquid - correct_liquid == pytest.approx(
        (qN_liquid - q0_liquid) * STEP / 60.0,
    )
    assert naive_gas - correct_gas == pytest.approx(
        (qN_gas - q0_gas) * STEP / 60.0,
    )


def test_summing_published_flow_disagrees_with_the_real_inventory_change():
    """Not just algebraically different from the correct ledger — materially
    wrong about this fixture's own physics. The cold-to-design transient
    carries enough one-step flow that the naive ledger's disagreement with
    the real inventory change is whole units, several orders above the
    correct ledger's own (float-noise) error against the same number.
    """
    plant = load()
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]
    start(plant)

    start_volume = vessel.volume
    start_gas_inventory = vessel.gas_inventory

    correct_liquid, correct_gas, naive_liquid, naive_gas = ledgers(engine, vessel, 10_000)

    actual_liquid = vessel.volume - start_volume
    actual_gas = vessel.gas_inventory - start_gas_inventory

    assert actual_liquid == pytest.approx(correct_liquid, abs=1e-6)
    assert actual_gas == pytest.approx(correct_gas, abs=1e-6)

    assert abs(actual_liquid - naive_liquid) > 1.0
    assert abs(actual_gas - naive_gas) > 0.1


# --------------------------------------------------------------------------
# 3. The identity holds at every individual step, not only in aggregate
# --------------------------------------------------------------------------


def test_the_identity_holds_at_every_step_through_the_transient():
    """A cumulative check can hide a per-step bug behind cancelling errors.
    This asserts the one-step form of the same identity —
    Δinventory this step equals q_n · dt / 60 — at every step immediately
    after start(), while the ramp is still moving flow on both domains.
    """
    plant = load()
    engine = Engine.from_plant(plant)
    vessel = plant.devices["V-101"]
    start(plant)

    for _ in range(500):
        before_volume = vessel.volume
        before_gas_inventory = vessel.gas_inventory
        q_liquid = net_liquid(vessel)
        q_gas = net_gas(vessel)

        engine.step(STEP)

        assert vessel.volume - before_volume == pytest.approx(
            q_liquid * STEP / 60.0,
            abs=1e-9,
        )
        assert vessel.gas_inventory - before_gas_inventory == pytest.approx(
            q_gas * STEP / 60.0,
            abs=1e-9,
        )
