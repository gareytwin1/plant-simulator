"""
Objective evaluator - T14-3, contract C8.

The build-plan tests come first: the success path, the failure and timeout
paths, and a hold duration that resets when the variable leaves the band.
The rest pin ending precedence, latching, atomicity and config validation.
"""

import pytest

from app.engine.snapshot import build_snapshot
from app.scenarios.objectives import (
    Objective,
    ObjectiveEvaluator,
    ObjectiveStatus,
)
from app.scenarios.triggers import Condition, ConditionEvaluationError, ConditionSyntaxError

PENDING = ObjectiveStatus.PENDING
SUCCEEDED = ObjectiveStatus.SUCCEEDED
FAILED = ObjectiveStatus.FAILED
TIMED_OUT = ObjectiveStatus.TIMED_OUT


def compressor_snapshot(sim_time, discharge_pressure=480.0, tripped=False):
    return build_snapshot(
        sim_time=sim_time,
        speed=1.0,
        running=True,
        equipment={"K-101": {"discharge_pressure": discharge_pressure, "tripped": tripped}},
        nodes={},
        controllers={},
    )


def recover_config(hold=None, failure="K-101.tripped"):
    config = {"id": "recover", "success": {"condition": "K-101.discharge_pressure >= 450"}}

    if hold is not None:
        config["success"]["hold_duration_s"] = hold

    if failure is not None:
        config["failure"] = {"condition": failure}

    return config


def status_of(results, objective_id="recover"):
    return next(r.status for r in results if r.id == objective_id)


def evaluator(hold=None, failure="K-101.tripped", time_limit_s=None):
    return ObjectiveEvaluator.from_config([recover_config(hold, failure)], time_limit_s)


# --- success ---


def test_success_without_a_hold_ends_on_the_first_step_the_condition_holds():
    ev = evaluator()

    assert status_of(ev.evaluate(compressor_snapshot(0.0, 400.0))) == PENDING

    results = ev.evaluate(compressor_snapshot(1.0, 460.0))

    assert status_of(results) == SUCCEEDED
    assert results[0].ended_at == pytest.approx(1.0)


def test_success_with_a_hold_waits_out_the_duration():
    ev = evaluator(hold=300)

    assert status_of(ev.evaluate(compressor_snapshot(10.0, 460.0))) == PENDING
    assert status_of(ev.evaluate(compressor_snapshot(309.9, 460.0))) == PENDING

    results = ev.evaluate(compressor_snapshot(310.0, 460.0))

    assert status_of(results) == SUCCEEDED
    assert results[0].ended_at == pytest.approx(310.0)


# --- failure and timeout ---


def test_failure_condition_ends_the_objective_failed():
    ev = evaluator(hold=300)

    ev.evaluate(compressor_snapshot(0.0, 460.0))
    results = ev.evaluate(compressor_snapshot(5.0, 460.0, tripped=True))

    assert status_of(results) == FAILED
    assert results[0].ended_at == pytest.approx(5.0)


def test_objective_times_out_at_the_scenario_limit():
    ev = evaluator(time_limit_s=600)

    assert status_of(ev.evaluate(compressor_snapshot(599.9, 300.0))) == PENDING

    results = ev.evaluate(compressor_snapshot(600.0, 300.0))

    assert status_of(results) == TIMED_OUT
    assert results[0].ended_at == pytest.approx(600.0)


def test_no_time_limit_means_no_timeout():
    ev = evaluator(time_limit_s=None)

    assert status_of(ev.evaluate(compressor_snapshot(1e9, 300.0))) == PENDING


def test_hold_still_running_at_the_limit_times_out():
    ev = evaluator(hold=300, time_limit_s=200)

    ev.evaluate(compressor_snapshot(0.0, 460.0))

    assert status_of(ev.evaluate(compressor_snapshot(200.0, 460.0))) == TIMED_OUT


# --- hold resets ---


def test_hold_resets_when_the_variable_leaves_the_band():
    ev = evaluator(hold=100)

    ev.evaluate(compressor_snapshot(0.0, 460.0))
    ev.evaluate(compressor_snapshot(90.0, 460.0))
    assert status_of(ev.evaluate(compressor_snapshot(95.0, 400.0))) == PENDING

    # back in band at 100: the 100 s hold restarts here, not at 0
    assert status_of(ev.evaluate(compressor_snapshot(100.0, 460.0))) == PENDING
    assert status_of(ev.evaluate(compressor_snapshot(199.9, 460.0))) == PENDING
    assert status_of(ev.evaluate(compressor_snapshot(200.0, 460.0))) == SUCCEEDED


# --- precedence, latching, atomicity ---


def test_failure_outranks_success_and_timeout_on_the_same_step():
    ev = evaluator(time_limit_s=10)

    results = ev.evaluate(compressor_snapshot(10.0, 460.0, tripped=True))

    assert status_of(results) == FAILED


def test_success_outranks_timeout_on_the_same_step():
    ev = evaluator(time_limit_s=10)

    assert status_of(ev.evaluate(compressor_snapshot(10.0, 460.0))) == SUCCEEDED


def test_ending_latches():
    ev = evaluator(time_limit_s=100)

    ev.evaluate(compressor_snapshot(1.0, 460.0))
    later = ev.evaluate(compressor_snapshot(2.0, 100.0, tripped=True))

    assert status_of(later) == SUCCEEDED
    assert later[0].ended_at == pytest.approx(1.0)


