"""
Scenario lifecycle - T14-4. The build-plan tests come first: a scenario loads
and arms with no state change, an ignored one reaches its expected failure, a
correctly recovered one reaches success, and abort restores a clean state.

Every scenario here runs the reference plant from `feed_pump_trip`: P-101
tripped, V-101 draining at about 0.19 and falling. Ignored, its level reaches
0.1 in about 90 s. Recovered - P-101 started *and* given a speed target, since
`stop()` zeroes it - the level climbs back past 0.25 in about 330 s.
"""

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.alarms.acknowledge import Acknowledged
from app.api.action import apply_action
from app.engine.engine import Engine
from app.engine.persistence import capture_state, restore_state
from app.engine.scheduler import Scheduler
from app.envelope.evaluator import Severity, isa_band
from app.envelope.tracker import ExcursionTracker
from app.plant.loader import load_plant_file
from app.scenarios.runner import (
    Outcome,
    Phase,
    ScenarioConfigError,
    ScenarioLibrary,
    ScenarioNotFound,
    ScenarioRunner,
    ScenarioStateError,
    apply_overrides,
)
from app.scenarios.replay import Recording, replay
from app.scoring.metrics import compute_metrics
from app.scoring.score import load_scoring_config, score_run
from app.scoring.throughput import ThroughputTracker
from app.training.runtime import PlantRuntime


CONFIG = Path(__file__).resolve().parent.parent / "config"
DT = 1.0


def scenario(**changes):
    config = {
        "id": "pump-trip",
        "plant": "olefins_lite",
        "initial_condition": {"condition": "feed_pump_trip"},
        "objectives": [
            {
                "id": "recover-level",
                "success": {"condition": "V-101.level >= 0.25", "hold_duration_s": 20},
                "failure": {"condition": "V-101.level <= 0.1"},
            },
        ],
        "relevant_tags": ["P-101", "LV-101"],
        "time_limit_s": 900,
        "difficulty": "easy",
        "seed": 7,
    }
    config.update(changes)

    return config


def armed_engine(condition="feed_pump_trip"):
    """What a load should leave behind, built without the runner."""
    plant = load_plant_file(CONFIG / "plants" / "olefins_lite.yaml")
    engine = Engine.from_plant(plant)
    restore_state(engine, json.loads((CONFIG / "initial_conditions" / f"{condition}.json").read_text()))

    # A run steps a PlantRuntime, whose trip system adds its interlock slots to the arbiter.
    return PlantRuntime(engine, plant).engine


def started(config=None):
    runner = ScenarioRunner()
    runner.load_config(config or scenario())
    runner.start()

    return runner


def act(runner, target, action, value=None):
    engine = runner.engine
    apply_action(engine.equipment, runner.actions, engine.clock.sim_time, target, action, value, loops=engine.loops)


def run_out(runner, limit=1000):
    for _ in range(limit):
        if runner.phase is not Phase.RUNNING:
            return

        runner.step(DT)

    raise AssertionError(f"still running after {limit} steps")


def recover(runner):
    act(runner, "P-101", "start")
    act(runner, "P-101", "set_speed_target", 1.0)


# ---- the build-plan tests ----


def test_a_scenario_loads_and_arms_with_no_state_change():
    runner = ScenarioRunner()

    result = runner.load_config(scenario())

    assert runner.phase is Phase.LOADED
    assert result.elapsed_s == pytest.approx(0.0)
    assert capture_state(runner.engine) == capture_state(armed_engine())


def test_an_ignored_scenario_reaches_the_expected_trip():
    runner = started()

    run_out(runner)

    result = runner.result()
    assert result.phase is Phase.COMPLETE
    assert result.outcome is Outcome.FAILED
    assert result.objectives[0].status.value == "failed"
    assert 60 < result.elapsed_s < 150
    assert runner.engine.equipment["V-101"].level <= 0.1


def test_a_correctly_recovered_scenario_reaches_the_expected_success():
    runner = started()
    recover(runner)

    run_out(runner)

    result = runner.result()
    assert result.phase is Phase.COMPLETE
    assert result.outcome is Outcome.SUCCEEDED
    assert result.objectives[0].status.value == "succeeded"
    assert result.elapsed_s < 900


