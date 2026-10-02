import pytest

from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent
from app.envelope.evaluator import Limits, Severity
from app.envelope.tracker import ExcursionTracker
from app.scoring.actionlog import ActionLog
from app.scoring.metrics import compute_metrics

LIMITS = Limits(warning_hi=10.0, alarm_hi=20.0, trip_hi=30.0)


def _relevant(action):
    return action.tag == "V-101"


def _history(*steps):
    history = AlarmHistory(capacity=100)
    manager = AlarmManager()
    for sim_time, severity in steps:
        event = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side="hi")
        history.record_events(manager.evaluate([event], sim_time=sim_time))
    return history


def _run():
    """Hand-checked run: warning at t=10, alarm at 14, trip at 20; operator
    touches the wrong valve at 12, the right one at 17 and again at 25."""
    history = _history((10.0, Severity.WARNING), (14.0, Severity.ALARM), (20.0, Severity.TRIP))

    log = ActionLog()
    log.record("V-102", "set_position", 0.5, sim_time=12.0)
    log.record("V-101", "set_position", 0.2, sim_time=17.0)
    log.record("V-101", "set_position", 0.0, sim_time=25.0)

    tracker = ExcursionTracker(LIMITS)
    for value, severity, dt in [
        (12.0, Severity.WARNING, 4.0),
        (23.0, Severity.ALARM, 6.0),
        (35.0, Severity.TRIP, 2.0),
        (5.0, Severity.NORMAL, 8.0),
    ]:
        tracker.update(value, severity, dt)

    return log, history, {"discharge pressure": tracker}


def test_alarm_count_counts_every_alarm_event():
    log, history, trackers = _run()

    assert compute_metrics(log, history, trackers, _relevant).alarm_count == 3


def test_acknowledgements_are_not_alarms():
    history = _history((10.0, Severity.WARNING))
    history.record_acknowledge("K-101.discharge pressure", "K-101", sim_time=11.0)

    assert compute_metrics(ActionLog(), history, {}, _relevant).alarm_count == 1


def test_trip_count_counts_trip_severity_events():
    log, history, trackers = _run()

    assert compute_metrics(log, history, trackers, _relevant).trip_count == 1


def test_time_to_recognise_is_first_alarm_to_first_relevant_action():
    log, history, trackers = _run()

    assert compute_metrics(log, history, trackers, _relevant).time_to_recognise_s == pytest.approx(7.0)


def test_time_to_recognise_ignores_a_relevant_action_before_the_first_alarm():
    history = _history((10.0, Severity.WARNING))
    log = ActionLog()
    log.record("V-101", "set_position", 0.9, sim_time=3.0)
    log.record("V-101", "set_position", 0.2, sim_time=16.0)

    assert compute_metrics(log, history, {}, _relevant).time_to_recognise_s == pytest.approx(6.0)


def test_time_to_recognise_is_none_without_an_alarm_or_a_relevant_action():
    log = ActionLog()
    log.record("V-101", "set_position", 0.2, sim_time=5.0)

    assert compute_metrics(log, AlarmHistory(capacity=10), {}, _relevant).time_to_recognise_s is None

    wrong_only = ActionLog()
    wrong_only.record("V-102", "set_position", 0.2, sim_time=15.0)
    result = compute_metrics(wrong_only, _history((10.0, Severity.WARNING)), {}, _relevant)

    assert result.time_to_recognise_s is None


def test_peak_excursion_is_the_trackers_peak_per_label():
    log, history, trackers = _run()
    peaks = compute_metrics(log, history, trackers, _relevant).peak_excursions

    assert peaks["discharge pressure"].severity is Severity.TRIP
    assert peaks["discharge pressure"].magnitude == pytest.approx(5.0)


def test_peak_excursion_is_none_for_a_point_that_never_left_normal():
    quiet = ExcursionTracker(LIMITS)
    quiet.update(5.0, Severity.NORMAL, 1.0)

    result = compute_metrics(ActionLog(), AlarmHistory(capacity=10), {"p": quiet}, _relevant)

    assert result.peak_excursions == {"p": None}


