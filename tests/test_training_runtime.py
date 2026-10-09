"""
Plant runtime (T16-6): trips before each step, alarms after it.

Most tests run a small liquid plant, `XV-1` a valve that starts fully open and
`P-1` a pump behind it, because every number in it is one the test chooses: the
valve strokes 0.05 a second, so a limit and an interlock on its position put a
band crossing and a trip at a time the test can state. The end-to-end test at
the bottom runs the reference plant, where the trip is a real process variable.
"""

import threading
import time
from pathlib import Path

import pytest

from app.alarms.acknowledge import Acknowledged
from app.alarms.history import AcknowledgeRecord, ClearRecord
from app.alarms.manager import AlarmManager, EnvelopeEvent, Event, _alarm_id
from app.api.action import UnknownAction
from app.disturbances.malfunction import Malfunction, MalfunctionRegistry
from app.engine.engine import Engine
from app.envelope.evaluator import Severity
from app.equipment.registry import EquipmentRegistry
from app.plant.loader import load_plant, load_plant_file
from app.training.runtime import PlantRuntime

PLANT_FILE = Path(__file__).resolve().parent.parent / "config" / "plants" / "olefins_lite.yaml"

STEP = 1.0

VALVE_POINT = _alarm_id("XV-1", "position")


def plant_config(limits=(), interlocks=()):
    config = {
        "nodes": [
            {"id": "N-1", "boundary": True, "pressure": 50.0, "domain": "liquid"},
            {"id": "N-2", "boundary": True, "pressure": 50.0, "domain": "liquid"},
            {"id": "N-3", "boundary": True, "pressure": 50.0, "domain": "liquid"},
        ],
        "equipment": [
            {"tag": "P-1", "type": "pump", "node_in": "N-1", "node_out": "N-2", "design": {}},
            {"tag": "XV-1", "type": "control_valve", "node_in": "N-2", "node_out": "N-3", "design": {}},
        ],
    }

    if limits:
        config["limits"] = list(limits)

    if interlocks:
        config["interlocks"] = list(interlocks)

    return config


LIMITS = [{"tag": "XV-1", "variable": "position", "hi": 0.6, "hi_hi": 0.9}]

TRIP = {
    "tag": "XS-1",
    "condition": "XV-1.position >= 0.95",
    "delay_s": 5.0,
    "actions": ["P-1.stop"],
    "reset": "manual",
}


def build(limits=LIMITS, interlocks=(TRIP,), **kwargs):
    plant = load_plant(plant_config(limits, interlocks))
    runtime = PlantRuntime(Engine.from_plant(plant), plant, **kwargs)
    runtime.engine.start()
    runtime.act("P-1", "start", None)

    return runtime


def run(runtime, seconds):
    for _ in range(int(seconds / STEP)):
        runtime.step(STEP)


def kinds(runtime):
    return [type(entry).__name__ for entry in runtime.alarm_entries()]


# Trips


def test_a_condition_held_for_the_whole_delay_trips_and_the_pump_stops_through_the_arbiter():
    runtime = build()

    run(runtime, 4.0)

    assert runtime.trips.tripped == ()
    assert runtime.engine.equipment["P-1"].running

    run(runtime, 2.0)

    assert runtime.trips.tripped == ("XS-1",)
    assert not runtime.engine.equipment["P-1"].running
    assert runtime.engine.arbiter.resolve("P-1.run").requesters == ("XS-1",)


def test_a_recovery_inside_the_delay_prevents_the_trip():
    runtime = build()

    run(runtime, 2.0)
    runtime.act("XV-1", "set_position_target", 0.5)
    run(runtime, 20.0)

    assert runtime.trips.tripped == ()
    assert runtime.engine.equipment["P-1"].running


def test_a_manual_interlock_stays_tripped_until_reset_after_the_condition_clears():
    runtime = build()
    run(runtime, 6.0)
    assert runtime.trips.tripped == ("XS-1",)

    runtime.act("XS-1", "reset", None)
    run(runtime, 2.0)

    assert runtime.trips.tripped == ("XS-1",), "a reset while the condition holds changes nothing"
    assert [event.message for event in runtime.actions].count("XS-1 reset") == 1

    runtime.act("XV-1", "set_position_target", 0.5)
    run(runtime, 20.0)
    assert runtime.trips.tripped == ("XS-1",), "clearing the condition alone does not release it"
    assert runtime.engine.arbiter.resolve("P-1.run").requesters == ("XS-1",)

    runtime.act("XS-1", "reset", None)
    run(runtime, 1.0)

    assert runtime.trips.tripped == ()
    assert [event.message for event in runtime.actions].count("XS-1 reset") == 2


