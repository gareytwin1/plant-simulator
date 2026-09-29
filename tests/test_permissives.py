"""
Restart permissives and trip reset (T11-3).

A topology-free engine, as in test_trip_actions: `HV-900` is the hand-stroked
trip trigger and `HV-901` the hand-stroked permissive source, so the test
decides exactly when each changes. Every run advances the way `RestartGate`'s
module docstring requires: gate first, then the trip system, then the step.
"""

import warnings

import pytest

from app.controls.arbitration import Source
from app.engine.engine import Engine
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.safety.actions import TripSystem
from app.safety.interlocks import InterlockState, load_interlocks
from app.safety.permissives import Permissive, RestartGate

STEP = 1.0
TRIGGER = "HV-900"
ENABLE = "HV-901"
PERMIT = Permissive.parse(f"{ENABLE}.position >= 0.5")


def trip_entry(reset):
    return {
        "tag": "XS-1",
        "condition": f"{TRIGGER}.position <= 0.5",
        "delay_s": 0.0,
        "actions": ["M-1.stop"],
        "reset": reset,
    }


def build(machine_type=CentrifugalPump, permissives=(), reset="auto", gated_reset=True):
    trigger = ControlValve(TRIGGER)
    enable = ControlValve(ENABLE)
    machine = machine_type("M-1")
    engine = Engine([trigger, enable, machine])
    engine.start()
    interlocks = load_interlocks({"interlocks": [trip_entry(reset)]})
    trips = TripSystem(interlocks, engine.equipment, engine.arbiter, engine.snapshot())
    gate = RestartGate(
        "M-1",
        engine.equipment,
        engine.arbiter,
        interlocks,
        engine.snapshot(),
        permissives=permissives,
        resets=["XS-1"] if gated_reset else [],
    )

    return engine, trips, gate, trigger, enable, machine, interlocks["XS-1"]


def run(engine, trips, gate, seconds):
    for _ in range(int(seconds / STEP)):
        snapshot = engine.snapshot()
        gate.update(snapshot)
        trips.update(snapshot)
        engine.step(STEP)


def operator_run(engine):
    engine.arbiter.demand("M-1.run", Source.OPERATOR, "operator", 1.0)


def stroke(valve, position, engine, trips, gate):
    valve.set_position_target(position)
    run(engine, trips, gate, 12.0)


# Start blocked until permissives are satisfied


@pytest.mark.parametrize("machine_type", [CentrifugalPump, GasCompressor])
def test_start_is_blocked_by_an_unsatisfied_permissive_and_says_why(machine_type):
    engine, trips, gate, _, enable, machine, _ = build(machine_type, [PERMIT])
    stroke(enable, 0.0, engine, trips, gate)

    operator_run(engine)
    run(engine, trips, gate, 5.0)

    assert not machine.running
    assert len(gate.blocked) == 1
    assert "HV-901.position >= 0.5" in gate.blocked[0]
    assert "HV-901.position reads" in gate.blocked[0]


def test_start_proceeds_once_the_permissive_is_satisfied():
    engine, trips, gate, _, enable, machine, _ = build(permissives=[PERMIT])
    stroke(enable, 0.0, engine, trips, gate)
    operator_run(engine)
    run(engine, trips, gate, 5.0)
    assert not machine.running

    stroke(enable, 1.0, engine, trips, gate)

    assert gate.blocked == ()
    assert machine.running


def test_every_unsatisfied_permissive_is_reported():
    other = Permissive.parse(f"{TRIGGER}.position >= 2.0")
    engine, trips, gate, _, enable, *_ = build(permissives=[PERMIT, other])

    stroke(enable, 0.0, engine, trips, gate)

    assert len(gate.blocked) == 2


def test_losing_a_permissive_after_start_does_not_stop_the_machine():
    engine, trips, gate, _, enable, machine, _ = build(permissives=[PERMIT])
    stroke(enable, 1.0, engine, trips, gate)
    operator_run(engine)
    run(engine, trips, gate, 5.0)
    assert machine.running

    stroke(enable, 0.0, engine, trips, gate)

    assert gate.blocked
    assert machine.running


