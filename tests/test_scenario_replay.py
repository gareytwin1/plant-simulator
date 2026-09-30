"""
Deterministic replay - T14-5. The build-plan tests come first: the same
scenario, seed and action log give an identical final state and identical
result metrics, faster than real time.

Every run here is the `feed_pump_trip` scenario from test_scenario_runner.py:
ignored, V-101 drains to its failure in about 90 s; recovered, it passes 0.25
in about 330 s. "Identical" means `==` on the captured state, the snapshot and
the result, never `approx` - a replay that is merely close is a different run.
"""

import ast
import json
import time
from pathlib import Path

import pytest

from app.api.action import apply_action
from app.engine.persistence import capture_state
from app.scenarios.replay import Act, Recording, RecordingFormatError, ReplayDivergence, replay
from app.scenarios.runner import Outcome, Phase, ScenarioRunner, Tick, TickKind


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

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


def started(config=None):
    runner = ScenarioRunner()
    runner.load_config(config or scenario())
    runner.start()

    return runner


def run_out(runner, dt=DT, limit=2000):
    for _ in range(limit):
        if runner.phase is not Phase.RUNNING:
            return

        runner.step(dt)

    raise AssertionError(f"still running after {limit} steps")


def steps(runner, count, dt=DT):
    for _ in range(count):
        runner.step(dt)


def recovered_run():
    runner = started()
    steps(runner, 12)
    runner.act("P-101", "start", None)
    steps(runner, 3)
    runner.act("P-101", "set_speed_target", 1.0)
    run_out(runner)

    return runner


def assert_identical(live, replayed):
    assert capture_state(replayed.engine) == capture_state(live.engine)
    assert replayed.snapshot().as_dict() == live.snapshot().as_dict()
    assert replayed.result().as_dict() == live.result().as_dict()


# ---- the build-plan tests ----


def test_same_scenario_seed_and_action_log_gives_identical_final_state():
    live = recovered_run()

    replayed = replay(Recording.of(live))

    assert capture_state(replayed.engine) == capture_state(live.engine)
    assert replayed.snapshot().as_dict() == live.snapshot().as_dict()


def test_replay_gives_identical_result_metrics():
    live = recovered_run()

    replayed = replay(Recording.of(live))

    assert live.result().outcome is Outcome.SUCCEEDED
    assert replayed.result().as_dict() == live.result().as_dict()


def test_replay_is_faster_than_real_time():
    live = recovered_run()
    recording = Recording.of(live)

    began = time.perf_counter()
    replayed = replay(recording)
    wall = time.perf_counter() - began

    assert replayed.result().elapsed_s > 300
    assert wall < replayed.result().elapsed_s


# ---- what a recording carries ----


def test_an_ignored_run_replays_to_the_same_failure():
    live = started()
    run_out(live)

    replayed = replay(Recording.of(live))

    assert live.result().outcome is Outcome.FAILED
    assert_identical(live, replayed)


def test_the_recording_interleaves_actions_with_the_ticks_they_fell_between():
    live = started()
    steps(live, 2)
    live.act("P-101", "start", None)
    live.step(DT)

    inputs = Recording.of(live).inputs

    assert inputs == (
        Tick(TickKind.START, 0),
        Tick(TickKind.STEP, 0, dt=DT, speed=1.0, paused=False),
        Tick(TickKind.STEP, 0, dt=DT, speed=1.0, paused=False),
        Act("P-101", "start", None, sim_time=pytest.approx(2.0)),
        Tick(TickKind.STEP, 1, dt=DT, speed=1.0, paused=False),
    )


def test_an_action_before_start_replays_before_the_time_zero_malfunction():
    # LV-101's capacity is written by the malfunction on start; an action
    # taken while armed lands on the undisturbed plant, at the same sim time.
    config = scenario(malfunctions=[{"target_tag": "LV-101", "parameter": "capacity", "value": 10.0}])
    live = ScenarioRunner()
    live.load_config(config)
    live.act("LV-101", "set_position_target", 0.3)
    live.start()
    live.act("P-101", "start", None)
    steps(live, 40)

    recording = Recording.of(live)
    replayed = replay(recording)

    assert [type(item).__name__ for item in recording.inputs[:3]] == ["Act", "Tick", "Act"]
    assert_identical(live, replayed)


def test_uneven_steps_speed_changes_and_pauses_replay_exactly():
    live = started()
    steps(live, 5, dt=0.5)
    live.engine.clock.set_speed(4.0)
    steps(live, 5, dt=0.25)
    live.engine.clock.pause()
    steps(live, 3)
    live.act("P-101", "start", None)
    live.act("P-101", "set_speed_target", 1.0)
    live.engine.clock.resume()
    live.engine.clock.set_speed(1.0)
    run_out(live, dt=0.7)

    replayed = replay(Recording.of(live))

    assert_identical(live, replayed)


def test_an_action_applied_straight_to_the_runner_log_is_replayed_too():
    live = started()
    steps(live, 5)
    engine = live.engine
    apply_action(engine.equipment, live.actions, engine.clock.sim_time, "P-101", "start", None)
    apply_action(engine.equipment, live.actions, engine.clock.sim_time, "P-101", "set_speed_target", 1.0)
    run_out(live)

    replayed = replay(Recording.of(live))

    assert replayed.result().outcome is Outcome.SUCCEEDED
    assert_identical(live, replayed)


def test_an_aborted_run_replays_to_the_same_abort():
    live = started()
    live.act("P-101", "start", None)
    steps(live, 20)
    live.abort()

    replayed = replay(Recording.of(live))

    assert replayed.phase is Phase.ABORTED
    assert_identical(live, replayed)


