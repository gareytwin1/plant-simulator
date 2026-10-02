import dataclasses

import pytest

from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent
from app.envelope.evaluator import Limits, Severity
from app.envelope.tracker import ExcursionTracker
from app.scoring.actionlog import ActionLog
from app.scoring.metrics import RunMetrics, compute_metrics
from app.scoring.score import ScoringConfig, load_scoring_config, score_run
from app.scoring.throughput import ThroughputTracker

LIMITS = Limits(warning_hi=10.0, alarm_hi=20.0, trip_hi=30.0)


def _relevant(action):
    return action.tag == "V-101"


def _history(*steps):
    history = AlarmHistory(capacity=100)
    manager = AlarmManager()
    for sim_time, severity in steps:
        side = None if severity is Severity.NORMAL else "hi"
        event = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side=side)
        history.record_events(manager.evaluate([event], sim_time=sim_time))
        history.record_clears(manager.last_cleared(), sim_time=sim_time)
    return history


def _tracker(*steps):
    tracker = ExcursionTracker(LIMITS)
    for value, severity, dt in steps:
        tracker.update(value, severity, dt)
    return tracker


def _slow_and_stable():
    """One warning at t=10, a late correct response at t=100, settled by t=400."""
    history = _history((10.0, Severity.WARNING), (400.0, Severity.NORMAL))
    log = ActionLog()
    log.record("V-101", "set_position", 0.4, sim_time=100.0)
    tracker = _tracker((12.0, Severity.WARNING, 390.0))
    return compute_metrics(log, history, {"discharge pressure": tracker}, _relevant)


def _fast_and_trippy():
    """Correct response in 2 s, but the plant still trips at t=20."""
    history = _history((10.0, Severity.WARNING), (14.0, Severity.ALARM), (20.0, Severity.TRIP))
    log = ActionLog()
    log.record("V-101", "set_position", 0.2, sim_time=12.0)
    tracker = _tracker(
        (12.0, Severity.WARNING, 4.0), (23.0, Severity.ALARM, 6.0), (35.0, Severity.TRIP, 2.0)
    )
    return compute_metrics(log, history, {"discharge pressure": tracker}, _relevant)


def _clean():
    return compute_metrics(ActionLog(), AlarmHistory(capacity=10), {}, _relevant)


def test_slow_and_stable_outscores_fast_and_trippy():
    config = load_scoring_config()

    assert score_run(_slow_and_stable(), config).total > score_run(_fast_and_trippy(), config).total


def test_clean_run_scores_one_hundred():
    assert score_run(_clean(), load_scoring_config()).total == pytest.approx(100.0)


def test_worst_possible_run_scores_zero():
    config = load_scoring_config()
    worst = RunMetrics(
        alarm_count=1000,
        trip_count=1000,
        time_to_recognise_s=None,
        time_to_stabilise_s=None,
        peak_excursions={"x": _tracker((99.0, Severity.TRIP, 1.0)).peak},
        time_outside_envelope_s=1e9,
        production_lost={},
        unnecessary_actions=1000,
    )
    # production_lost has no entries, so its weight is free: score is not 0 by that share.
    free = config.weights["production_lost"] / sum(config.weights.values())

    assert score_run(worst, config).total == pytest.approx(100.0 * free)


def test_none_times_are_free_without_alarms_and_costly_with_them():
    config = load_scoring_config()
    quiet = score_run(_clean(), config)
    metrics = dataclasses.replace(_clean(), alarm_count=1)

    assert quiet.penalties["time_to_recognise"] == 0.0
    assert quiet.penalties["time_to_stabilise"] == 0.0
    assert score_run(metrics, config).penalties["time_to_recognise"] == 1.0
    assert score_run(metrics, config).penalties["time_to_stabilise"] == 1.0


def test_raising_a_weight_lowers_the_score_for_a_run_that_does_badly_there():
    config = load_scoring_config()
    metrics = _fast_and_trippy()
    heavier = dataclasses.replace(config, weights={**config.weights, "trip_count": 400.0})

    assert score_run(metrics, heavier).total < score_run(metrics, config).total


def test_raising_a_weight_raises_the_score_for_a_run_that_does_well_there():
    config = load_scoring_config()
    metrics = _slow_and_stable()
    heavier = dataclasses.replace(config, weights={**config.weights, "trip_count": 400.0})

    assert score_run(metrics, heavier).total > score_run(metrics, config).total


def test_score_is_identical_across_replays():
    config = load_scoring_config()

    assert score_run(_fast_and_trippy(), config) == score_run(_fast_and_trippy(), config)


def test_production_lost_is_normalised_per_label():
    config = dataclasses.replace(load_scoring_config(), production_lost_scales={"flow": 100.0, "level": 10.0})
    tracker_flow = ThroughputTracker(target=10.0)
    tracker_flow.update(5.0, 10.0)  # lost 50
    metrics = compute_metrics(
        ActionLog(), AlarmHistory(capacity=10), {}, _relevant, throughput={"flow": tracker_flow}
    )

    assert score_run(metrics, config).penalties["production_lost"] == pytest.approx(0.5)


def test_unscaled_production_label_is_an_error():
    tracker = ThroughputTracker(target=10.0)
    tracker.update(5.0, 10.0)
    metrics = compute_metrics(
        ActionLog(), AlarmHistory(capacity=10), {}, _relevant, throughput={"flow": tracker}
    )

    with pytest.raises(ValueError, match="flow"):
        score_run(metrics, load_scoring_config())


def test_config_rejects_missing_or_bad_values():
    config = load_scoring_config()

    with pytest.raises(ValueError, match="missing"):
        ScoringConfig({}, config.scales, config.peak_severity_penalty, {})
    with pytest.raises(ValueError, match="non-negative"):
        ScoringConfig({**config.weights, "trip_count": -1.0}, config.scales, config.peak_severity_penalty, {})
    with pytest.raises(ValueError, match="positive"):
        ScoringConfig(config.weights, {**config.scales, "alarm_count": 0.0}, config.peak_severity_penalty, {})


def test_config_rejects_unknown_weight_keys():
    config = load_scoring_config()

    with pytest.raises(ValueError, match="unknown"):
        ScoringConfig({**config.weights, "bogus": 1.0}, config.scales, config.peak_severity_penalty, {})