def test_abort_restores_a_clean_state():
    config = scenario(
        malfunctions=[{"target_tag": "LV-101", "parameter": "capacity", "value": 10.0}],
    )
    runner = started(config)
    recover(runner)
    for _ in range(50):
        runner.step(DT)
    assert capture_state(runner.engine) != capture_state(armed_engine())

    result = runner.abort()

    assert result.phase is Phase.ABORTED
    assert result.outcome is Outcome.ABORTED
    assert capture_state(runner.engine) == capture_state(armed_engine())
    assert runner.engine.equipment["LV-101"].capacity == pytest.approx(20.0)


# ---- scenario time ----


def test_scenario_time_starts_at_zero_whatever_the_condition_clock_says():
    runner = started()
    origin = runner.engine.clock.sim_time
    assert origin == pytest.approx(14770.0)

    for _ in range(10):
        snapshot = runner.step(DT)

    assert snapshot.sim_time == pytest.approx(origin + 10)
    assert runner.result().elapsed_s == pytest.approx(10.0)


def test_a_time_limit_is_seconds_since_the_run_began():
    config = scenario(
        objectives=[{"id": "never", "success": {"condition": "V-101.level >= 5.0"}}],
        time_limit_s=30,
    )
    runner = started(config)

    run_out(runner)

    result = runner.result()
    assert result.outcome is Outcome.TIMED_OUT
    assert result.elapsed_s == pytest.approx(30.0)


def test_a_scenario_with_no_objectives_runs_its_time_limit_out():
    runner = started(scenario(objectives=[], time_limit_s=12))

    run_out(runner)

    result = runner.result()
    assert result.outcome is Outcome.TIMED_OUT
    assert result.elapsed_s == pytest.approx(12.0)


def test_a_completed_run_stops_advancing_the_plant():
    runner = started(scenario(objectives=[], time_limit_s=5))
    run_out(runner)
    frozen = capture_state(runner.engine)

    runner.step(DT)

    assert capture_state(runner.engine) == frozen


def test_nothing_advances_before_start():
    runner = ScenarioRunner()
    runner.load_config(scenario())
    armed = capture_state(runner.engine)

    runner.step(DT)

    assert capture_state(runner.engine) == armed
    assert runner.phase is Phase.LOADED


def test_a_loaded_run_annunciates_every_interlock_before_it_starts():
    runner = ScenarioRunner()
    runner.load_config(scenario())

    for snapshot in (runner.snapshot(), runner.step(DT)):
        assert set(snapshot.as_dict()["interlocks"]) == {"LSHH-101", "LSLL-101", "PSHH-101"}


def test_a_trip_still_reads_tripped_once_the_run_is_over():
    # Ignored, the feed pump trip drains V-101 past LSLL-101, which latches.
    runner = started(scenario(objectives=[], time_limit_s=300))
    run_out(runner)

    assert runner.phase is Phase.COMPLETE
    for snapshot in (runner.snapshot(), runner.step(DT)):
        assert snapshot.interlocks["LSLL-101"].state == "tripped"


def test_act_applies_and_logs_an_action_on_the_live_run():
    runner = started()

    runner.act("P-101", "set_speed_target", 0.5)

    assert runner.engine.equipment["P-101"].speed_target == pytest.approx(0.5)
    assert [event.tag for event in runner.actions] == ["P-101"]


def test_act_is_refused_once_the_run_is_over_so_its_result_stays_final():
    finished = started(scenario(objectives=[], time_limit_s=3))
    run_out(finished)
    aborted = started()
    aborted.abort()

    for runner in (finished, aborted):
        with pytest.raises(ScenarioStateError, match="final"):
            runner.act("P-101", "start", None)

        assert len(runner.result().actions) == 0


def test_act_refuses_with_nothing_loaded():
    runner = ScenarioRunner()

    with pytest.raises(ScenarioStateError, match="no scenario"):
        runner.act("P-101", "start", None)


def test_act_does_not_interleave_with_a_step():
    # act and step take the runner's lock, so while one thread holds it in a
    # step, an act waits rather than mutating a device mid-step.
    runner = started()
    entered, release = threading.Event(), threading.Event()
    engine = runner.engine
    real_step = engine.step

    def slow_step(dt):
        entered.set()
        release.wait(timeout=5)

        return real_step(dt)

    engine.step = slow_step
    stepper = threading.Thread(target=runner.step, args=(DT,))
    stepper.start()
    assert entered.wait(timeout=5)

    actor = threading.Thread(target=runner.act, args=("P-101", "start", None))
    actor.start()
    actor.join(timeout=0.2)
    assert actor.is_alive()
    assert engine.equipment["P-101"].running is False

    release.set()
    stepper.join(timeout=5)
    actor.join(timeout=5)
    assert engine.equipment["P-101"].running is True


