import pytest

from app.envelope.evaluator import Evaluator, Limits, Severity
from app.envelope.tracker import ExcursionTracker


def _limits():
    return Limits(
        trip_lo=0.0,
        alarm_lo=10.0,
        warning_lo=20.0,
        warning_hi=80.0,
        alarm_hi=90.0,
        trip_hi=100.0,
    )


def test_accumulated_time_matches_injected_step_counts_exactly():
    tracker = ExcursionTracker(_limits())

    tracker.update(15.0, Severity.WARNING, dt=2.0)
    tracker.update(15.0, Severity.WARNING, dt=3.0)
    tracker.update(5.0, Severity.ALARM, dt=4.0)
    tracker.update(50.0, Severity.NORMAL, dt=10.0)

    assert tracker.time_in(Severity.WARNING) == pytest.approx(5.0)
    assert tracker.time_in(Severity.ALARM) == pytest.approx(4.0)
    assert tracker.time_in(Severity.TRIP) == pytest.approx(0.0)


def test_normal_severity_does_not_accumulate_time_in_any_band():
    tracker = ExcursionTracker(_limits())

    tracker.update(50.0, Severity.NORMAL, dt=5.0)

    assert tracker.time_in(Severity.WARNING) == pytest.approx(0.0)
    assert tracker.time_in(Severity.ALARM) == pytest.approx(0.0)
    assert tracker.time_in(Severity.TRIP) == pytest.approx(0.0)


def test_magnitude_measured_against_the_side_the_value_actually_crossed():
    tracker = ExcursionTracker(_limits())

    tracker.update(15.0, Severity.WARNING, dt=1.0)
    assert tracker.peak is not None
    assert tracker.peak.magnitude == pytest.approx(5.0)  # warning_lo (20) - 15

    tracker.update(85.0, Severity.WARNING, dt=1.0)
    assert tracker.peak.magnitude == pytest.approx(5.0)  # 85 - warning_hi (80), tied


def test_peak_captured_across_a_transient_survives_de_escalation():
    tracker = ExcursionTracker(_limits())

    tracker.update(15.0, Severity.WARNING, dt=1.0)  # magnitude 5
    tracker.update(5.0, Severity.ALARM, dt=1.0)  # magnitude 5
    tracker.update(-10.0, Severity.TRIP, dt=1.0)  # magnitude 10, at t=3.0
    tracker.update(15.0, Severity.WARNING, dt=1.0)  # magnitude 5 again, at t=4.0

    peak = tracker.peak
    assert peak is not None
    assert peak.severity is Severity.TRIP
    assert peak.magnitude == pytest.approx(10.0)
    assert peak.timestamp == pytest.approx(3.0)


def test_peak_is_none_until_the_first_non_normal_update():
    tracker = ExcursionTracker(_limits())

    tracker.update(50.0, Severity.NORMAL, dt=1.0)

    assert tracker.peak is None


def test_counters_reset_correctly_on_scenario_reset():
    tracker = ExcursionTracker(_limits())

    tracker.update(-10.0, Severity.TRIP, dt=5.0)
    tracker.reset()

    assert tracker.peak is None
    assert tracker.time_in(Severity.WARNING) == pytest.approx(0.0)
    assert tracker.time_in(Severity.ALARM) == pytest.approx(0.0)
    assert tracker.time_in(Severity.TRIP) == pytest.approx(0.0)

    tracker.update(15.0, Severity.WARNING, dt=2.0)
    assert tracker.time_in(Severity.WARNING) == pytest.approx(2.0)
    assert tracker.peak is not None
    assert tracker.peak.timestamp == pytest.approx(2.0)


def test_deadband_held_recovery_reads_zero_magnitude_without_corrupting_peak():
    limits = _limits()
    evaluator = Evaluator(limits, deadband=5.0)
    tracker = ExcursionTracker(limits)

    severity = evaluator.evaluate(5.0, dt=1.0)  # past alarm_lo (10), magnitude 5
    tracker.update(5.0, severity, dt=1.0)
    assert severity is Severity.ALARM
    assert tracker.peak is not None
    assert tracker.peak.magnitude == pytest.approx(5.0)

    # 12 has recovered past alarm_lo (10) but not past the deadband clear
    # point (10 + 5 = 15), so Evaluator still holds ALARM even though 12 is
    # on neither side of alarm_lo/alarm_hi - the real peak must survive this.
    severity = evaluator.evaluate(12.0, dt=1.0)
    tracker.update(12.0, severity, dt=1.0)

    assert severity is Severity.ALARM
    assert tracker.time_in(Severity.ALARM) == pytest.approx(2.0)
    assert tracker.peak.magnitude == pytest.approx(5.0)


def test_negative_dt_is_rejected():
    tracker = ExcursionTracker(_limits())

    with pytest.raises(ValueError):
        tracker.update(15.0, Severity.WARNING, dt=-1.0)
