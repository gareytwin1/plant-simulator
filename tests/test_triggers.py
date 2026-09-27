"""
Trigger evaluator - T14-2, contract C8.

The build-plan tests come first: all three trigger types fire under the
right conditions, one-shot fires exactly once, and evaluating a trigger adds
no measurable per-step cost - pinned here as "a condition string compiles
exactly once, at construction, never again on evaluate". The rest pin the
one-shot bookkeeping, the "TAG.field [OP value]" grammar, and the errors a
malformed scenario or a malformed condition should raise.
"""

from unittest import mock

import pytest

from app.engine.snapshot import build_snapshot
from app.scenarios.triggers import (
    Condition,
    ConditionEvaluationError,
    ConditionSyntaxError,
    ConditionTrigger,
    OperatorActionTrigger,
    TimeTrigger,
    Trigger,
    TriggerEvaluator,
)
from app.scoring.actionlog import ActionLog


def snapshot_at(sim_time, equipment=None, nodes=None, controllers=None):
    return build_snapshot(
        sim_time=sim_time,
        speed=1.0,
        running=True,
        equipment=equipment or {},
        nodes=nodes or {},
        controllers=controllers or {},
    )


def compressor_snapshot(sim_time, discharge_pressure, tripped=False):
    return snapshot_at(
        sim_time,
        equipment={"K-101": {"discharge_pressure": discharge_pressure, "tripped": tripped}},
    )


# --- time triggers ---


def test_time_trigger_does_not_fire_before_sim_time():
    trigger = Trigger(id="t1", kind=TimeTrigger(sim_time=30.0))
    evaluator = TriggerEvaluator([trigger])

    fired = evaluator.evaluate(snapshot_at(29.9), ActionLog())

    assert fired == ()


def test_time_trigger_fires_once_sim_time_is_reached():
    trigger = Trigger(id="t1", kind=TimeTrigger(sim_time=30.0))
    evaluator = TriggerEvaluator([trigger])

    fired = evaluator.evaluate(snapshot_at(30.0), ActionLog())

    assert fired == ("t1",)


def test_time_trigger_without_one_shot_fires_on_every_later_step():
    trigger = Trigger(id="t1", kind=TimeTrigger(sim_time=30.0))
    evaluator = TriggerEvaluator([trigger])

    evaluator.evaluate(snapshot_at(30.0), ActionLog())
    second = evaluator.evaluate(snapshot_at(31.0), ActionLog())

    assert second == ("t1",)


def test_time_trigger_with_one_shot_fires_exactly_once():
    trigger = Trigger(id="t1", kind=TimeTrigger(sim_time=30.0), one_shot=True)
    evaluator = TriggerEvaluator([trigger])

    first = evaluator.evaluate(snapshot_at(30.0), ActionLog())
    second = evaluator.evaluate(snapshot_at(31.0), ActionLog())
    third = evaluator.evaluate(snapshot_at(3000.0), ActionLog())

    assert first == ("t1",)
    assert second == ()
    assert third == ()


# --- condition triggers ---


def test_condition_trigger_fires_when_comparison_holds():
    trigger = Trigger(
        id="high-discharge",
        kind=ConditionTrigger(Condition.parse("K-101.discharge_pressure > 900.0")),
    )
    evaluator = TriggerEvaluator([trigger])

    below = evaluator.evaluate(compressor_snapshot(0.0, discharge_pressure=899.0), ActionLog())
    above = evaluator.evaluate(compressor_snapshot(1.0, discharge_pressure=901.0), ActionLog())

    assert below == ()
    assert above == ("high-discharge",)


@pytest.mark.parametrize(
    ("expression", "value", "expected"),
    [
        ("K-101.discharge_pressure == 900.0", 900.0, True),
        ("K-101.discharge_pressure == 900.0", 900.1, False),
        ("K-101.discharge_pressure != 900.0", 900.1, True),
        ("K-101.discharge_pressure >= 900.0", 900.0, True),
        ("K-101.discharge_pressure <= 900.0", 900.0, True),
        ("K-101.discharge_pressure < 900.0", 899.9, True),
    ],
)
def test_every_comparison_operator_evaluates_correctly(expression, value, expected):
    condition = Condition.parse(expression)

    assert condition.is_met(compressor_snapshot(0.0, discharge_pressure=value)) is expected


def test_condition_trigger_bare_field_is_a_truthy_check():
    condition = Condition.parse("K-101.tripped")

    assert condition.is_met(compressor_snapshot(0.0, discharge_pressure=0.0, tripped=True)) is True
    assert condition.is_met(compressor_snapshot(0.0, discharge_pressure=0.0, tripped=False)) is False


@pytest.mark.parametrize(
    "expression",
    ["", "K-101", "K-101.discharge_pressure >", "K-101.discharge_pressure ~ 900", "900 > K-101.discharge_pressure"],
)
def test_malformed_condition_expression_rejected_at_parse(expression):
    with pytest.raises(ConditionSyntaxError):
        Condition.parse(expression)


def test_condition_raises_on_unknown_tag():
    condition = Condition.parse("V-999.level > 50.0")

    with pytest.raises(ConditionEvaluationError, match="V-999"):
        condition.is_met(compressor_snapshot(0.0, discharge_pressure=901.0))