def test_the_runner_can_be_driven_by_a_scheduler():
    runner = started()
    before = runner.engine.clock.sim_time

    snapshot = Scheduler(runner).step_once()

    assert snapshot is not None
    assert snapshot.sim_time > before


# ---- what a run carries ----


def test_a_malfunction_takes_effect_at_its_onset_and_abort_reverts_it():
    config = scenario(
        malfunctions=[
            {
                "target_tag": "LV-101",
                "parameter": "capacity",
                "value": 10.0,
                "start_condition": {"type": "at_time", "sim_time": 5},
            },
        ],
    )
    runner = started(config)
    valve = runner.engine.equipment["LV-101"]

    for _ in range(4):
        runner.step(DT)
    assert valve.capacity == pytest.approx(20.0)

    for _ in range(2):
        runner.step(DT)
    assert valve.capacity == pytest.approx(10.0)

    runner.abort()
    assert runner.engine.equipment["LV-101"].capacity == pytest.approx(20.0)


def test_a_flag_malfunction_loads_from_config_and_abort_restores_it():
    config = scenario(
        malfunctions=[
            {
                "target_tag": "LV-101",
                "parameter": "signal_ok",
                "value": False,
                "start_condition": {"type": "at_time", "sim_time": 5},
            },
        ],
    )
    runner = started(config)
    valve = runner.engine.equipment["LV-101"]

    for _ in range(4):
        runner.step(DT)
    assert valve.signal_ok is True

    for _ in range(2):
        runner.step(DT)
    assert valve.signal_ok is False

    runner.abort()
    assert runner.engine.equipment["LV-101"].signal_ok is True


def test_a_malfunction_due_at_time_zero_takes_effect_on_start():
    config = scenario(
        malfunctions=[{"target_tag": "LV-101", "parameter": "capacity", "value": 10.0}],
    )
    runner = ScenarioRunner()
    runner.load_config(config)
    assert runner.engine.equipment["LV-101"].capacity == pytest.approx(20.0)

    runner.start()

    assert runner.engine.equipment["LV-101"].capacity == pytest.approx(10.0)


def test_triggers_are_recorded_at_the_scenario_time_they_first_fired():
    config = scenario(
        triggers=[
            {"id": "ten-seconds", "type": "time", "sim_time": 10},
            {"id": "restarted", "type": "operator_action", "action": "P-101.start"},
        ],
    )
    runner = started(config)

    for _ in range(3):
        runner.step(DT)
    assert runner.result().triggers_fired == {}

    act(runner, "P-101", "start")
    for _ in range(10):
        runner.step(DT)

    assert runner.result().triggers_fired == {
        "restarted": pytest.approx(4.0),
        "ten-seconds": pytest.approx(10.0),
    }


def test_actions_are_reported_at_scenario_time():
    runner = started()
    for _ in range(6):
        runner.step(DT)

    act(runner, "P-101", "set_speed_target", 0.5)

    (action,) = runner.result().actions
    assert action == {"sim_time": pytest.approx(6.0), "tag": "P-101", "action": "set_speed_target", "value": 0.5}


def test_an_override_adjusts_the_named_initial_condition():
    config = scenario(initial_condition={"condition": "feed_pump_trip", "overrides": {"V-101._level": 0.4}})
    runner = ScenarioRunner()

    runner.load_config(config)

    assert runner.engine.equipment["V-101"].level == pytest.approx(0.4)
    assert capture_state(runner.engine) != capture_state(armed_engine())


def test_the_result_is_json_ready():
    runner = started()
    recover(runner)
    run_out(runner)

    body = json.loads(json.dumps(runner.result().as_dict()))

    assert body["outcome"] == "succeeded"
    assert body["objectives"][0]["id"] == "recover-level"
    assert body["seed"] == 7


# ---- lifecycle refusals ----


def test_nothing_can_be_started_result_read_or_aborted_before_a_load():
    runner = ScenarioRunner()

    assert runner.phase is Phase.IDLE
    for call in (runner.start, runner.abort, runner.result):
        with pytest.raises(ScenarioStateError, match="no scenario"):
            call()


def test_a_run_cannot_be_started_twice():
    runner = started()

    with pytest.raises(ScenarioStateError, match="running"):
        runner.start()


