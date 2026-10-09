"""
Trip actions on equipment (T11-2).

Two harnesses. Most tests use a topology-free engine whose trigger is a
hand-stroked valve, `HV-900`: an interlock watches its position, so the test
decides exactly when a trip latches without depending on any process
response, and what is under test is only what the trip then does to its
target. The end-to-end tests run the reference plant, where the trip
conditions are real process variables and the cascade has to come out of the
solver.

Every run advances the way `TripSystem`'s module docstring requires: update
on the latest snapshot, immediately before each step.
"""

import dataclasses
from pathlib import Path
from types import MappingProxyType

import pytest

from app.controls.arbitration import CommandArbiter, Source
from app.controls.modes import Mode
from app.disturbances.malfunction import Malfunction, MalfunctionRegistry
from app.engine.engine import Engine
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.registry import EquipmentRegistry
from app.equipment.valve import FAIL_ACTIONS, FAIL_OPEN, ControlValve
from app.plant.loader import load_plant_file
from app.safety.actions import FAIL_TARGETS, TRIP_ACTIONS, TripSystem
from app.safety.interlocks import InterlockState, load_interlocks

PLANT_FILE = Path(__file__).resolve().parent.parent / "config" / "plants" / "olefins_lite.yaml"

STEP = 1.0

TRIGGER = "HV-900"


def interlock(tag, actions, reset="auto", delay_s=0.0):
    return {
        "tag": tag,
        "condition": f"{TRIGGER}.position <= 0.5",
        "delay_s": delay_s,
        "actions": actions,
        "reset": reset,
    }


def build(devices, *entries):
    trigger = ControlValve(TRIGGER)
    engine = Engine([trigger, *devices])
    engine.start()
    trips = TripSystem(
        load_interlocks({"interlocks": list(entries)}),
        engine.equipment,
        engine.arbiter,
        engine.snapshot(),
    )

    return engine, trips, trigger


def run(engine, trips, seconds, between=None):
    for _ in range(int(seconds / STEP)):
        trips.update(engine.snapshot())

        if between is not None:
            between()

        engine.step(STEP)


def run_operator_first(engine, trips, seconds, operator):
    """An operator write lands between steps, before the trip system runs."""
    for _ in range(int(seconds / STEP)):
        operator()
        trips.update(engine.snapshot())
        engine.step(STEP)


def trip(engine, trips, trigger):
    trigger.set_position_target(0.0)
    run(engine, trips, 12.0)

    assert trips.tripped


def clear(engine, trips, trigger):
    trigger.set_position_target(1.0)
    run(engine, trips, 12.0)


def running_machine(machine_type):
    machine = machine_type("M-1")
    machine.start()

    if isinstance(machine, CentrifugalPump):
        machine.set_speed_target(1.0)
    else:
        machine.set_load_target(1.0)

    return machine


def output_of(machine):
    return machine.speed if isinstance(machine, CentrifugalPump) else machine.load


def target_of(machine):
    return machine.speed_target if isinstance(machine, CentrifugalPump) else machine.load_target


# Each trip action leaves the configured state


@pytest.mark.parametrize("machine_type", [CentrifugalPump, GasCompressor])
def test_stop_leaves_a_running_machine_stopped(machine_type):
    machine = running_machine(machine_type)
    engine, trips, trigger = build([machine], interlock("XS-1", ["M-1.stop"]))
    run(engine, trips, 30.0)
    assert machine.running
    assert output_of(machine) == pytest.approx(1.0)

    trip(engine, trips, trigger)
    run(engine, trips, 30.0)

    assert not machine.running
    assert output_of(machine) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("verb", "fail_action", "expected"),
    [
        ("close", None, 0.10),
        ("open", None, 1.0),
        ("fail", None, 0.10),
        ("fail", FAIL_OPEN, 1.0),
    ],
)
def test_valve_actions_drive_the_valve_to_the_configured_position(verb, fail_action, expected):
    valve = ControlValve("XV-1")
    valve.set_position_target(0.5)
    valve.position = 0.5

    if fail_action is not None:
        valve.fail_action = fail_action

    engine, trips, trigger = build([valve], interlock("XS-1", [f"XV-1.{verb}"]))

    trip(engine, trips, trigger)
    run(engine, trips, 30.0)

    assert valve.position_target == pytest.approx(expected)
    assert valve.position == pytest.approx(expected)