# Alarms


def test_a_plant_that_starts_outside_a_limit_raises_its_alarm_before_the_first_step():
    runtime = build()

    (event,) = runtime.alarm_entries()

    assert isinstance(event, Event)
    assert event.tag == "XV-1"
    assert event.id == VALVE_POINT
    assert "HIHI" in event.message
    assert event.sim_time == 0.0


def test_band_changes_are_recorded_once_and_a_return_to_normal_is_a_clear():
    runtime = build(interlocks=())
    runtime.act("XV-1", "set_position_target", 0.5)

    run(runtime, 20.0)

    # hihi at construction, hi when it drops below 0.9, then settled below 0.6.
    assert kinds(runtime) == ["Event", "Event", "ClearRecord"]
    first, second, clear = runtime.alarm_entries()
    assert "HIHI" in first.message
    assert "HI" in second.message and "HIHI" not in second.message
    assert clear.alarm_id == VALVE_POINT
    assert first.sim_time < second.sim_time < clear.sim_time

    run(runtime, 20.0)

    assert len(runtime.alarm_entries()) == 3


def test_steps_in_an_unchanged_band_record_nothing():
    runtime = build(interlocks=())

    run(runtime, 30.0)

    assert kinds(runtime) == ["Event"], "only the alarm raised at construction"


def test_the_alarm_pipeline_names_a_point_by_its_variable_with_spaces():
    runtime = build(interlocks=())

    (event, *_) = runtime.alarm_entries()

    assert event.message.startswith("XV-1 position")


def test_alarm_events_are_the_alarm_managers_own_events():
    manager = AlarmManager()
    runtime = build(interlocks=(), alarms=manager)

    assert manager.active()[0].active
    assert runtime.alarms is manager


# Acknowledge


def test_acknowledge_reports_recorded_already_unknown_and_no_event():
    runtime = build(interlocks=())

    assert runtime.acknowledge(VALVE_POINT) is Acknowledged.RECORDED
    assert runtime.acknowledge(VALVE_POINT) is Acknowledged.ALREADY
    assert runtime.acknowledge("no-such-alarm") is Acknowledged.UNKNOWN

    (*_, ack) = runtime.alarm_entries()
    assert isinstance(ack, AcknowledgeRecord)
    assert ack.tag == "XV-1"


def test_acknowledge_of_an_alarm_history_never_recorded_leaves_it_unacknowledged():
    manager = AlarmManager()
    manager.evaluate([EnvelopeEvent("Z-1", "level", Severity.ALARM, "hi")], 0.0)
    runtime = build(interlocks=(), alarms=manager)
    drifted = _alarm_id("Z-1", "level")

    assert runtime.acknowledge(drifted) is Acknowledged.NO_EVENT
    assert manager.is_acknowledged(drifted) is False


def test_acknowledge_is_stamped_with_the_plants_sim_time():
    runtime = build(interlocks=())
    run(runtime, 7.0)

    runtime.acknowledge(VALVE_POINT)

    (*_, ack) = runtime.alarm_entries()
    assert ack.sim_time == pytest.approx(7.0)


def test_a_cleared_alarm_that_was_never_acknowledged_can_still_be_acknowledged():
    runtime = build(interlocks=())
    runtime.act("XV-1", "set_position_target", 0.5)
    run(runtime, 20.0)
    assert any(isinstance(entry, ClearRecord) for entry in runtime.alarm_entries())

    assert runtime.acknowledge(VALVE_POINT) is Acknowledged.RECORDED