def test_a_running_scenario_must_be_aborted_before_another_is_loaded():
    runner = started()

    with pytest.raises(ScenarioStateError, match="abort"):
        runner.load_config(scenario())

    runner.abort()
    runner.load_config(scenario())
    assert runner.phase is Phase.LOADED


def test_a_completed_result_is_not_discarded_by_an_abort():
    runner = started(scenario(objectives=[], time_limit_s=3))
    run_out(runner)

    with pytest.raises(ScenarioStateError, match="complete"):
        runner.abort()

    assert runner.result().outcome is Outcome.TIMED_OUT


def test_an_aborted_run_cannot_be_started_again():
    runner = started()
    runner.abort()

    with pytest.raises(ScenarioStateError, match="aborted"):
        runner.start()


# ---- a load is refused whole ----


BAD_CONFIGS = {
    "schema": (lambda: {**scenario(), "difficulty": "impossible"}, ScenarioConfigError, "difficulty"),
    "unknown tag in a condition": (
        lambda: scenario(objectives=[{"id": "o", "success": {"condition": "X-999.level >= 1"}}]),
        ValueError,
        "X-999",
    ),
    "unknown field in a trigger": (
        lambda: scenario(triggers=[{"id": "t", "type": "condition", "condition": "V-101.nope > 1"}]),
        ValueError,
        "nope",
    ),
    "unknown override": (
        lambda: scenario(initial_condition={"condition": "feed_pump_trip", "overrides": {"V-101.nope": 1}}),
        ScenarioConfigError,
        "nope",
    ),
    "a value the device refuses": (
        lambda: scenario(initial_condition={"condition": "feed_pump_trip", "overrides": {"V-101._level": -1}}),
        ValueError,
        "level",
    ),
    "unknown malfunction target": (
        lambda: scenario(malfunctions=[{"target_tag": "X-999", "parameter": "capacity", "value": 1}]),
        KeyError,
        "X-999",
    ),
    "unwritable malfunction parameter": (
        lambda: scenario(malfunctions=[{"target_tag": "LV-101", "parameter": "position", "value": 1}]),
        ValueError,
        "position",
    ),
    "a flag set on a ramp": (
        lambda: scenario(
            malfunctions=[
                {
                    "target_tag": "LV-101",
                    "parameter": "signal_ok",
                    "value": False,
                    "profile": {"type": "ramp", "duration_s": 10},
                },
            ],
        ),
        ScenarioConfigError,
        "only a step",
    ),
    "a flag written to a number": (
        lambda: scenario(malfunctions=[{"target_tag": "LV-101", "parameter": "capacity", "value": True}]),
        ValueError,
        "capacity",
    ),
    "a number written to a flag": (
        lambda: scenario(malfunctions=[{"target_tag": "LV-101", "parameter": "signal_ok", "value": 0}]),
        ValueError,
        "signal_ok",
    ),
    "ramp profile without a duration": (
        lambda: scenario(
            malfunctions=[
                {"target_tag": "LV-101", "parameter": "capacity", "value": 1, "profile": {"type": "ramp"}},
            ],
        ),
        ScenarioConfigError,
        "ramp",
    ),
    "condition onset without a condition": (
        lambda: scenario(
            malfunctions=[
                {
                    "target_tag": "LV-101",
                    "parameter": "capacity",
                    "value": 1,
                    "start_condition": {"type": "condition"},
                },
            ],
        ),
        ScenarioConfigError,
        "at_time",
    ),
    "condition onset on a field the plant lacks": (
        lambda: scenario(
            malfunctions=[
                {
                    "target_tag": "LV-101",
                    "parameter": "capacity",
                    "value": 1,
                    "start_condition": {"type": "condition", "condition": "V-101.nope > 1"},
                },
            ],
        ),
        ValueError,
        "nope",
    ),
    "no relevant tags": (
        lambda: {key: value for key, value in scenario().items() if key != "relevant_tags"},
        ScenarioConfigError,
        "relevant_tags",
    ),
    "a relevant tag the plant lacks": (
        lambda: scenario(relevant_tags=["P-101", "X-999"]),
        ScenarioConfigError,
        "X-999",
    ),
    "a point named as a relevant tag": (
        lambda: scenario(relevant_tags=["V-101.level"]),
        ScenarioConfigError,
        "V-101.level",
    ),
    "a loop-driven device without its loop": (
        lambda: scenario(relevant_tags=["P-101", "PV-101"]),
        ScenarioConfigError,
        "PV-101 is driven by loop PIC-101",
    ),
}