def test_one_trip_takes_every_action_it_names():
    pump = running_machine(CentrifugalPump)
    valve = ControlValve("XV-1")
    engine, trips, trigger = build([pump, valve], interlock("XS-1", ["M-1.stop", "XV-1.close"]))

    trip(engine, trips, trigger)
    run(engine, trips, 30.0)

    assert not pump.running
    assert valve.position == pytest.approx(valve.min_position)


def test_the_trip_delay_is_the_recovery_window():
    pump = running_machine(CentrifugalPump)
    engine, trips, trigger = build([pump], interlock("XS-1", ["M-1.stop"], delay_s=10.0))
    run(engine, trips, 30.0)

    # 1.0 -> 0.5 at 0.05/s is ten seconds of stroke; the condition then holds
    # for about five seconds and clears again, inside the ten-second window.
    trigger.set_position_target(0.3)
    run(engine, trips, 12.0)
    trigger.set_position_target(1.0)
    run(engine, trips, 12.0)

    assert trips.tripped == ()
    assert pump.running


# Every action routes through command arbitration


def test_a_trip_action_is_an_interlock_demand_on_the_arbiter():
    pump = running_machine(CentrifugalPump)
    valve = ControlValve("XV-1")
    engine, trips, trigger = build([pump, valve], interlock("XS-1", ["M-1.stop", "XV-1.close"]))

    assert set(engine.arbiter.outputs) == {"M-1.run", "XV-1"}
    assert engine.arbiter.resolve("M-1.run") is None

    trip(engine, trips, trigger)

    for output in ("M-1.run", "XV-1"):
        resolution = engine.arbiter.resolve(output)
        assert resolution.source is Source.INTERLOCK
        assert resolution.requesters == ("XS-1",)

    assert engine.arbiter.resolve("M-1.run").value == 0.0


@pytest.mark.parametrize("machine_type", [CentrifugalPump, GasCompressor])
@pytest.mark.parametrize(
    ("starts_running", "demand", "ends_running"),
    [(False, 0.7, True), (True, 0.2, False)],
)
def test_the_run_output_switches_at_its_midpoint_and_never_raises(
    machine_type, starts_running, demand, ends_running,
):
    machine = machine_type("M-1")

    if starts_running:
        machine.start()

    engine, trips, _ = build([machine], interlock("XS-1", ["M-1.stop"]))

    engine.arbiter.demand("M-1.run", Source.OPERATOR, "console", demand)
    run(engine, trips, 5.0)

    assert machine.running is ends_running


@pytest.mark.parametrize("machine_type", [CentrifugalPump, GasCompressor])
def test_a_standing_run_demand_restarts_the_machine_when_the_trip_releases(machine_type):
    machine = running_machine(machine_type)
    engine, trips, trigger = build([machine], interlock("XS-1", ["M-1.stop"], reset="manual"))
    engine.arbiter.demand("M-1.run", Source.OPERATOR, "console", 1.0)
    run(engine, trips, 5.0)

    trip(engine, trips, trigger)
    clear(engine, trips, trigger)
    assert not machine.running

    assert trips.interlocks["XS-1"].reset()
    run(engine, trips, 10.0)

    assert machine.running
    assert target_of(machine) == pytest.approx(0.0)
    assert output_of(machine) == pytest.approx(0.0)


def position_not_a_number(monkeypatch, engine, trigger):
    monkeypatch.setattr(
        trigger,
        "get_state",
        lambda: {**ControlValve.get_state(trigger), "position": None},
    )