def test_condition_raises_on_unknown_field():
    condition = Condition.parse("K-101.speed > 0.5")

    with pytest.raises(ConditionEvaluationError, match="speed"):
        condition.is_met(compressor_snapshot(0.0, discharge_pressure=901.0))


def test_condition_raises_comparing_a_non_numeric_field():
    condition = Condition.parse("K-101.tripped > 0.5")

    with pytest.raises(ConditionEvaluationError, match="not a number"):
        condition.is_met(compressor_snapshot(0.0, discharge_pressure=901.0, tripped=True))


def test_condition_raises_when_tag_is_ambiguous_across_sections():
    condition = Condition.parse("N-1.level > 0.0")
    snapshot = snapshot_at(
        0.0,
        equipment={"N-1": {"level": 1.0}},
        nodes={"N-1": {"level": 1.0}},
    )

    with pytest.raises(ConditionEvaluationError, match="ambiguous"):
        condition.is_met(snapshot)


# --- operator_action triggers ---


def test_operator_action_trigger_fires_after_the_matching_action_is_recorded():
    trigger = Trigger(id="ack", kind=OperatorActionTrigger(tag="K-101", action="stop"))
    evaluator = TriggerEvaluator([trigger])
    log = ActionLog()

    before = evaluator.evaluate(snapshot_at(0.0), log)
    log.record(tag="K-101", action="stop", value=None, sim_time=1.0)
    after = evaluator.evaluate(snapshot_at(1.0), log)

    assert before == ()
    assert after == ("ack",)


def test_operator_action_trigger_ignores_a_different_tag_or_action():
    trigger = Trigger(id="ack", kind=OperatorActionTrigger(tag="K-101", action="stop"))
    evaluator = TriggerEvaluator([trigger])
    log = ActionLog()

    log.record(tag="K-101", action="start", value=None, sim_time=0.0)
    log.record(tag="P-101", action="stop", value=None, sim_time=1.0)

    assert evaluator.evaluate(snapshot_at(1.0), log) == ()


def test_operator_action_trigger_with_one_shot_fires_exactly_once():
    trigger = Trigger(id="ack", kind=OperatorActionTrigger(tag="K-101", action="stop"), one_shot=True)
    evaluator = TriggerEvaluator([trigger])
    log = ActionLog()
    log.record(tag="K-101", action="stop", value=None, sim_time=1.0)

    first = evaluator.evaluate(snapshot_at(1.0), log)
    second = evaluator.evaluate(snapshot_at(2.0), log)

    assert first == ("ack",)
    assert second == ()


# --- from_config ---


def test_from_config_builds_all_three_trigger_types():
    evaluator = TriggerEvaluator.from_config(
        [
            {"id": "trip-onset", "type": "time", "sim_time": 30.0, "one_shot": True},
            {"id": "high-discharge", "type": "condition", "condition": "K-101.discharge_pressure > 900.0"},
            {"id": "operator-response", "type": "operator_action", "action": "K-101.stop"},
        ],
    )
    log = ActionLog()
    log.record(tag="K-101", action="stop", value=None, sim_time=30.0)

    fired = evaluator.evaluate(compressor_snapshot(30.0, discharge_pressure=901.0), log)

    assert set(fired) == {"trip-onset", "high-discharge", "operator-response"}


def test_from_config_rejects_unknown_trigger_type():
    with pytest.raises(ValueError, match="weather"):
        Trigger.from_config({"id": "t1", "type": "weather"})


def test_from_config_rejects_an_operator_action_without_a_dotted_tag():
    with pytest.raises(ConditionSyntaxError):
        Trigger.from_config({"id": "t1", "type": "operator_action", "action": "stop"})


def test_duplicate_trigger_ids_rejected():
    triggers = [
        Trigger(id="dup", kind=TimeTrigger(sim_time=1.0)),
        Trigger(id="dup", kind=TimeTrigger(sim_time=2.0)),
    ]

    with pytest.raises(ValueError, match="dup"):
        TriggerEvaluator(triggers)


def test_evaluate_returns_only_the_ids_that_fired_this_step():
    evaluator = TriggerEvaluator(
        [
            Trigger(id="early", kind=TimeTrigger(sim_time=10.0)),
            Trigger(id="late", kind=TimeTrigger(sim_time=100.0)),
        ],
    )

    fired = evaluator.evaluate(snapshot_at(50.0), ActionLog())

    assert fired == ("early",)


# --- no measurable per-step cost ---


def test_condition_is_compiled_once_and_never_reparsed_on_evaluate():
    # "Adds no measurable step cost" means the condition string is compiled
    # once, at construction - evaluate() is dict lookups and a comparison,
    # never a re-parse. Pinned as a call count rather than a wall-clock
    # timing assertion, which would flake with the environment.
    with mock.patch.object(Condition, "parse", wraps=Condition.parse) as parse:
        evaluator = TriggerEvaluator.from_config(
            [{"id": "high-discharge", "type": "condition", "condition": "K-101.discharge_pressure > 900.0"}],
        )
        assert parse.call_count == 1

        snapshot = compressor_snapshot(0.0, discharge_pressure=901.0)
        for step in range(1000):
            evaluator.evaluate(snapshot, ActionLog())

        assert parse.call_count == 1