@pytest.mark.parametrize("make, error, mention", BAD_CONFIGS.values(), ids=BAD_CONFIGS.keys())
def test_a_bad_scenario_is_refused_and_leaves_the_loaded_run_untouched(make, error, mention):
    runner = ScenarioRunner()
    runner.load_config(scenario(id="first"))
    armed = capture_state(runner.engine)

    with pytest.raises(error, match=mention):
        runner.load_config(make())

    assert runner.result().scenario_id == "first"
    assert capture_state(runner.engine) == armed


def test_a_relevant_tag_may_be_a_device_a_loop_or_an_interlock():
    runner = ScenarioRunner()

    runner.load_config(scenario(relevant_tags=["P-101", "PV-101", "PIC-101", "LSLL-101"]))

    assert runner.phase is Phase.LOADED


def test_a_condition_for_another_plant_is_refused():
    runner = ScenarioRunner()

    with pytest.raises(ValueError):
        runner.load_config(scenario(plant="gas_compression"))

    assert runner.phase is Phase.IDLE


def test_overrides_never_invent_a_device_or_attribute():
    state = json.loads((CONFIG / "initial_conditions" / "feed_pump_trip.json").read_text())

    for key in ("nope", "X-999.level", "V-101.", ".level"):
        with pytest.raises(ScenarioConfigError):
            apply_overrides(copy.deepcopy(state), {key: 1})


# ---- the library ----


@pytest.mark.parametrize("name", ["../plants/olefins_lite", "/etc/passwd", "a/b", "", "a.b"])
def test_a_name_that_is_not_plain_never_reaches_the_filesystem(name):
    library = ScenarioLibrary()

    for lookup in (library.scenario, library.plant_path, library.condition):
        with pytest.raises(ScenarioNotFound):
            lookup(name)


def test_a_scenario_is_read_from_the_library_by_id(tmp_path):
    (tmp_path / "pump-trip.yaml").write_text(
        "id: pump-trip\n"
        "plant: olefins_lite\n"
        "initial_condition: {condition: feed_pump_trip}\n"
        "relevant_tags: [P-101, LV-101]\n"
        "time_limit_s: 60\n"
        "difficulty: easy\n"
        "seed: 1\n",
    )
    runner = ScenarioRunner(ScenarioLibrary(scenarios=tmp_path))

    assert runner.load("pump-trip").scenario_id == "pump-trip"

    with pytest.raises(ScenarioNotFound, match="missing"):
        runner.load("missing")


def test_an_unparseable_scenario_file_is_a_config_error(tmp_path):
    (tmp_path / "broken.yaml").write_text("id: [unclosed\n")

    with pytest.raises(ScenarioConfigError, match="not parseable"):
        ScenarioRunner(ScenarioLibrary(scenarios=tmp_path)).load("broken")


# ---- the plant runtime (T16-7) ----


def brimming(**changes):
    """The reference plant with V-101 already past LSHH-101's 0.9, P-101 running."""
    return scenario(
        initial_condition={"condition": "feed_pump_trip", "overrides": {"V-101._level": 0.95}},
        objectives=[{"id": "never", "success": {"condition": "V-101.level >= 5.0"}}],
        **changes,
    )


def brimming_run():
    runner = started(brimming())
    recover(runner)

    return runner


def run_for(runner, seconds):
    for _ in range(seconds):
        runner.step(DT)


def test_a_run_that_holds_v101_above_the_trip_level_trips_lshh_101():
    runner = brimming_run()
    assert runner.engine.equipment["P-101"].running

    run_for(runner, 30)

    assert not runner.engine.equipment["P-101"].running
    assert runner.engine.snapshot().equipment["P-101"]["speed"] < 1.0
    assert "V-101" in {entry.tag for entry in runner.alarm_entries()}


def test_an_interlock_reset_is_an_operator_action_the_run_journals():
    runner = brimming_run()
    run_for(runner, 30)

    with pytest.raises(ValueError, match="condition still holds"):
        runner.act("LSHH-101", "reset", None)
    assert "reset" not in [row["action"] for row in runner.result().actions]

    runner.act("LV-101", "set_position_target", 1.0)
    run_for(runner, 20)
    runner.act("LSHH-101", "reset", None)

    assert [row["action"] for row in runner.result().actions][-1] == "reset"
    assert runner.result().actions[-1]["tag"] == "LSHH-101"
    assert runner.result().actions[-1]["value"] is None