def position_not_published(monkeypatch, engine, trigger):
    def state():
        row = ControlValve.get_state(trigger)
        del row["position"]

        return row

    monkeypatch.setattr(trigger, "get_state", state)


def row_not_published(monkeypatch, engine, trigger):
    published = engine.snapshot

    def snapshot():
        full = published()
        equipment = {tag: row for tag, row in full.equipment.items() if tag != TRIGGER}

        return dataclasses.replace(full, equipment=MappingProxyType(equipment))

    monkeypatch.setattr(engine, "snapshot", snapshot)


@pytest.mark.parametrize(
    "lose_reading",
    [position_not_a_number, position_not_published, row_not_published],
)
def test_a_condition_that_loses_its_reading_fails_safe(monkeypatch, lose_reading):
    pump = running_machine(CentrifugalPump)
    engine, trips, trigger = build([pump], interlock("XS-1", ["M-1.stop"], delay_s=3.0))
    run(engine, trips, 5.0)
    assert trips.tripped == ()

    lose_reading(monkeypatch, engine, trigger)
    run(engine, trips, 2.0)
    assert trips.interlocks["XS-1"].state is InterlockState.PENDING

    run(engine, trips, 5.0)

    assert trips.tripped == ("XS-1",)
    assert not pump.running


# Trip overrides an operator command


def test_a_trip_overrides_an_operator_demand_and_hands_back_on_release():
    valve = ControlValve("XV-1")
    engine, trips, trigger = build([valve], interlock("XS-1", ["XV-1.close"]))
    engine.arbiter.demand("XV-1", Source.OPERATOR, "console", 0.8)
    run(engine, trips, 30.0)
    assert valve.position == pytest.approx(0.8)

    trip(engine, trips, trigger)
    engine.arbiter.demand("XV-1", Source.OPERATOR, "console", 0.9)
    run(engine, trips, 30.0)

    assert valve.position == pytest.approx(valve.min_position)

    clear(engine, trips, trigger)
    run(engine, trips, 30.0)

    assert trips.tripped == ()
    assert valve.position == pytest.approx(0.9)


def test_a_trip_overrides_an_operator_who_writes_the_device_directly():
    pump = running_machine(CentrifugalPump)
    valve = ControlValve("XV-1")
    engine, trips, trigger = build(
        [pump, valve],
        interlock("XS-1", ["M-1.stop", "XV-1.close"], reset="manual"),
    )
    run(engine, trips, 30.0)
    trip(engine, trips, trigger)
    run(engine, trips, 30.0)

    def operator():
        pump.start()
        pump.set_speed_target(1.0)
        valve.set_position_target(1.0)

    speeds = []
    positions = []

    for _ in range(20):
        run_operator_first(engine, trips, STEP, operator)
        speeds.append(pump.speed)
        positions.append(valve.position)

    assert not pump.running
    assert speeds == pytest.approx([0.0] * 20)
    assert positions == pytest.approx([valve.min_position] * 20)


def test_a_manual_trip_holds_until_reset_and_a_reset_restarts_nothing():
    pump = running_machine(CentrifugalPump)
    engine, trips, trigger = build([pump], interlock("XS-1", ["M-1.stop"], reset="manual"))
    run(engine, trips, 30.0)
    trip(engine, trips, trigger)
    clear(engine, trips, trigger)

    assert trips.tripped == ("XS-1",)
    assert engine.arbiter.resolve("M-1.run").source is Source.INTERLOCK

    assert trips.interlocks["XS-1"].reset()
    run(engine, trips, 30.0)

    assert engine.arbiter.resolve("M-1.run") is None
    assert not pump.running

    pump.start()
    pump.set_speed_target(1.0)
    run(engine, trips, 30.0)

    assert pump.running
    assert pump.speed == pytest.approx(1.0)


def test_a_paused_engine_advances_no_trip_delay():
    pump = running_machine(CentrifugalPump)
    engine, trips, trigger = build([pump], interlock("XS-1", ["M-1.stop"], delay_s=5.0))
    trigger.set_position_target(0.0)
    run(engine, trips, 12.0)
    assert trips.interlocks["XS-1"].state is InterlockState.PENDING

    engine.stop()
    run(engine, trips, 60.0)

    assert trips.interlocks["XS-1"].state is InterlockState.PENDING
    assert pump.running