def test_time_outside_envelope_sums_every_non_normal_band_across_points():
    log, history, trackers = _run()
    other = ExcursionTracker(LIMITS)
    other.update(12.0, Severity.WARNING, 3.0)

    result = compute_metrics(log, history, {**trackers, "other": other}, _relevant)

    assert result.time_outside_envelope_s == pytest.approx(4.0 + 6.0 + 2.0 + 3.0)


def test_unnecessary_actions_are_those_the_relevance_predicate_rejects():
    log, history, trackers = _run()

    assert compute_metrics(log, history, trackers, _relevant).unnecessary_actions == 1


def test_empty_run_is_all_zero():
    result = compute_metrics(ActionLog(), AlarmHistory(capacity=10), {}, _relevant)

    assert (result.alarm_count, result.trip_count, result.unnecessary_actions) == (0, 0, 0)
    assert result.time_outside_envelope_s == pytest.approx(0.0)
    assert result.time_to_recognise_s is None


def test_metrics_are_identical_on_a_repeat_computation():
    log, history, trackers = _run()

    assert compute_metrics(log, history, trackers, _relevant) == compute_metrics(
        log, history, trackers, _relevant
    )


def test_peak_excursions_cannot_be_mutated_by_a_consumer():
    log, history, trackers = _run()
    peaks = compute_metrics(log, history, trackers, _relevant).peak_excursions

    with pytest.raises(TypeError):
        peaks["discharge pressure"] = None


def _settling(*steps):
    history = AlarmHistory(capacity=100)
    manager = AlarmManager()
    for sim_time, severity in steps:
        side = None if severity is Severity.NORMAL else "hi"
        event = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side=side)
        history.record_events(manager.evaluate([event], sim_time=sim_time))
        history.record_clears(manager.last_cleared(), sim_time=sim_time)
    return history


def test_time_to_stabilise_is_first_alarm_to_last_return_to_normal():
    history = _settling(
        (10.0, Severity.WARNING), (14.0, Severity.ALARM), (30.0, Severity.NORMAL)
    )

    assert compute_metrics(ActionLog(), history, {}, _relevant).time_to_stabilise_s == pytest.approx(20.0)


def test_time_to_stabilise_waits_for_every_point_that_alarmed():
    history = AlarmHistory(capacity=100)
    manager = AlarmManager()
    for sim_time, pv, severity in [
        (10.0, "discharge pressure", Severity.ALARM),
        (11.0, "suction pressure", Severity.WARNING),
        (20.0, "discharge pressure", Severity.NORMAL),
    ]:
        side = None if severity is Severity.NORMAL else "hi"
        event = EnvelopeEvent(tag="K-101", pv=pv, severity=severity, side=side)
        history.record_events(manager.evaluate([event], sim_time=sim_time))
        history.record_clears(manager.last_cleared(), sim_time=sim_time)

    assert compute_metrics(ActionLog(), history, {}, _relevant).time_to_stabilise_s is None


def test_time_to_stabilise_is_none_while_a_point_is_still_alarming_or_never_alarmed():
    still_out = _settling((10.0, Severity.ALARM))

    assert compute_metrics(ActionLog(), still_out, {}, _relevant).time_to_stabilise_s is None
    assert (
        compute_metrics(ActionLog(), AlarmHistory(capacity=10), {}, _relevant).time_to_stabilise_s
        is None
    )


def test_a_re_alarm_after_a_clear_means_not_yet_stable():
    history = _settling(
        (10.0, Severity.ALARM), (20.0, Severity.NORMAL), (25.0, Severity.WARNING)
    )

    assert compute_metrics(ActionLog(), history, {}, _relevant).time_to_stabilise_s is None


def test_production_lost_is_reported_per_variable():
    from app.scoring.throughput import ThroughputTracker

    flow = ThroughputTracker(target=100.0)
    flow.update(70.0, dt=4.0)
    other = ThroughputTracker(target=5.0)
    other.update(4.0, dt=2.0)

    result = compute_metrics(
        ActionLog(), AlarmHistory(capacity=10), {}, _relevant, throughput={"flow": flow, "other": other}
    )

    assert result.production_lost == {"flow": pytest.approx(120.0), "other": pytest.approx(2.0)}


def test_production_lost_is_empty_without_throughput_trackers():
    result = compute_metrics(ActionLog(), AlarmHistory(capacity=10), {}, _relevant)

    assert result.production_lost == {}