def test_an_interlock_accepts_only_reset_with_no_value():
    runner = brimming_run()

    with pytest.raises(ValueError):
        runner.act("LSHH-101", "start", None)

    with pytest.raises(ValueError):
        runner.act("LSHH-101", "reset", 1.0)


def test_alarm_entries_and_acknowledge_are_refused_with_nothing_loaded():
    runner = ScenarioRunner()

    with pytest.raises(ScenarioStateError):
        runner.alarm_entries()

    with pytest.raises(ScenarioStateError):
        runner.acknowledge("alarm-0")


def test_a_run_acknowledges_its_own_alarms():
    runner = brimming_run()
    run_for(runner, 30)
    entry = runner.alarm_entries()[0]

    assert runner.acknowledge(entry.id) is Acknowledged.RECORDED
    assert runner.acknowledge(entry.id) is Acknowledged.ALREADY
    assert runner.acknowledge("no-such-alarm") is Acknowledged.UNKNOWN


def test_alarm_entries_and_acknowledge_hold_the_runner_lock():
    runner = brimming_run()
    run_for(runner, 30)
    entry = runner.alarm_entries()[0]
    reached = []

    with runner._lock:
        threads = [
            threading.Thread(target=lambda: reached.append(runner.alarm_entries())),
            threading.Thread(target=lambda: reached.append(runner.acknowledge(entry.id))),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=0.2)

        assert reached == []

    for thread in threads:
        thread.join(timeout=5)

    assert len(reached) == 2


def test_unload_is_refused_while_running_and_otherwise_returns_to_idle():
    runner = started()

    with pytest.raises(ScenarioStateError):
        runner.unload()

    runner.abort()
    runner.unload()

    assert runner.phase is Phase.IDLE

    runner.load_config(scenario())
    runner.unload()

    assert runner.phase is Phase.IDLE

    runner.unload()  # nothing loaded: still IDLE, not an error
    assert runner.phase is Phase.IDLE


def test_abort_rebuilds_the_runtime_so_interlocks_and_alarms_start_clean():
    runner = brimming_run()
    run_for(runner, 30)
    assert not runner.engine.equipment["P-101"].running
    tripped = runner._run.runtime
    assert tripped.trips.interlocks["LSHH-101"].tripped
    taken = len(runner.actions)

    runner.abort()

    fresh = ScenarioRunner()
    fresh.load_config(brimming())
    assert runner._run.runtime is not tripped
    assert not runner._run.runtime.trips.interlocks["LSHH-101"].tripped
    assert runner.alarm_entries() == fresh.alarm_entries()
    assert len(runner.actions) == taken


# ---- scoring a finished run (T20-15) ----


_BANDS = {
    isa_band(severity, side): severity
    for severity in (Severity.WARNING, Severity.ALARM, Severity.TRIP)
    for side in ("lo", "hi")
}


def scored(recovered, config=None):
    """A run taken to completion, with every snapshot it published: the armed
    one, then each step's."""
    runner = ScenarioRunner()
    runner.load_config(config or scenario())
    published = [runner.snapshot()]
    runner.start()

    if recovered:
        recover(runner)

    while runner.phase is Phase.RUNNING:
        published.append(runner.step(DT))

    return runner, published


def tracked_by_hand(published, plant):
    """The scoring trackers for `plant`, fed from `published` outside the run."""
    scoring = load_scoring_config().plant(plant)
    limits = armed_engine().limits
    excursions = {
        label: ExcursionTracker(limits[tuple(label.split("."))].limits) for label in scoring.excursions
    }
    throughput = {label: ThroughputTracker(target) for label, target in scoring.throughput.items()}
    last = published[0].sim_time

    for snapshot in published:
        dt, last = snapshot.sim_time - last, snapshot.sim_time

        for label, tracker in excursions.items():
            tag, variable = label.split(".")
            band = snapshot.envelope.get(label)
            severity = Severity.NORMAL if band is None else _BANDS[band["band"]]
            tracker.update(snapshot.equipment[tag][variable], severity, dt)

        for label, tracker in throughput.items():
            tag, variable = label.split(".")
            tracker.update(snapshot.equipment[tag][variable], dt)

    return excursions, throughput


