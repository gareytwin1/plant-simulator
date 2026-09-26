import pytest

from app.envelope.evaluator import Evaluator, Limits, Severity


def _limits():
    return Limits(
        trip_lo=0.0,
        alarm_lo=10.0,
        warning_lo=20.0,
        warning_hi=80.0,
        alarm_hi=90.0,
        trip_hi=100.0,
    )


def test_normal_band_is_between_the_two_warning_limits():
    evaluator = Evaluator(_limits())

    assert evaluator.evaluate(50.0, dt=1.0) is Severity.NORMAL


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.0, Severity.TRIP),
        (10.0, Severity.ALARM),
        (20.0, Severity.WARNING),
        (80.0, Severity.WARNING),
        (90.0, Severity.ALARM),
        (100.0, Severity.TRIP),
    ],
)
def test_boundary_value_belongs_to_the_more_severe_band(value, expected):
    evaluator = Evaluator(_limits())

    assert evaluator.evaluate(value, dt=1.0) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.001, Severity.ALARM),
        (10.001, Severity.WARNING),
        (20.001, Severity.NORMAL),
        (79.999, Severity.NORMAL),
        (89.999, Severity.WARNING),
        (99.999, Severity.ALARM),
    ],
)
def test_value_just_inside_a_boundary_belongs_to_the_less_severe_band(value, expected):
    evaluator = Evaluator(_limits())

    assert evaluator.evaluate(value, dt=1.0) is expected


def test_missing_limit_is_never_reached():
    evaluator = Evaluator(Limits(trip_hi=100.0))

    assert evaluator.evaluate(-1_000_000.0, dt=1.0) is Severity.NORMAL


def test_inconsistent_ordering_is_rejected():
    with pytest.raises(ValueError):
        Limits(alarm_lo=10.0, warning_lo=5.0)


def test_deadband_holds_the_worse_classification_while_hovering_at_a_threshold():
    evaluator = Evaluator(_limits(), deadband=2.0)

    assert evaluator.evaluate(20.0, dt=1.0) is Severity.WARNING
    assert evaluator.evaluate(21.0, dt=1.0) is Severity.WARNING
    assert evaluator.evaluate(20.5, dt=1.0) is Severity.WARNING
    assert evaluator.evaluate(21.9, dt=1.0) is Severity.WARNING


def test_deadband_releases_once_cleared_by_more_than_its_width():
    evaluator = Evaluator(_limits(), deadband=2.0)

    assert evaluator.evaluate(20.0, dt=1.0) is Severity.WARNING
    assert evaluator.evaluate(22.0, dt=1.0) is Severity.NORMAL


def test_zero_deadband_de_escalates_immediately_on_the_raw_boundary():
    evaluator = Evaluator(_limits(), deadband=0.0)

    assert evaluator.evaluate(20.0, dt=1.0) is Severity.WARNING
    assert evaluator.evaluate(20.0001, dt=1.0) is Severity.NORMAL


def test_on_delay_suppresses_a_transient_shorter_than_its_duration():
    evaluator = Evaluator(_limits(), on_delay=5.0)

    assert evaluator.evaluate(15.0, dt=2.0) is Severity.NORMAL
    assert evaluator.evaluate(50.0, dt=2.0) is Severity.NORMAL


def test_on_delay_commits_once_the_worse_classification_persists_long_enough():
    evaluator = Evaluator(_limits(), on_delay=5.0)

    assert evaluator.evaluate(15.0, dt=2.0) is Severity.NORMAL
    assert evaluator.evaluate(15.0, dt=2.0) is Severity.NORMAL
    assert evaluator.evaluate(15.0, dt=2.0) is Severity.WARNING


def test_on_delay_resets_when_the_transient_moves_to_a_different_band():
    evaluator = Evaluator(_limits(), on_delay=5.0)

    assert evaluator.evaluate(15.0, dt=4.0) is Severity.NORMAL
    assert evaluator.evaluate(5.0, dt=4.0) is Severity.NORMAL
    assert evaluator.evaluate(5.0, dt=4.0) is Severity.ALARM


def test_on_delay_does_not_hold_back_de_escalation():
    evaluator = Evaluator(_limits(), on_delay=5.0)

    assert evaluator.evaluate(15.0, dt=2.0) is Severity.NORMAL
    assert evaluator.evaluate(15.0, dt=2.0) is Severity.NORMAL
    assert evaluator.evaluate(15.0, dt=2.0) is Severity.WARNING
    assert evaluator.evaluate(50.0, dt=0.0) is Severity.NORMAL


def test_same_severity_side_flip_reclassifies_immediately():
    evaluator = Evaluator(_limits(), deadband=5.0)

    assert evaluator.evaluate(20.0, dt=1.0) is Severity.WARNING
    assert evaluator.evaluate(80.0, dt=0.0) is Severity.WARNING
    assert evaluator.severity is Severity.WARNING

    # 76.0 is nowhere near the old lo-side clear point (20 + 5 = 25), so this
    # only stays WARNING if the flip switched to the new hi-side threshold
    # (80 - 5 = 75) rather than leaving the stale lo-side one in place.
    assert evaluator.evaluate(76.0, dt=0.0) is Severity.WARNING
    assert evaluator.evaluate(74.0, dt=0.0) is Severity.NORMAL


def test_severity_property_matches_the_last_evaluate_call():
    evaluator = Evaluator(_limits())

    evaluator.evaluate(5.0, dt=1.0)

    assert evaluator.severity is Severity.ALARM


def test_negative_dt_is_rejected():
    evaluator = Evaluator(_limits())

    with pytest.raises(ValueError):
        evaluator.evaluate(50.0, dt=-1.0)


def test_negative_deadband_is_rejected():
    with pytest.raises(ValueError):
        Evaluator(_limits(), deadband=-1.0)


def test_negative_on_delay_is_rejected():
    with pytest.raises(ValueError):
        Evaluator(_limits(), on_delay=-1.0)