class BlockingManager(AlarmManager):
    """Holds the first acknowledge until released, counting every lookup."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lookups = 0
        self._guard = threading.Lock()

    def is_acknowledged(self, alarm_id):
        with self._guard:
            self.lookups += 1

        return super().is_acknowledged(alarm_id)

    def acknowledge(self, alarm_id, sim_time):
        self.entered.set()
        assert self.release.wait(timeout=5.0)
        super().acknowledge(alarm_id, sim_time)


def test_concurrent_acknowledges_record_once_and_a_step_waits_for_them():
    manager = BlockingManager()
    runtime = build(interlocks=(), alarms=manager)
    outcomes = []

    def acknowledge():
        outcomes.append(runtime.acknowledge(VALVE_POINT))

    first = threading.Thread(target=acknowledge)
    first.start()
    assert manager.entered.wait(timeout=5.0)

    second = threading.Thread(target=acknowledge)
    stepper = threading.Thread(target=lambda: runtime.step(STEP))
    second.start()
    stepper.start()

    try:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and second.is_alive() and stepper.is_alive():
            time.sleep(0.01)

        assert manager.lookups == 1, "the second acknowledge never reached the manager"
        assert second.is_alive()
        assert stepper.is_alive()
        assert runtime.engine.clock.sim_time == 0.0, "the step has not run"
    finally:
        manager.release.set()

    for thread in (first, second, stepper):
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    assert sorted(outcome.value for outcome in outcomes) == ["already", "recorded"]
    acks = [entry for entry in runtime.alarm_entries() if isinstance(entry, AcknowledgeRecord)]
    assert len(acks) == 1
    assert runtime.engine.clock.sim_time == pytest.approx(STEP)


# Operator actions


def test_a_device_action_is_applied_and_logged():
    runtime = build()

    runtime.act("XV-1", "set_position_target", 0.25)

    assert runtime.engine.equipment["XV-1"].position_target == pytest.approx(0.25)
    assert runtime.actions.events[-1].message == "XV-1 set_position_target 0.25"


@pytest.mark.parametrize(
    ("target", "action", "value", "error"),
    [
        ("NOPE-1", "start", None, KeyError),
        ("P-1", "explode", None, UnknownAction),
        ("P-1", "set_speed_target", None, ValueError),
        ("XS-1", "start", None, UnknownAction),
        ("XS-1", "reset", 1.0, ValueError),
    ],
)
def test_a_refused_action_raises_and_logs_nothing(target, action, value, error):
    runtime = build()
    logged = len(runtime.actions)

    with pytest.raises(error):
        runtime.act(target, action, value)

    assert len(runtime.actions) == logged


def test_an_action_between_steps_is_seen_by_the_next_trip_evaluation():
    # The condition reads the target an operator writes, which changes the
    # moment act() returns. A trip system still looking at the snapshot the
    # last step published would see 1.0 and miss a close the operator had
    # already made.
    interlock = {**TRIP, "condition": "XV-1.position_target <= 0.5", "delay_s": 0.0}
    runtime = build(interlocks=(interlock,))
    runtime.step(STEP)
    assert runtime.trips.tripped == ()

    runtime.act("XV-1", "set_position_target", 0.0)
    runtime.step(STEP)

    assert runtime.trips.tripped == ("XS-1",)


# Determinism


def test_with_no_limits_and_no_interlocks_it_steps_exactly_like_a_bare_engine():
    config = plant_config()
    bare = Engine.from_plant(load_plant(config))
    plant = load_plant(config)
    wrapped = PlantRuntime(Engine.from_plant(plant), plant)

    for engine in (bare, wrapped.engine):
        engine.start()

    bare.equipment["P-1"].start()
    wrapped.act("P-1", "start", None)
    bare.equipment["XV-1"].set_position_target(0.4)
    wrapped.act("XV-1", "set_position_target", 0.4)

    for _ in range(60):
        expected = bare.step(STEP).as_dict()

        assert wrapped.step(STEP).as_dict() == expected

    assert wrapped.alarm_entries() == ()


# The reference plant, end to end


def test_overfilling_the_separator_raises_alarms_then_trips_the_feed_pump():
    plant = load_plant_file(PLANT_FILE)

    runtime = PlantRuntime.from_plant(plant)

    runtime.engine.start()

    for tag in ("P-101", "K-101"):
        runtime.act(tag, "start", None)

    for _ in range(30):
        runtime.step(STEP)

    registry = EquipmentRegistry()

    for device in runtime.engine.equipment.values():
        registry.register(device)

    malfunctions = MalfunctionRegistry(registry)
    malfunctions.add(Malfunction("P-101", "shutoff_pressure_rise", 40.0))

    for _ in range(6000):
        malfunctions.update(runtime.snapshot())
        runtime.step(STEP)

        if "LSHH-101" in runtime.trips.tripped:
            break

    assert runtime.trips.tripped == ("LSHH-101",)
    assert runtime.snapshot().equipment["V-101"]["level"] >= 0.9
    assert not runtime.engine.equipment["P-101"].running

    # P-101 is built stopped and backflows until it is started, so its flow
    # alarm comes first; the vessel's own alarms follow it.
    raised = [entry for entry in runtime.alarm_entries() if isinstance(entry, Event)]
    assert raised[0].message.startswith("P-101 flow")
    vessel = [event for event in raised if event.message.startswith("V-101")]
    assert len(vessel) >= 2
    assert any("HIHI" in event.message for event in vessel)