@pytest.mark.parametrize("recovered", [False, True], ids=["ignored", "recovered"])
def test_a_completed_run_is_scored_from_its_own_log_history_and_trackers(recovered):
    runner, published = scored(recovered)
    excursions, throughput = tracked_by_hand(published, "olefins_lite")

    expected = compute_metrics(
        runner.actions,
        SimpleNamespace(entries=runner.alarm_entries),
        excursions,
        lambda action: action.tag in scenario()["relevant_tags"],
        throughput,
        trips=runner._run.runtime.trip_records(),
        outcome=runner.result().outcome.value,
    )

    result = runner.result()
    assert result.phase is Phase.COMPLETE
    assert result.metrics == expected
    assert result.score == score_run(expected, load_scoring_config())
    assert result.metrics.alarm_count > 0
    assert set(result.metrics.peak_excursions) == {"V-101.level", "K-101.outlet_pressure"}
    assert result.metrics.production_lost["LV-101.flow"] > 0.0


def test_recovering_scores_higher_than_ignoring():
    ignored, _ = scored(recovered=False)
    recovered, _ = scored(recovered=True)

    assert recovered.result().score.total > ignored.result().score.total


def test_no_score_or_metric_while_the_run_is_live():
    runner = ScenarioRunner()

    assert runner.load_config(scenario()).score is None
    assert runner.start().metrics is None

    run_for(runner, 10)

    result = runner.result()
    assert result.phase is Phase.RUNNING
    assert result.score is None and result.metrics is None
    assert result.as_dict()["score"] is None and result.as_dict()["metrics"] is None


def test_an_aborted_run_has_no_score():
    runner = started()
    run_for(runner, 30)

    result = runner.abort()

    assert result.phase is Phase.ABORTED
    assert result.score is None and result.metrics is None


def test_a_completed_score_is_final():
    runner, _ = scored(recovered=True)
    score = runner.result().score

    runner.step(DT)
    runner.acknowledge(runner.alarm_entries()[0].id)

    assert runner.result().score == score


def test_replaying_a_run_gives_the_same_score():
    runner, _ = scored(recovered=True)

    replayed = replay(Recording.of(runner)).result()

    assert replayed.score == runner.result().score
    assert replayed.metrics == runner.result().metrics


def test_the_score_is_in_the_json_result():
    runner, _ = scored(recovered=True)

    body = json.loads(json.dumps(runner.result().as_dict()))

    assert body["score"]["total"] == runner.result().score.total
    assert set(body["score"]["penalties"]) == set(load_scoring_config().weights)
    assert body["metrics"]["production_lost"]["LV-101.flow"] > 0.0


# ---- which actions were relevant (T15-5) ----


def first_alarm(runner):
    """The run's first alarm, in scenario time."""
    return min(entry.sim_time for entry in runner.alarm_entries()) - runner.result().started_at


def test_an_action_on_an_unlisted_tag_is_unnecessary_and_one_on_a_listed_tag_is_not():
    runner = started()
    runner.act("PIC-101", "set_setpoint", 200.0)
    runner.act("P-101", "start", None)
    runner.act("P-101", "set_speed_target", 1.0)
    run_out(runner)

    assert runner.result().outcome is Outcome.SUCCEEDED
    assert runner.result().metrics.unnecessary_actions == 1


def test_the_result_names_each_unnecessary_action_in_log_order():
    runner = started()
    run_for(runner, 2)
    first = runner.act("PIC-101", "set_setpoint", 200.0)
    runner.act("P-101", "start", None)
    second = runner.act("PIC-101", "manual", None)
    runner.act("P-101", "set_speed_target", 1.0)
    run_out(runner)

    result = runner.result()
    assert result.unnecessary_actions == (
        {"sim_time": first, "tag": "PIC-101", "action": "set_setpoint", "value": 200.0},
        {"sim_time": second, "tag": "PIC-101", "action": "manual", "value": None},
    )
    assert len(result.unnecessary_actions) == result.metrics.unnecessary_actions
    body = json.loads(json.dumps(result.as_dict()))
    assert body["unnecessary_actions"] == [dict(row) for row in result.unnecessary_actions]


def test_a_run_with_only_listed_actions_names_none():
    runner = started()
    recover(runner)
    run_out(runner)

    assert runner.result().unnecessary_actions == ()
    assert runner.result().as_dict()["unnecessary_actions"] == []