def test_an_unpublished_reading_does_not_permit_a_start():
    with pytest.warns(UserWarning, match="no_such_variable"):
        engine, trips, gate, *_, machine, _ = build(
            permissives=[Permissive.parse(f"{ENABLE}.no_such_variable >= 0.0")],
        )

    operator_run(engine)

    run(engine, trips, gate, 5.0)

    assert not machine.running
    assert "not published" in gate.blocked[0]


def test_a_published_permissive_variable_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build(permissives=[PERMIT])


# Reset required after a trip


@pytest.mark.parametrize("reset", ["auto", "manual"])
def test_a_tripped_machine_stays_blocked_after_the_condition_clears(reset):
    engine, trips, gate, trigger, _, machine, interlock = build(reset=reset)
    machine.start()
    stroke(trigger, 0.0, engine, trips, gate)
    assert interlock.tripped
    assert not machine.running

    operator_run(engine)
    stroke(trigger, 1.0, engine, trips, gate)

    assert not machine.running
    assert gate.awaiting_reset == ("XS-1",)
    assert gate.blocked == ("interlock XS-1 tripped, reset required",)


@pytest.mark.parametrize("reset", ["auto", "manual"])
def test_reset_after_the_condition_clears_lets_the_machine_start(reset):
    engine, trips, gate, trigger, _, machine, interlock = build(reset=reset)
    machine.start()
    stroke(trigger, 0.0, engine, trips, gate)
    operator_run(engine)
    stroke(trigger, 1.0, engine, trips, gate)

    assert gate.reset()
    run(engine, trips, gate, 2.0)

    assert interlock.state is InterlockState.NORMAL
    assert gate.blocked == ()
    assert machine.running


def test_ungated_auto_trip_restarts_on_a_standing_run_demand():
    engine, trips, gate, trigger, _, machine, _ = build(gated_reset=False)
    machine.start()
    operator_run(engine)
    stroke(trigger, 0.0, engine, trips, gate)
    assert not machine.running

    stroke(trigger, 1.0, engine, trips, gate)

    assert machine.running


# Reset without clearing the condition re-trips


@pytest.mark.parametrize("reset", ["auto", "manual"])
def test_reset_with_the_condition_still_present_changes_nothing(reset):
    engine, trips, gate, trigger, _, machine, interlock = build(reset=reset)
    machine.start()
    stroke(trigger, 0.0, engine, trips, gate)
    operator_run(engine)

    assert not gate.reset()
    run(engine, trips, gate, 2.0)

    assert interlock.tripped
    assert gate.awaiting_reset == ("XS-1",)
    assert not machine.running


def test_condition_returning_after_a_reset_trips_again():
    engine, trips, gate, trigger, _, machine, interlock = build(reset="manual")
    machine.start()
    stroke(trigger, 0.0, engine, trips, gate)
    stroke(trigger, 1.0, engine, trips, gate)
    assert gate.reset()
    operator_run(engine)
    run(engine, trips, gate, 2.0)
    assert machine.running

    stroke(trigger, 0.0, engine, trips, gate)

    assert interlock.tripped
    assert not machine.running
    assert gate.awaiting_reset == ("XS-1",)


def test_reset_leaves_an_interlock_the_gate_was_not_given_alone():
    engine, trips, gate, trigger, _, machine, interlock = build(
        reset="manual",
        gated_reset=False,
    )
    machine.start()
    stroke(trigger, 0.0, engine, trips, gate)
    stroke(trigger, 1.0, engine, trips, gate)

    assert gate.reset()
    assert interlock.tripped


# Construction refuses what cannot work


def test_gate_refuses_a_device_that_is_not_a_machine():
    engine, _, _, _, _, _, _ = build()

    with pytest.raises(ValueError, match="pump or compressor"):
        RestartGate(ENABLE, engine.equipment, engine.arbiter, {}, engine.snapshot())


def test_gate_refuses_an_unknown_interlock_or_permissive_device():
    engine, *_ = build()

    with pytest.raises(ValueError, match="unknown interlock"):
        RestartGate(
            "M-1",
            engine.equipment,
            engine.arbiter,
            {},
            engine.snapshot(),
            resets=["XS-9"],
        )

    with pytest.raises(ValueError, match="unknown device"):
        RestartGate(
            "M-1",
            engine.equipment,
            engine.arbiter,
            {},
            engine.snapshot(),
            permissives=[Permissive.parse("V-9.position >= 0.5")],
        )