def test_an_ended_objective_is_no_longer_evaluated():
    ev = evaluator()
    ev.evaluate(compressor_snapshot(1.0, 460.0))
    gone = build_snapshot(2.0, 1.0, True, {}, {}, {})

    assert status_of(ev.evaluate(gone)) == SUCCEEDED


def test_resolved_once_every_objective_has_ended():
    ev = ObjectiveEvaluator.from_config(
        [
            {"id": "a", "success": {"condition": "K-101.discharge_pressure >= 450"}},
            {"id": "b", "success": {"condition": "K-101.discharge_pressure >= 500"}},
        ],
    )

    ev.evaluate(compressor_snapshot(1.0, 460.0))
    assert not ev.resolved

    ev.evaluate(compressor_snapshot(2.0, 510.0))
    assert ev.resolved


def test_a_raising_step_commits_nothing():
    ev = ObjectiveEvaluator.from_config(
        [
            {"id": "a", "success": {"condition": "K-101.discharge_pressure >= 450"}},
            {"id": "b", "success": {"condition": "K-999.tripped"}},
        ],
    )

    with pytest.raises(ConditionEvaluationError):
        ev.evaluate(compressor_snapshot(1.0, 460.0))

    assert not ev.resolved
    assert ev._ended == {}
    assert ev._held_since == {}


def test_validate_raises_for_an_unknown_tag_up_front():
    ev = ObjectiveEvaluator.from_config(
        [{"id": "a", "success": {"condition": "K-101.tripped"}, "failure": {"condition": "K-999.tripped"}}],
    )

    with pytest.raises(ConditionEvaluationError, match="K-999"):
        ev.validate(compressor_snapshot(0.0))


def test_objectives_are_independent():
    ev = ObjectiveEvaluator.from_config(
        [recover_config(), {"id": "other", "success": {"condition": "K-101.discharge_pressure >= 500"}}],
        time_limit_s=50,
    )

    results = ev.evaluate(compressor_snapshot(50.0, 460.0))

    assert [(r.id, r.status) for r in results] == [("recover", SUCCEEDED), ("other", TIMED_OUT)]


# --- construction ---


def test_duplicate_objective_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate objective id 'recover'"):
        ObjectiveEvaluator.from_config([recover_config(), recover_config()])


@pytest.mark.parametrize("limit", [-1.0, float("nan"), float("inf")])
def test_bad_time_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="time_limit_s"):
        ObjectiveEvaluator([], limit)


def test_from_config_compiles_conditions_once():
    objective = Objective.from_config(recover_config(hold=60))

    assert objective.success == Condition.parse("K-101.discharge_pressure >= 450")
    assert objective.failure == Condition.parse("K-101.tripped")
    assert objective.hold_duration_s == pytest.approx(60.0)


def test_failure_is_optional_and_hold_defaults_to_zero():
    objective = Objective.from_config(recover_config(failure=None))

    assert objective.failure is None
    assert objective.hold_duration_s == pytest.approx(0.0)


@pytest.mark.parametrize(
    "config, message",
    [
        ({"success": {"condition": "K-101.tripped"}}, "missing 'id'"),
        ({"id": "", "success": {"condition": "K-101.tripped"}}, "non-empty string"),
        ({"id": "x"}, "missing 'success'"),
        ({"id": "x", "success": "K-101.tripped"}, "not an object"),
        ({"id": "x", "success": {}}, "no 'condition'"),
        ({"id": "x", "success": {"condition": 5}}, "not a string"),
        ({"id": "x", "success": {"condition": "K-101.tripped", "extra": 1}}, "unexpected key"),
        ({"id": "x", "success": {"condition": "K-101.tripped"}, "extra": 1}, "unexpected key"),
        ({"id": "x", "success": {"condition": "K-101.tripped", "hold_duration_s": -1}}, "negative"),
        ({"id": "x", "success": {"condition": "K-101.tripped", "hold_duration_s": "5"}}, "not a number"),
        ({"id": "x", "success": {"condition": "K-101.tripped", "hold_duration_s": True}}, "not a number"),
        ({"id": "x", "success": {"condition": "K-101.tripped", "hold_duration_s": float("inf")}}, "not finite"),
        ({"id": "x", "success": {"condition": "K-101.tripped", "hold_duration_s": 10**400}}, "too large"),
        ({"id": "x", "success": {"condition": "K-101.tripped"}, "failure": None}, "not an object"),
        ({"id": "x", "success": {"condition": "K-101.tripped"}, "failure": {"condition": "K-101.tripped", "hold_duration_s": 1}}, "unexpected key"),
    ],
)
def test_malformed_objective_config_is_rejected_specifically(config, message):
    with pytest.raises(ValueError, match=message):
        Objective.from_config(config)


def test_malformed_condition_string_raises_a_syntax_error():
    with pytest.raises(ConditionSyntaxError):
        Objective.from_config({"id": "x", "success": {"condition": "not a condition"}})


def test_a_scenario_with_no_objectives_is_vacuously_resolved():
    ev = ObjectiveEvaluator.from_config([], time_limit_s=10)

    assert ev.resolved
    assert ev.evaluate(compressor_snapshot(0.0)) == ()