def test_no_unnecessary_actions_are_named_before_the_run_is_scored():
    runner = started()
    runner.act("PIC-101", "set_setpoint", 200.0)

    assert runner.result().unnecessary_actions is None
    assert runner.result().as_dict()["unnecessary_actions"] is None

    runner.abort()

    assert runner.result().as_dict()["unnecessary_actions"] is None


def test_recognition_waits_for_the_first_action_on_a_listed_tag():
    runner = started()
    run_for(runner, 5)
    runner.act("PIC-101", "set_setpoint", 200.0)
    run_for(runner, 5)
    relevant_at = runner.act("P-101", "start", None)
    runner.act("P-101", "set_speed_target", 1.0)
    run_out(runner)

    alarmed_at = first_alarm(runner)
    assert alarmed_at < 5.0
    assert runner.result().metrics.time_to_recognise_s == pytest.approx(relevant_at - alarmed_at)


def test_a_run_with_only_unlisted_actions_after_the_alarm_was_never_recognised():
    runner = started()
    run_for(runner, 5)
    runner.act("PIC-101", "set_setpoint", 200.0)
    run_out(runner)

    metrics = runner.result().metrics
    assert metrics.alarm_count > 0
    assert metrics.unnecessary_actions == 1
    assert metrics.time_to_recognise_s is None


# ---- the outcome and the run's own trips (T20-21) ----


def run_shipped(scenario_id, recover_at=None):
    runner = ScenarioRunner()
    runner.load(scenario_id)
    runner.start()

    if recover_at is not None:
        run_for(runner, recover_at)
        recover(runner)

    run_out(runner)

    return runner


@pytest.mark.parametrize(
    "scenario_id", ["compressor_trip", "restricted_discharge", "loss_of_feed", "blocked_drain"]
)
def test_an_ignored_failing_scenario_scores_at_most_fifty(scenario_id):
    result = run_shipped(scenario_id).result()

    assert result.outcome is not Outcome.SUCCEEDED
    assert result.metrics.outcome == result.outcome.value
    assert result.score.total <= 50.0


def test_a_prompt_recovery_of_the_pump_trip_scores_above_ninety():
    runner = run_shipped("pump_trip", recover_at=10)
    result = runner.result()

    assert result.outcome is Outcome.SUCCEEDED
    assert result.metrics.trip_count == 0
    assert result.metrics.alarm_count > 0
    assert result.score.total > 90.0


def test_a_latched_interlock_counts_one_trip_however_long_it_stays_latched():
    runner = brimming_run()

    run_for(runner, 120)

    runtime = runner._run.runtime
    assert runtime.trips.interlocks["LSHH-101"].tripped
    assert [record.tag for record in runtime.trip_records()] == ["LSHH-101"]


def scoring_library(tmp_path, plants):
    document = yaml.safe_load((CONFIG / "scoring.yaml").read_text())
    document["plants"] = plants
    for entry in plants.values():
        document["production_lost_scales"].update(dict.fromkeys(entry.get("throughput", {}), 1.0))
    (tmp_path / "scoring.yaml").write_text(yaml.safe_dump(document))

    return ScenarioLibrary(scoring=tmp_path / "scoring.yaml")


def test_a_plant_with_no_scoring_entry_scores_on_its_log_and_history_alone(tmp_path):
    runner = ScenarioRunner(scoring_library(tmp_path, {}))
    runner.load_config(scenario())
    runner.start()
    run_out(runner)

    metrics = runner.result().metrics
    assert metrics.alarm_count > 0
    assert dict(metrics.peak_excursions) == {}
    assert dict(metrics.production_lost) == {}
    assert metrics.time_outside_envelope_s == 0.0


@pytest.mark.parametrize(
    "plants, mention",
    [
        ({"olefins_lite": {"excursions": ["V-101.pressure"]}}, "no configured limit"),
        ({"olefins_lite": {"excursions": ["X-999.level"]}}, "no configured limit"),
        ({"olefins_lite": {"throughput": {"LV-101.nope": 1.0}}}, "no number"),
        ({"olefins_lite": {"throughput": {"X-999.flow": 1.0}}}, "no number"),
    ],
    ids=["unlimited point", "unknown limit tag", "unpublished field", "unknown throughput tag"],
)
def test_a_scoring_point_the_plant_lacks_refuses_the_load(tmp_path, plants, mention):
    runner = ScenarioRunner(scoring_library(tmp_path, plants))

    with pytest.raises(ValueError, match=mention):
        runner.load_config(scenario())

    assert runner.phase is Phase.IDLE
