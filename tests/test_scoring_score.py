import dataclasses
import json

import pytest
import yaml

from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent
from app.engine.engine import Engine
from app.envelope.evaluator import Limits, Severity
from app.envelope.tracker import ExcursionTracker
from app.plant.loader import load_plant_file
from app.scoring.actionlog import ActionLog
from app.scoring.metrics import RunMetrics, compute_metrics
from app.scoring.score import (
    DEFAULT_CONFIG_PATH,
    PlantScoring,
    ScoringConfig,
    load_scoring_config,
    score_run,
    scoring_config_from_yaml,
)
from app.scoring.throughput import ThroughputTracker
from app.training.runtime import PlantRuntime

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
    config = dataclasses.replace(
        load_scoring_config(), production_lost_scales={"flow": 100.0, "level": 10.0}, plants={}
    )
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


def test_config_rejects_unknown_scale_keys():
    config = load_scoring_config()

    with pytest.raises(ValueError, match="unknown"):
        ScoringConfig(config.weights, {**config.scales, "bogus": 1.0}, config.peak_severity_penalty, {})


def test_production_lost_averages_across_labels():
    config = dataclasses.replace(
        load_scoring_config(), production_lost_scales={"flow": 100.0, "level": 10.0}, plants={}
    )
    flow = ThroughputTracker(target=10.0)
    flow.update(5.0, 10.0)  # lost 50 of 100 -> 0.5
    level = ThroughputTracker(target=10.0)
    level.update(0.0, 1.0)  # lost 10 of 10 -> 1.0
    metrics = compute_metrics(
        ActionLog(), AlarmHistory(capacity=10), {}, _relevant, throughput={"flow": flow, "level": level}
    )

    assert score_run(metrics, config).penalties["production_lost"] == pytest.approx(0.75)


def test_peak_severity_takes_the_worst_point():
    config = load_scoring_config()
    trackers = {
        "a": _tracker((12.0, Severity.WARNING, 1.0)),
        "b": _tracker((35.0, Severity.TRIP, 1.0)),
        "c": _tracker((5.0, Severity.NORMAL, 1.0)),
    }
    metrics = compute_metrics(ActionLog(), AlarmHistory(capacity=10), trackers, _relevant)

    assert score_run(metrics, config).penalties["peak_severity"] == pytest.approx(
        config.peak_severity_penalty[Severity.TRIP]
    )


def test_config_rejects_bad_peak_severity_penalty():
    config = load_scoring_config()
    penalties = dict(config.peak_severity_penalty)

    def build(table):
        return ScoringConfig(config.weights, config.scales, table, {})

    with pytest.raises(ValueError, match="TRIP"):
        build({k: v for k, v in penalties.items() if k is not Severity.TRIP})
    with pytest.raises(ValueError, match="TRIP"):
        build({**penalties, Severity.TRIP: 1.5})
    with pytest.raises(ValueError, match="unknown"):
        build({**penalties, Severity.NORMAL: 0.0})


# ---- what a run tracks, per plant (T20-15) ----


def _with_plants(plants, scales=None):
    config = load_scoring_config()
    return ScoringConfig(
        config.weights, config.scales, config.peak_severity_penalty, scales or {}, plants=plants
    )


def test_the_shipped_config_tracks_both_scenario_plants():
    config = load_scoring_config()

    assert config.plant("olefins_lite").excursions == ("V-101.level", "K-101.outlet_pressure")
    assert dict(config.plant("separator").throughput) == {"LV-301.flow": 50.0}


def test_a_plant_with_no_entry_tracks_nothing():
    assert load_scoring_config().plant("gas_compression") == PlantScoring()


def test_a_throughput_label_needs_a_production_lost_scale():
    with pytest.raises(ValueError, match="production_lost_scales"):
        _with_plants({"p": PlantScoring(throughput={"FV-1.flow": 10.0})})

    _with_plants({"p": PlantScoring(throughput={"FV-1.flow": 10.0})}, scales={"FV-1.flow": 100.0})


@pytest.mark.parametrize("label", ["level", "V-1.", ".level", "V-1.a.b", 7])
def test_a_label_must_be_a_tag_and_a_field(label):
    with pytest.raises(ValueError, match="<tag>.<field>"):
        _with_plants({"p": PlantScoring(excursions=(label,))})


@pytest.mark.parametrize("target", [-1.0, float("nan"), float("inf")])
def test_a_throughput_target_must_be_finite_and_non_negative(target):
    with pytest.raises(ValueError, match="target"):
        _with_plants({"p": PlantScoring(throughput={"FV-1.flow": target})}, scales={"FV-1.flow": 1.0})


def test_an_excursion_is_listed_once():
    with pytest.raises(ValueError, match="repeat"):
        _with_plants({"p": PlantScoring(excursions=("V-1.level", "V-1.level"))})


def test_a_score_and_its_metrics_are_json_ready():
    metrics = _fast_and_trippy()
    score = score_run(metrics, load_scoring_config())

    assert json.loads(json.dumps(score.as_dict())) == {"total": score.total, "penalties": dict(score.penalties)}
    peak = json.loads(json.dumps(metrics.as_dict()))["peak_excursions"]["discharge pressure"]
    assert peak == {"severity": "TRIP", "magnitude": 5.0, "timestamp": 12.0}


@pytest.mark.parametrize(
    "entry, mention",
    [
        ("[V-101.level]", "must be a mapping"),
        ("{excursions: V-101.level}", "excursions must be a list"),
        ("{throughput: [LV-101.flow]}", "throughput must be a mapping"),
        ("{excursion: [V-101.level]}", "unknown keys"),
        ("{throughput: {LV-101.flow: null}}", "must be a number"),
        ("{throughput: {LV-101.flow: fast}}", "must be a number"),
    ],
)
def test_a_plants_entry_of_the_wrong_shape_is_a_value_error(entry, mention):
    with pytest.raises(ValueError, match=mention):
        scoring_config_from_yaml(_with_entry("odd", yaml.safe_load(entry)))


def test_an_empty_plants_entry_tracks_nothing():
    assert scoring_config_from_yaml(_with_entry("empty", None)).plant("empty") == PlantScoring()


def _with_entry(plant_id, entry):
    document = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    document["plants"][plant_id] = entry
    return yaml.safe_dump(document)


@pytest.mark.parametrize("plant_id", sorted(load_scoring_config().plants))
def test_every_shipped_plants_entry_names_points_its_plant_has(plant_id):
    plant = load_plant_file(DEFAULT_CONFIG_PATH.parent / "plants" / f"{plant_id}.yaml")

    PlantRuntime(Engine.from_plant(plant), plant, scoring=load_scoring_config().plant(plant_id))


def test_an_empty_scoring_file_is_a_value_error():
    with pytest.raises(ValueError, match="must be a mapping"):
        scoring_config_from_yaml("")


def test_plants_that_is_not_a_mapping_is_a_value_error():
    document = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    document["plants"] = ["olefins_lite"]

    with pytest.raises(ValueError, match="plants must be a mapping"):
        scoring_config_from_yaml(yaml.safe_dump(document))
