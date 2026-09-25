"""
Command arbitration (T7-4).

The arbiter is checked twice: once against a recording actuator, where the
decision itself is visible, and once bound to a real ControlValve, where the
decision has to survive as a stroked position. Precedence is only worth
having if it is independent of arrival order, so that claim is checked over
every permutation of posts and releases rather than one hand-picked sequence.
"""

import itertools
import math

import pytest

from app.controls.arbitration import (
    PRECEDENCE,
    CommandArbiter,
    ConflictingDemand,
    Source,
)
from app.equipment.valve import ControlValve


OUTPUT = "FV-101.position"


def recording_arbiter() -> tuple[CommandArbiter, list[float]]:
    arbiter = CommandArbiter()
    written: list[float] = []
    arbiter.bind(OUTPUT, written.append)

    return arbiter, written


def test_precedence_is_interlock_then_operator_then_controller():
    assert PRECEDENCE == (
        Source.INTERLOCK,
        Source.OPERATOR,
        Source.CONTROLLER,
    )


def test_interlock_demand_overrides_an_operator_command():
    arbiter, written = recording_arbiter()

    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.8)
    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", 0.1)
    arbiter.apply()

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.source is Source.INTERLOCK
    assert resolution.value == pytest.approx(0.1)
    assert written == [pytest.approx(0.1)]


def test_an_operator_repost_cannot_break_through_a_held_interlock():
    arbiter, written = recording_arbiter()

    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", 0.1)

    for value in (0.5, 0.9, 1.0):
        arbiter.demand(OUTPUT, Source.OPERATOR, "console", value)
        arbiter.apply()

    assert written == [pytest.approx(0.1)] * 3


def test_operator_override_beats_the_controller_in_manual():
    arbiter, written = recording_arbiter()

    arbiter.demand(OUTPUT, Source.CONTROLLER, "FIC-101", 0.3)
    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.7)
    arbiter.apply()

    arbiter.release(OUTPUT, Source.OPERATOR, "console")
    arbiter.apply()

    assert written == [pytest.approx(0.7), pytest.approx(0.3)]


def test_releasing_the_interlock_falls_back_to_the_next_source():
    arbiter, written = recording_arbiter()

    arbiter.demand(OUTPUT, Source.CONTROLLER, "FIC-101", 0.3)
    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.7)
    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", 0.1)
    arbiter.apply()

    arbiter.release(OUTPUT, Source.INTERLOCK, "I-101")
    arbiter.apply()

    arbiter.release(OUTPUT, Source.OPERATOR, "console")
    arbiter.apply()

    assert written == [
        pytest.approx(0.1),
        pytest.approx(0.7),
        pytest.approx(0.3),
    ]


POSTS = (
    (Source.CONTROLLER, "FIC-101", 0.3),
    (Source.OPERATOR, "console", 0.7),
    (Source.INTERLOCK, "I-101", 0.1),
    (Source.INTERLOCK, "I-102", 0.1),
)


@pytest.mark.parametrize("order", list(itertools.permutations(POSTS)))
def test_precedence_is_stable_regardless_of_arrival_order(order):
    arbiter, written = recording_arbiter()

    for source, requester, value in order:
        arbiter.demand(OUTPUT, source, requester, value)

    arbiter.apply()

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.source is Source.INTERLOCK
    assert resolution.requesters == ("I-101", "I-102")
    assert written == [pytest.approx(0.1)]


@pytest.mark.parametrize(
    "order",
    list(itertools.permutations(POSTS[2:] + POSTS[1:2])),
)
def test_resolution_after_releases_is_independent_of_release_order(order):
    arbiter, _ = recording_arbiter()

    for source, requester, value in POSTS:
        arbiter.demand(OUTPUT, source, requester, value)

    for source, requester, _value in order:
        arbiter.release(OUTPUT, source, requester)

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.source is Source.CONTROLLER
    assert resolution.value == pytest.approx(0.3)


def test_one_trip_resetting_leaves_another_trip_in_force():
    arbiter, _ = recording_arbiter()

    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.9)
    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", 0.1)
    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-102", 0.1)

    arbiter.release(OUTPUT, Source.INTERLOCK, "I-101")

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.source is Source.INTERLOCK
    assert resolution.requesters == ("I-102",)