def test_the_first_update_counts_time_since_construction():
    pump = running_machine(CentrifugalPump)
    engine, trips, trigger = build([pump], interlock("XS-1", ["M-1.stop"], delay_s=30.0))
    trigger.set_position_target(0.0)

    for _ in range(12):
        engine.step(STEP)

    trips.update(engine.snapshot())

    assert trips.interlocks["XS-1"].state is InterlockState.PENDING
    assert trips.interlocks["XS-1"].pending_elapsed == pytest.approx(12.0)


def test_an_older_snapshot_is_refused_before_any_interlock_moves():
    pump = running_machine(CentrifugalPump)
    engine, trips, trigger = build([pump], interlock("XS-1", ["M-1.stop"], delay_s=5.0))
    stale = engine.snapshot()
    trigger.set_position_target(0.0)
    run(engine, trips, 12.0)
    pending = trips.interlocks["XS-1"].pending_elapsed

    with pytest.raises(ValueError, match="older than"):
        trips.update(stale)

    assert trips.interlocks["XS-1"].pending_elapsed == pytest.approx(pending)


def test_a_condition_on_an_unpublished_variable_is_refused():
    plant = load_plant_file(PLANT_FILE)
    engine = Engine.from_plant(plant)
    entries = [
        *plant.passthrough("interlocks"),
        {
            "tag": "PSHH-102",
            "condition": "K-101.discharge_pressure >= 350.0",
            "delay_s": 2.0,
            "actions": ["K-101.stop"],
            "reset": "manual",
        },
    ]

    with pytest.raises(ValueError, match=r"PSHH-102: condition K-101\.discharge_pressure is not published"):
        TripSystem(
            load_interlocks({"interlocks": entries}),
            engine.equipment,
            CommandArbiter(),
            engine.snapshot(),
        )


# Configuration is checked up front


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("M-1", "malformed trip action"),
        ("M-9.stop", "unknown device 'M-9'"),
        ("M-1.close", "CentrifugalPump, which allows only ['stop']"),
        ("XV-1.stop", "ControlValve, which allows only ['close', 'fail', 'open']"),
    ],
)
def test_an_action_that_does_not_resolve_is_refused(action, message):
    with pytest.raises(ValueError, match="interlock XS-1") as raised:
        build([CentrifugalPump("M-1"), ControlValve("XV-1")], interlock("XS-1", [action]))

    assert message in str(raised.value)


def test_a_condition_on_an_unknown_device_is_refused_not_skipped():
    entry = {**interlock("XS-1", ["M-1.stop"]), "condition": "HV-999.position <= 0.5"}

    with pytest.raises(ValueError, match="condition names unknown device 'HV-999'"):
        build([CentrifugalPump("M-1")], entry)


def test_a_condition_on_a_field_that_is_not_a_number_is_refused():
    entry = {**interlock("XS-1", ["M-1.stop"]), "condition": "M-1.running >= 1"}

    with pytest.raises(ValueError, match="M-1.running is True, not a number"):
        build([running_machine(CentrifugalPump)], entry)


def test_two_interlocks_that_contradict_on_one_output_are_refused():
    with pytest.raises(ValueError, match="XV-1.open contradicts interlock XS-1's XV-1.close"):
        build(
            [ControlValve("XV-1")],
            interlock("XS-1", ["XV-1.close"]),
            interlock("XS-2", ["XV-1.open"]),
        )


def test_two_interlocks_that_agree_on_one_output_both_hold_it():
    valve = ControlValve("XV-1")
    engine, trips, trigger = build(
        [valve],
        interlock("XS-1", ["XV-1.close"]),
        interlock("XS-2", ["XV-1.fail"]),
    )

    trip(engine, trips, trigger)

    assert engine.arbiter.resolve("XV-1").requesters == ("XS-1", "XS-2")


