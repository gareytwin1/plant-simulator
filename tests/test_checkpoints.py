"""Public state accessors - T12-5.

Each of `PID`, `Loop`, `CommandArbiter`, `Evaluator`, `ExcursionTracker` and
`Engine` saves and restores its own slow state through `checkpoint()`,
`validate_checkpoint()` and `restore_checkpoint()`. These tests pin each
class on its own, without `persistence.py`: a checkpoint round-trips, it does
not alias live state, and a refused one leaves the object untouched and names
the field relative to the object.
"""

import dataclasses

import pytest

from app.controls.arbitration import ArbiterCheckpoint, CommandArbiter, Source
from app.controls.modes import Loop, Mode
from app.controls.pid import PID, Action
from app.engine.engine import Engine
from app.envelope.evaluator import Band, Evaluator, Limits, Severity
from app.envelope.tracker import Excursion, ExcursionTracker
from app.equipment.vessel import Vessel
from app.statetypes import StateError


LIMITS = Limits(warning_hi=0.8, alarm_hi=0.9)


def worked_pid():
    pid = PID(kp=2.0, ki=0.5, kd=0.1, output_min=0.0, output_max=1.0, setpoint=5.0)

    for measurement in (4.0, 4.5, 4.8):
        pid.compute(measurement, 1.0)

    return pid


def test_a_pid_checkpoint_resumes_exactly_on_a_fresh_pid():
    original = worked_pid()
    twin = PID(kp=0.0, ki=0.0, kd=0.0, output_min=0.0, output_max=9.0, action=Action.DIRECT)

    twin.restore_checkpoint(original.checkpoint())

    assert twin.checkpoint() == original.checkpoint()
    assert twin.compute(4.9, 1.0) == pytest.approx(original.compute(4.9, 1.0))


@pytest.mark.parametrize(
    ("change", "path", "message"),
    [
        ({"ki": -0.1}, "ki", "-0.1 is negative"),
        ({"output_min": 2.0, "output_max": 1.0}, "", "output_min 2.0 exceeds output_max 1.0"),
    ],
)
def test_a_pid_refuses_a_checkpoint_its_constructor_would_refuse(change, path, message):
    pid = worked_pid()
    before = pid.checkpoint()

    with pytest.raises(StateError, match=message) as refused:
        pid.restore_checkpoint(dataclasses.replace(before, **change))

    assert refused.value.path == path
    assert pid.checkpoint() == before


def test_a_loop_checkpoint_round_trips_its_mode_without_reseeding_manual_output():
    original = Loop(worked_pid(), Mode.MANUAL)
    original.manual_output = 0.4
    original.mode = Mode.AUTO
    original.compute(4.9, 1.0)

    twin = Loop(worked_pid(), Mode.MANUAL)
    twin.restore_checkpoint(original.checkpoint())

    assert twin.checkpoint() == original.checkpoint()
    assert twin.mode is Mode.AUTO
    assert twin.manual_output == pytest.approx(0.4)


def test_a_loop_names_a_refused_pid_field_relative_to_itself():
    loop = Loop(worked_pid(), Mode.AUTO)
    bad = dataclasses.replace(loop.checkpoint().pid, ki=-1.0)

    with pytest.raises(StateError) as refused:
        loop.restore_checkpoint(dataclasses.replace(loop.checkpoint(), pid=bad))

    assert refused.value.path == "pid.ki"


def bound_arbiter():
    arbiter = CommandArbiter()
    arbiter.bind("PV-101", lambda value: None)
    arbiter.bind("PV-102", lambda value: None)
    arbiter.demand("PV-101", Source.OPERATOR, "HS-1", 0.5)
    arbiter.demand("PV-102", Source.INTERLOCK, "XS-1", 0.0)

    return arbiter


def test_an_arbiter_checkpoint_replaces_every_held_demand():
    original = bound_arbiter()
    twin = bound_arbiter()
    twin.release("PV-101", Source.OPERATOR, "HS-1")
    twin.demand("PV-101", Source.CONTROLLER, "PIC-1", 0.9)

    twin.restore_checkpoint(original.checkpoint())

    assert twin.checkpoint() == original.checkpoint()
    assert twin.resolve("PV-101").source is Source.OPERATOR


def test_an_arbiter_checkpoint_does_not_alias_the_held_demands():
    arbiter = bound_arbiter()
    saved = arbiter.checkpoint()

    arbiter.demand("PV-101", Source.OPERATOR, "HS-1", 0.7)

    assert saved.demands["PV-101"][Source.OPERATOR] == {"HS-1": 0.5}


def test_an_arbiter_refuses_two_requesters_of_one_source_that_disagree():
    arbiter = bound_arbiter()
    before = arbiter.checkpoint()
    held = {source: {} for source in Source}
    held[Source.OPERATOR] = {"HS-1": 0.2, "HS-2": 0.8}

    with pytest.raises(StateError) as refused:
        arbiter.restore_checkpoint(ArbiterCheckpoint({"PV-101": held}))

    assert refused.value.path == "PV-101.operator.HS-2"
    assert arbiter.checkpoint() == before


def test_an_arbiter_refuses_an_output_it_does_not_bind():
    arbiter = bound_arbiter()

    with pytest.raises(StateError, match="PV-999"):
        arbiter.restore_checkpoint(ArbiterCheckpoint({"PV-999": {}}))


def held_evaluator():
    evaluator = Evaluator(LIMITS, deadband=0.01, on_delay=2.0)
    evaluator.evaluate(0.85, 3.0)
    evaluator.evaluate(0.95, 1.0)

    return evaluator