def test_disagreeing_requesters_of_one_source_are_rejected():
    arbiter, _ = recording_arbiter()

    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", 0.1)

    with pytest.raises(ConflictingDemand, match="I-101"):
        arbiter.demand(OUTPUT, Source.INTERLOCK, "I-102", 1.0)

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.requesters == ("I-101",)


def test_a_requester_may_replace_its_own_demand():
    arbiter, _ = recording_arbiter()

    arbiter.demand(OUTPUT, Source.CONTROLLER, "FIC-101", 0.3)
    arbiter.demand(OUTPUT, Source.CONTROLLER, "FIC-101", 0.4)

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.value == pytest.approx(0.4)


def test_an_undemanded_output_is_left_alone():
    arbiter, written = recording_arbiter()

    arbiter.apply()
    assert arbiter.resolve(OUTPUT) is None

    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.6)
    arbiter.release(OUTPUT, Source.OPERATOR, "console")
    arbiter.apply()

    assert written == []


def test_releasing_a_demand_that_is_not_held_is_a_no_op():
    arbiter, _ = recording_arbiter()

    arbiter.release(OUTPUT, Source.INTERLOCK, "I-101")

    assert arbiter.resolve(OUTPUT) is None


def test_an_unbound_output_is_rejected():
    arbiter, _ = recording_arbiter()

    with pytest.raises(KeyError, match="FV-999"):
        arbiter.demand("FV-999.position", Source.OPERATOR, "console", 0.5)

    with pytest.raises(KeyError, match="FV-999"):
        arbiter.resolve("FV-999.position")


def test_an_output_is_bound_only_once():
    arbiter, _ = recording_arbiter()

    with pytest.raises(ValueError, match="already bound"):
        arbiter.bind(OUTPUT, lambda value: None)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_demand_is_rejected(value):
    arbiter, _ = recording_arbiter()

    with pytest.raises(ValueError, match="non-finite"):
        arbiter.demand(OUTPUT, Source.OPERATOR, "console", value)

    assert arbiter.resolve(OUTPUT) is None


def test_an_unknown_source_is_rejected():
    arbiter, _ = recording_arbiter()

    with pytest.raises(ValueError):
        arbiter.demand(OUTPUT, "scenario", "S-1", 0.5)


def test_resolving_does_not_write_to_the_device():
    arbiter, written = recording_arbiter()

    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.6)
    arbiter.resolve(OUTPUT)
    arbiter.resolve(OUTPUT)

    assert written == []


def test_the_arbitrated_value_strokes_a_real_valve():
    valve = ControlValve("FV-101")
    valve.stroke_rate = 1.0
    arbiter = CommandArbiter()
    arbiter.bind(OUTPUT, valve.set_position_target)

    arbiter.demand(OUTPUT, Source.CONTROLLER, "FIC-101", 0.4)
    arbiter.demand(OUTPUT, Source.OPERATOR, "console", 0.8)
    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", valve.fail_position)
    arbiter.apply()
    valve.integrate(2.0)

    assert valve.position == pytest.approx(valve.fail_position)

    arbiter.release(OUTPUT, Source.INTERLOCK, "I-101")
    arbiter.apply()
    valve.integrate(2.0)

    assert valve.position == pytest.approx(0.8)


def test_requesters_agreeing_up_to_float_rounding_are_not_a_conflict():
    arbiter, _ = recording_arbiter()

    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-101", 0.1)
    arbiter.demand(OUTPUT, Source.INTERLOCK, "I-102", 1.0 - 0.9)

    resolution = arbiter.resolve(OUTPUT)
    assert resolution is not None
    assert resolution.requesters == ("I-101", "I-102")
    assert resolution.value == pytest.approx(0.1)


def test_a_failing_actuator_does_not_stop_the_other_outputs():
    arbiter = CommandArbiter()
    written: list[float] = []

    def faulty(value: float) -> None:
        raise RuntimeError("actuator fault")

    arbiter.bind("FV-101.position", faulty)
    arbiter.bind("FV-102.position", written.append)
    arbiter.demand("FV-101.position", Source.INTERLOCK, "I-101", 0.1)
    arbiter.demand("FV-102.position", Source.INTERLOCK, "I-101", 0.1)

    with pytest.raises(ExceptionGroup) as raised:
        arbiter.apply()

    assert raised.group_contains(RuntimeError, match="actuator fault")
    assert written == [pytest.approx(0.1)]