def test_triggers_fire_at_the_same_times_in_a_replay():
    config = scenario(
        triggers=[
            {"id": "ten-seconds", "type": "time", "sim_time": 10},
            {"id": "restarted", "type": "operator_action", "action": "P-101.start"},
        ],
    )
    live = started(config)
    steps(live, 3)
    live.act("P-101", "start", None)
    steps(live, 10)

    replayed = replay(Recording.of(live))

    assert replayed.result().triggers_fired == live.result().triggers_fired
    assert_identical(live, replayed)


def test_the_recording_does_not_share_the_scenario_document_it_was_loaded_from():
    config = scenario()
    live = ScenarioRunner()
    live.load_config(config)
    live.start()
    steps(live, 5)
    config["time_limit_s"] = 1

    recording = Recording.of(live)

    assert recording.scenario["time_limit_s"] == 900


# ---- the recording as a document ----


def test_a_recording_survives_a_json_round_trip_and_still_replays_exactly():
    live = started()
    steps(live, 3, dt=0.3)
    live.act("P-101", "start", None)
    live.act("P-101", "set_speed_target", 1.0)
    run_out(live, dt=0.3)
    recording = Recording.of(live)

    restored = Recording.from_dict(json.loads(json.dumps(recording.as_dict())))

    assert restored == recording
    assert_identical(live, replay(restored))


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"scenario": {}},
        {"scenario": {}, "inputs": {}},
        {"scenario": {}, "inputs": [{"type": "jump"}]},
        {"scenario": {}, "inputs": [{"type": "start"}]},
        {"scenario": {}, "inputs": [{"type": "start", "actions": -1}]},
        {"scenario": {}, "inputs": [{"type": "start", "actions": True}]},
        {"scenario": {}, "inputs": [{"type": "step", "actions": 0, "dt": "1", "speed": 1, "paused": False}]},
        {"scenario": {}, "inputs": [{"type": "step", "actions": 0, "dt": 1, "speed": 1, "paused": 0}]},
        {"scenario": {}, "inputs": [{"type": "act", "target": 1, "action": "start", "value": None, "sim_time": 0}]},
        {"scenario": {}, "inputs": [{"type": "act", "target": "P-101", "action": "start", "value": None}]},
    ],
)
def test_a_malformed_recording_is_refused(document):
    with pytest.raises(RecordingFormatError):
        Recording.from_dict(document)


# ---- a replay that stops reproducing says so ----


def edited(recording, index, **changes):
    item = recording.inputs[index]
    row = {**{field: getattr(item, field) for field in item.__dataclass_fields__}, **changes}
    inputs = list(recording.inputs)
    inputs[index] = type(item)(**row)

    return Recording(recording.scenario, tuple(inputs))


def test_an_action_replayed_at_another_time_is_a_divergence():
    live = started()
    steps(live, 4)
    live.act("P-101", "start", None)
    recording = Recording.of(live)
    index = next(i for i, item in enumerate(recording.inputs) if isinstance(item, Act))

    with pytest.raises(ReplayDivergence, match=rf"inputs\[{index}\].*recorded at t=5"):
        replay(edited(recording, index, sim_time=5.0))


def test_a_step_cut_differently_is_a_divergence():
    live = started()
    steps(live, 4)
    live.act("P-101", "start", None)

    with pytest.raises(ReplayDivergence, match="replayed at t=5"):
        replay(edited(Recording.of(live), 1, dt=2.0))


def test_a_step_past_the_end_of_the_run_is_a_divergence():
    live = started()
    run_out(live)
    recording = Recording.of(live)
    extra = Tick(TickKind.STEP, 0, dt=DT, speed=1.0, paused=False)

    with pytest.raises(ReplayDivergence, match="replay is complete"):
        replay(Recording(recording.scenario, recording.inputs + (extra,)))


def test_a_tick_reached_with_a_different_action_count_is_a_divergence():
    live = started()
    steps(live, 2)

    with pytest.raises(ReplayDivergence, match="recorded after 1 actions, reached after 0"):
        replay(edited(Recording.of(live), 1, actions=1))


def test_an_action_the_runner_refuses_is_a_divergence():
    live = started()
    live.act("P-101", "start", None)
    recording = edited(Recording.of(live), 1, target="X-999")

    with pytest.raises(ReplayDivergence, match="refused"):
        replay(recording)


# ---- the one input a recording does not carry ----


APP = Path(__file__).resolve().parent.parent / "app"
RNG = APP / "engine" / "rng.py"


def imports_rng(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module == "app.engine.rng":
            return True

        if isinstance(node, ast.Import) and any(alias.name == "app.engine.rng" for alias in node.names):
            return True

    return False


def test_nothing_in_the_plant_draws_a_random_number_a_recording_would_miss():
    users = [str(path.relative_to(APP)) for path in sorted(APP.rglob("*.py")) if path != RNG and imports_rng(path)]

    assert users == [], (
        f"{users} use a SeededRNG: a recording carries no generator state, so "
        f"replay (app/scenarios/replay.py) must capture and restore it before "
        f"a random draw can reach a scenario run"
    )


def test_the_rng_guard_catches_each_way_of_importing_the_generator(tmp_path):
    module = tmp_path / "m.py"

    for source in ("from app.engine.rng import SeededRNG", "import app.engine.rng"):
        module.write_text(source)
        assert imports_rng(module), source

    module.write_text("from app.engine.rngs import other\n")
    assert not imports_rng(module)