def test_an_evaluator_checkpoint_resumes_exactly_including_its_pending_escalation():
    original = held_evaluator()
    twin = Evaluator(LIMITS, deadband=0.01, on_delay=2.0)

    twin.restore_checkpoint(original.checkpoint())

    assert twin.checkpoint() == original.checkpoint()
    assert twin.checkpoint().pending == Band(Severity.ALARM, "hi", 0.9)
    assert twin.evaluate(0.95, 1.5) == original.evaluate(0.95, 1.5) == Severity.ALARM


@pytest.mark.parametrize(
    ("change", "path", "message"),
    [
        ({"band": Band(Severity.NORMAL, "hi", 0.8)}, "band.severity", "never NORMAL"),
        ({"pending": Band(Severity.NORMAL, "hi", 0.8)}, "pending.severity", "never NORMAL"),
        ({"band": Band(Severity.WARNING, "hi", 0.7)}, "band.threshold", r"0\.7 is not the configured warning_hi \(0\.8\)"),
        ({"pending": Band(Severity.TRIP, "hi", 1.0)}, "pending.threshold", r"not the configured trip_hi \(None\)"),
        ({"pending_elapsed": -1.0}, "pending_elapsed", "-1.0 is negative"),
    ],
)
def test_an_evaluator_refuses_a_checkpoint_it_could_never_have_held(change, path, message):
    evaluator = held_evaluator()
    before = evaluator.checkpoint()

    with pytest.raises(StateError, match=message) as refused:
        evaluator.restore_checkpoint(dataclasses.replace(before, **change))

    assert refused.value.path == path
    assert evaluator.checkpoint() == before


def worked_tracker():
    tracker = ExcursionTracker(LIMITS)

    for value, severity in ((0.85, Severity.WARNING), (0.95, Severity.ALARM), (0.5, Severity.NORMAL)):
        tracker.update(value, severity, 2.0)

    return tracker


def test_a_tracker_checkpoint_resumes_exactly():
    original = worked_tracker()
    twin = ExcursionTracker(LIMITS)

    twin.restore_checkpoint(original.checkpoint())

    assert twin.checkpoint() == original.checkpoint()
    assert twin.peak == original.peak
    assert twin.time_in(Severity.ALARM) == pytest.approx(2.0)


def test_a_tracker_checkpoint_does_not_alias_its_time_in_band():
    tracker = worked_tracker()
    saved = tracker.checkpoint()

    tracker.update(0.95, Severity.ALARM, 5.0)

    assert saved.time_in_band[Severity.ALARM] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"elapsed": -1.0}, "elapsed"),
        ({"time_in_band": {Severity.WARNING: 0.0, Severity.ALARM: -1.0, Severity.TRIP: 0.0}}, "time_in_band.ALARM"),
        ({"time_in_band": {Severity.WARNING: 0.0}}, "time_in_band"),
        ({"peak": Excursion(Severity.ALARM, -1.0, 1.0)}, "peak.magnitude"),
        ({"peak": Excursion(Severity.ALARM, 1.0, -1.0)}, "peak.timestamp"),
    ],
)
def test_a_tracker_refuses_a_checkpoint_no_update_could_produce(change, path):
    tracker = worked_tracker()
    before = tracker.checkpoint()

    with pytest.raises(StateError) as refused:
        tracker.restore_checkpoint(dataclasses.replace(before, **change))

    assert refused.value.path == path
    assert tracker.checkpoint() == before


def vessel_engine():
    return Engine(
        [Vessel("V-101")],
        limits={("V-101", "level"): Evaluator(Limits(warning_hi=0.8, alarm_hi=0.9))},
    )


def test_an_engine_checkpoint_round_trips_its_own_state():
    original = vessel_engine()
    twin = vessel_engine()
    saved = original.checkpoint()

    twin.restore_checkpoint(saved)

    assert twin.checkpoint() == saved


def test_an_engine_checkpoint_does_not_alias_the_engines_envelope_state():
    engine = vessel_engine()
    saved = engine.checkpoint()

    engine.restore_checkpoint(
        dataclasses.replace(
            saved,
            envelope_band={("V-101", "level"): (Severity.WARNING, "hi")},
            envelope_since={("V-101", "level"): 7.0},
        ),
    )

    assert saved.envelope_band[("V-101", "level")][0] is Severity.NORMAL
    assert engine.checkpoint().envelope_since == {("V-101", "level"): 7.0}


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"primed": frozenset({"PIC-999"})}, "primed"),
        ({"envelope_band": {}}, "envelope_band"),
        ({"envelope_since": {}}, "envelope_since"),
        ({"envelope_band": {("V-101", "level"): (Severity.ALARM, None)}}, "envelope_band.V-101.level"),
        ({"envelope_band": {("V-101", "level"): (Severity.NORMAL, "hi")}}, "envelope_band.V-101.level"),
    ],
)
def test_an_engine_refuses_a_checkpoint_that_does_not_fit_it(change, path):
    engine = vessel_engine()
    before = engine.checkpoint()

    with pytest.raises(StateError) as refused:
        engine.restore_checkpoint(dataclasses.replace(before, **change))

    assert refused.value.path == path
    assert engine.checkpoint() == before


def test_a_state_error_prefixes_its_path_and_keeps_the_reason():
    error = StateError("ki", "-1 is negative").within("loops.PIC-101.pid")

    assert str(error) == "loops.PIC-101.pid.ki: -1 is negative"
    assert StateError("", "bad").within("arbiter").path == "arbiter"
    assert str(StateError("", "bad")) == "bad"
