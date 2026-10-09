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

import pytest

from app.alarms.acknowledge import Acknowledged
from app.api.action import apply_action
from app.engine.engine import Engine
from app.engine.persistence import capture_state, restore_state
from app.engine.scheduler import Scheduler
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
