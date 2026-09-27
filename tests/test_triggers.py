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
    log = ActionLog()  # one log for the whole run, as a real caller keeps

    first = evaluator.evaluate(snapshot_at(30.0), log)
    second = evaluator.evaluate(snapshot_at(31.0), log)
    third = evaluator.evaluate(snapshot_at(3000.0), log)

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


def test_condition_trigger_bare_field_is_a_boolean_check():
    condition = Condition.parse("K-101.tripped")

    assert condition.is_met(compressor_snapshot(0.0, discharge_pressure=0.0, tripped=True)) is True
    assert condition.is_met(compressor_snapshot(0.0, discharge_pressure=0.0, tripped=False)) is False


def test_bare_condition_on_a_non_boolean_field_raises_rather_than_reading_as_always_true():
    condition = Condition.parse("K-101.discharge_pressure")

    with pytest.raises(ConditionEvaluationError, match="not true/false"):
        condition.is_met(compressor_snapshot(0.0, discharge_pressure=901.0))


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


@pytest.mark.parametrize("action", ["K-101.", ".stop"])
def test_from_config_rejects_an_operator_action_with_an_empty_tag_or_verb(action):
    with pytest.raises(ConditionSyntaxError):
        Trigger.from_config({"id": "t1", "type": "operator_action", "action": action})


@pytest.mark.parametrize(
    ("trigger_type", "config"),
    [
        ("time", {"id": "t1", "type": "time"}),
        ("condition", {"id": "t1", "type": "condition"}),
        ("operator_action", {"id": "t1", "type": "operator_action"}),
    ],
)
def test_from_config_names_the_trigger_id_when_its_type_specific_field_is_missing(trigger_type, config):
    with pytest.raises(ValueError, match="t1"):
        Trigger.from_config(config)


def test_from_config_rejects_a_non_boolean_one_shot():
    with pytest.raises(ValueError, match="t1"):
        Trigger.from_config({"id": "t1", "type": "time", "sim_time": 1.0, "one_shot": "false"})


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


# --- validate() catches a bad condition before the step loop ---


def test_validate_raises_for_a_bad_tag_before_any_evaluate_call():
    evaluator = TriggerEvaluator.from_config(
        [{"id": "typo", "type": "condition", "condition": "V-999.level > 50.0"}],
    )

    with pytest.raises(ConditionEvaluationError, match="V-999"):
        evaluator.validate(compressor_snapshot(0.0, discharge_pressure=0.0))


def test_validate_raises_for_a_misspelled_operator_action_tag():
    evaluator = TriggerEvaluator.from_config(
        [{"id": "typo", "type": "operator_action", "action": "K-1O1.stop"}],
    )

    with pytest.raises(ConditionEvaluationError, match="K-1O1"):
        evaluator.validate(compressor_snapshot(0.0, discharge_pressure=0.0))


def test_validate_passes_a_well_formed_scenario():
    evaluator = TriggerEvaluator.from_config(
        [
            {"id": "trip-onset", "type": "time", "sim_time": 30.0},
            {"id": "high-discharge", "type": "condition", "condition": "K-101.discharge_pressure > 900.0"},
            {"id": "operator-response", "type": "operator_action", "action": "K-101.stop"},
        ],
    )

    evaluator.validate(compressor_snapshot(0.0, discharge_pressure=0.0))  # does not raise


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


def test_operator_action_trigger_stops_scanning_the_log_once_it_has_matched():
    # The log is append-only, so a match can never un-happen. Once matched,
    # evaluate() must not keep rescanning it on every later step - that is
    # what would make per-step cost grow with scenario length.
    trigger = Trigger(id="ack", kind=OperatorActionTrigger(tag="K-101", action="stop"))
    evaluator = TriggerEvaluator([trigger])
    log = ActionLog()
    log.record(tag="K-101", action="stop", value=None, sim_time=0.0)

    original_is_met = OperatorActionTrigger.is_met
    calls = []

    def spy(self, snapshot, actions):
        calls.append(1)
        return original_is_met(self, snapshot, actions)

    with mock.patch.object(OperatorActionTrigger, "is_met", spy):
        for step in range(50):
            fired = evaluator.evaluate(snapshot_at(float(step)), log)
            assert fired == ("ack",)

    assert calls == [1]


def test_operator_action_trigger_only_rescans_when_the_log_has_grown():
    # An unmatched trigger still has to look eventually, but not on a step
    # where nothing new was recorded since it last looked - that is what
    # would make an unanswered trigger cost O(log length) every single step
    # for the rest of the scenario.
    trigger = Trigger(id="ack", kind=OperatorActionTrigger(tag="K-101", action="stop"))
    evaluator = TriggerEvaluator([trigger])
    log = ActionLog()
    log.record(tag="K-101", action="start", value=None, sim_time=0.0)  # no match

    original_is_met = OperatorActionTrigger.is_met
    calls = []

    def spy(self, snapshot, actions):
        calls.append(1)
        return original_is_met(self, snapshot, actions)

    with mock.patch.object(OperatorActionTrigger, "is_met", spy):
        for step in range(20):
            assert evaluator.evaluate(snapshot_at(float(step)), log) == ()

        assert calls == [1]  # one look at the unchanged log, then no more

        log.record(tag="K-101", action="stop", value=None, sim_time=20.0)
        fired = evaluator.evaluate(snapshot_at(20.0), log)

    assert fired == ("ack",)
    assert calls == [1, 1]  # the new entry earns exactly one more look


def test_a_fresh_action_log_is_not_read_through_the_previous_ones_latch():
    # A scenario reset or replay hands the evaluator a new ActionLog. A
    # trigger latched against the old one must not stay matched against a
    # log where its action never happened, and an unmatched one must not
    # skip the new log just because its length happens to coincide.
    trigger = Trigger(id="ack", kind=OperatorActionTrigger(tag="K-101", action="stop"))
    evaluator = TriggerEvaluator([trigger])

    first_log = ActionLog()
    first_log.record(tag="K-101", action="stop", value=None, sim_time=0.0)
    assert evaluator.evaluate(snapshot_at(0.0), first_log) == ("ack",)

    second_log = ActionLog()
    second_log.record(tag="K-101", action="start", value=None, sim_time=0.0)  # same length, no match
    assert evaluator.evaluate(snapshot_at(1.0), second_log) == ()

    second_log.record(tag="K-101", action="stop", value=None, sim_time=1.0)
    assert evaluator.evaluate(snapshot_at(2.0), second_log) == ("ack",)


def test_a_fresh_action_log_also_resets_one_shot_bookkeeping():
    # A fresh ActionLog is this evaluator's own signal that a new run has
    # begun - a one-shot trigger that already fired in the previous run
    # must not come back already spent in this one.
    evaluator = TriggerEvaluator([Trigger(id="t1", kind=TimeTrigger(sim_time=0.0), one_shot=True)])

    first_log = ActionLog()
    assert evaluator.evaluate(snapshot_at(0.0), first_log) == ("t1",)
    assert evaluator.evaluate(snapshot_at(1.0), first_log) == ()  # already fired

    second_log = ActionLog()
    assert evaluator.evaluate(snapshot_at(0.0), second_log) == ("t1",)  # fresh run, fires again


def test_evaluate_does_not_touch_the_action_log_when_no_trigger_needs_it():
    evaluator = TriggerEvaluator([Trigger(id="t1", kind=TimeTrigger(sim_time=10.0))])
    log = ActionLog()

    with mock.patch.object(ActionLog, "events", new_callable=mock.PropertyMock) as events:
        evaluator.evaluate(snapshot_at(10.0), log)

    events.assert_not_called()