def test_every_trippable_class_has_at_least_one_verb():
    assert all(TRIP_ACTIONS.values())


def test_every_valve_fail_action_has_a_trip_target():
    assert set(FAIL_TARGETS) == set(FAIL_ACTIONS)


# The reference plant, end to end


def reference_plant():
    plant = load_plant_file(PLANT_FILE)
    engine = Engine.from_plant(plant)
    trips = TripSystem.from_plant(plant, engine.equipment, engine.arbiter, engine.snapshot())
    engine.start()

    return engine, trips


def start_machines(engine, trips):
    for tag in ("P-101", "K-101"):
        engine.equipment[tag].start()

    run(engine, trips, 30.0)


def test_pshh_101_stops_k_101_when_its_discharge_runs_high():
    """K-101.outlet_pressure is N-204's solved pressure, published on K-101's
    row (T9-5). A discharge header pressed up past 350 psia carries N-204
    with it, and after its 2 s delay PSHH-101 latches and stops K-101."""
    engine, trips = reference_plant()
    start_machines(engine, trips)

    engine.topologies["gas"].node("N-202").set_boundary_pressure(400.0)
    run(engine, trips, 1.0)

    assert engine.snapshot().equipment["K-101"]["outlet_pressure"] >= 350.0
    assert trips.tripped == ()

    run(engine, trips, 3.0)

    assert trips.tripped == ("PSHH-101",)
    assert engine.equipment["K-101"].running is False


def test_the_reference_plant_runs_at_its_design_point_without_tripping():
    engine, trips = reference_plant()
    start_machines(engine, trips)
    run(engine, trips, 300.0)

    assert trips.tripped == ()
    assert engine.snapshot().equipment["V-101"]["level"] == pytest.approx(0.5, abs=0.01)


def test_a_trip_overrides_the_controller_on_a_loop_driven_valve():
    plant = load_plant_file(PLANT_FILE)

    engine = Engine.from_plant(plant)

    engine.start()
    engine.loops["PIC-101"].loop.mode = Mode.AUTO
    trips = TripSystem(
        load_interlocks(
            {
                "interlocks": [
                    {
                        "tag": "PSV-TEST",
                        "condition": "V-101.level >= 0.0",
                        "delay_s": 0.0,
                        "actions": ["PV-101.open"],
                        "reset": "auto",
                    },
                ],
            },
        ),
        engine.equipment,
        engine.arbiter,
        engine.snapshot(),
    )

    run(engine, trips, 30.0)

    assert engine.arbiter.resolve("PV-101").source is Source.INTERLOCK
    assert engine.equipment["PV-101"].position == pytest.approx(1.0)


def test_a_cascade_trip_propagates_through_the_plant():
    """The only scripted input is a malfunction that lets P-101 overfill
    V-101. LSHH-101 stops the pump; with the pump stopped the separator
    drains back through it and out through LV-101, and LSLL-101 latches on
    the level that falls - nothing here tells it to."""
    engine, trips = reference_plant()
    start_machines(engine, trips)

    registry = EquipmentRegistry()

    for device in engine.equipment.values():
        registry.register(device)

    malfunctions = MalfunctionRegistry(registry)
    malfunctions.add(Malfunction("P-101", "shutoff_pressure_rise", 40.0))

    order = []

    for _ in range(6000):
        snapshot = engine.snapshot()
        malfunctions.update(snapshot)
        trips.update(snapshot)

        for tag in trips.tripped:
            if tag not in order:
                order.append(tag)

        if len(order) == 2:
            break

        engine.step(STEP)

    assert order == ["LSHH-101", "LSLL-101"]

    pump = engine.equipment["P-101"]
    drain = engine.equipment["LV-101"]
    run(engine, trips, 30.0)

    assert not pump.running
    assert drain.position == pytest.approx(drain.min_position)
    assert engine.snapshot().equipment["V-101"]["level"] <= 0.1
