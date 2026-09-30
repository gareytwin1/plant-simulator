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
import dataclasses
import json
import shutil
import time
from pathlib import Path

import pytest

from app.api.action import apply_action
from app.disturbances.malfunction import MalfunctionRegistry
from app.engine.engine import Engine
from app.engine.persistence import capture_state
from app.scenarios.replay import Act, Recording, RecordingFormatError, ReplayDivergence, replay
from app.scenarios.runner import Outcome, Phase, ScenarioLibrary, ScenarioRunner, Tick, TickKind


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

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


def test_a_pause_and_speed_change_after_the_last_step_are_replayed():
    live = started()
    steps(live, 5)
    live.engine.clock.set_speed(3.0)
    live.engine.clock.pause()

    replayed = replay(Recording.of(live))

    assert replayed.engine.clock.paused
    assert_identical(live, replayed)


def fail_coupling_at(monkeypatch, sim_time, message="coupling failed"):
    """Make the coupling raise on the step that reaches `sim_time` - after the
    clock has advanced and every device has integrated, so the failed step
    has already moved the plant."""
    couple = Engine._couple

    def couple_unless(self):
        if self.clock.sim_time == sim_time:
            raise ValueError(message)

        couple(self)

    monkeypatch.setattr(Engine, "_couple", couple_unless)


def run_through_a_failed_step(monkeypatch):
    live = started()
    steps(live, 2)
    fail_coupling_at(monkeypatch, live.engine.clock.sim_time + DT)

    with pytest.raises(ValueError, match="coupling failed"):
        live.step(DT)

    return live


def test_a_run_that_carried_on_past_a_failed_step_replays_exactly(monkeypatch):
    live = run_through_a_failed_step(monkeypatch)
    steps(live, 4)
    live.act("P-101", "start", None)
    steps(live, 4)
    live.abort()

    recording = Recording.of(live)
    replayed = replay(recording)

    failed = [item for item in recording.inputs if isinstance(item, Tick) and item.error is not None]
    assert failed == [Tick(TickKind.STEP, 0, dt=DT, speed=1.0, paused=False, error="ValueError: coupling failed")]
    assert replayed.phase is Phase.ABORTED
    assert_identical(live, replayed)


def test_a_failed_step_survives_a_json_round_trip_and_still_replays_exactly(monkeypatch):
    live = run_through_a_failed_step(monkeypatch)
    steps(live, 4)

    recording = Recording.from_dict(json.loads(json.dumps(Recording.of(live).as_dict())))

    assert any(isinstance(item, Tick) and item.error for item in recording.inputs)
    assert_identical(live, replay(recording))


def test_a_run_that_carried_on_past_a_failed_start_replays_exactly(monkeypatch):
    # The start's malfunction update fails after the run is already RUNNING,
    # so the run carries on without its time-zero malfunction.
    update = MalfunctionRegistry.update

    def update_unless_at_zero(self, snapshot):
        if snapshot.sim_time == 0.0:
            raise ValueError("malfunction update failed")

        update(self, snapshot)

    monkeypatch.setattr(MalfunctionRegistry, "update", update_unless_at_zero)
    live = ScenarioRunner()
    live.load_config(scenario(malfunctions=[{"target_tag": "LV-101", "parameter": "capacity", "value": 10.0}]))

    with pytest.raises(ValueError, match="malfunction update failed"):
        live.start()

    live.act("P-101", "start", None)
    steps(live, 10)

    recording = Recording.of(live)
    replayed = replay(recording)

    assert recording.inputs[0] == Tick(TickKind.START, 0, error="ValueError: malfunction update failed")
    assert_identical(live, replayed)


def test_a_recorded_error_that_does_not_recur_is_a_divergence(monkeypatch):
    recording = Recording.of(run_through_a_failed_step(monkeypatch))
    monkeypatch.undo()

    with pytest.raises(ReplayDivergence, match=r"inputs\[3\]: step was recorded raising .* but did not"):
        replay(recording)


def test_a_different_error_where_one_was_recorded_is_a_divergence(monkeypatch):
    live = run_through_a_failed_step(monkeypatch)
    recording = Recording.of(live)
    fail_coupling_at(monkeypatch, live.engine.clock.sim_time, message="something else")

    with pytest.raises(ReplayDivergence, match="but raised 'ValueError: something else'"):
        replay(recording)


def test_a_plant_error_during_replay_propagates_as_itself(monkeypatch):
    live = started()
    steps(live, 2)
    recording = Recording.of(live)

    def broken(self, dt):
        raise ValueError("solver blew up")

    monkeypatch.setattr(Engine, "step", broken)

    with pytest.raises(ValueError, match="solver blew up") as raised:
        replay(recording)

    assert not isinstance(raised.value, ReplayDivergence)


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


CLOCK = {"speed": 1.0, "paused": False}


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"scenario": {}, "inputs": []},
        {"scenario": {}, "inputs": {}, "clock": CLOCK, "fingerprint": None},
        {"scenario": {}, "inputs": [], "clock": {"speed": 1.0}},
        {"scenario": {}, "inputs": [], "clock": {"speed": "fast", "paused": False}},
        {"scenario": {}, "inputs": [], "clock": {"speed": 1.0, "paused": 0}},
        {"scenario": {}, "inputs": [], "clock": CLOCK, "fingerprint": 7},
        {"scenario": {}, "inputs": [{"type": "jump"}], "clock": CLOCK, "fingerprint": None},
        {"scenario": {}, "inputs": [{"type": "start"}], "clock": CLOCK, "fingerprint": None},
        {"scenario": {}, "inputs": [{"type": "start", "actions": -1, "error": None}], "clock": CLOCK, "fingerprint": None},
        {"scenario": {}, "inputs": [{"type": "start", "actions": True, "error": None}], "clock": CLOCK, "fingerprint": None},
        {"scenario": {}, "inputs": [{"type": "start", "actions": 0, "error": 1}], "clock": CLOCK, "fingerprint": None},
        {
            "scenario": {},
            "inputs": [{"type": "step", "actions": 0, "error": None, "dt": "1", "speed": 1, "paused": False}],
            "clock": CLOCK,
            "fingerprint": None,
        },
        {
            "scenario": {},
            "inputs": [{"type": "step", "actions": 0, "error": None, "dt": 1, "speed": 1, "paused": 0}],
            "clock": CLOCK,
            "fingerprint": None,
        },
        {
            "scenario": {},
            "inputs": [{"type": "act", "target": 1, "action": "start", "value": None, "sim_time": 0}],
            "clock": CLOCK,
            "fingerprint": None,
        },
        {
            "scenario": {},
            "inputs": [{"type": "act", "target": "P-101", "action": "start", "value": None}],
            "clock": CLOCK,
            "fingerprint": None,
        },
    ],
)
def test_a_malformed_recording_is_refused(document):
    with pytest.raises(RecordingFormatError):
        Recording.from_dict(document)


def test_a_well_formed_recording_document_is_accepted():
    document = {
        "scenario": {},
        "inputs": [
            {"type": "act", "target": "P-101", "action": "start", "value": None, "sim_time": 0},
            {"type": "start", "actions": 1, "error": None},
            {"type": "step", "actions": 1, "error": "ValueError: boom", "dt": 1, "speed": 1, "paused": False},
            {"type": "abort", "actions": 1, "error": None},
        ],
        "clock": CLOCK,
            "fingerprint": None,
    }

    assert len(Recording.from_dict(document).inputs) == 4


# ---- a replay that stops reproducing says so ----


def copied_library(tmp_path):
    for name in ("plants", "initial_conditions"):
        shutil.copytree(CONFIG / name, tmp_path / name)

    return ScenarioLibrary(
        scenarios=tmp_path / "scenarios",
        plants=tmp_path / "plants",
        conditions=tmp_path / "initial_conditions",
    )


def test_a_replay_against_a_changed_initial_condition_is_refused(tmp_path):
    library = copied_library(tmp_path)
    live = ScenarioRunner(library)
    live.load_config(scenario())
    live.start()
    steps(live, 5)
    recording = Recording.of(live)

    condition = tmp_path / "initial_conditions" / "feed_pump_trip.json"
    state = json.loads(condition.read_text())
    state["equipment"]["V-101"]["_level"] += 0.01
    condition.write_text(json.dumps(state))

    with pytest.raises(ReplayDivergence, match="has changed since this run was recorded"):
        replay(recording, library)


def test_a_replay_against_a_changed_plant_file_is_refused(tmp_path):
    library = copied_library(tmp_path)
    live = ScenarioRunner(library)
    live.load_config(scenario())
    recording = Recording.of(live)

    plant = tmp_path / "plants" / "olefins_lite.yaml"
    plant.write_text(plant.read_text() + "\n# edited\n")

    with pytest.raises(ReplayDivergence, match="has changed since this run was recorded"):
        replay(recording, library)


def edited(recording, index, **changes):
    inputs = list(recording.inputs)
    inputs[index] = dataclasses.replace(inputs[index], **changes)

    return dataclasses.replace(recording, inputs=tuple(inputs))


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
        replay(dataclasses.replace(recording, inputs=recording.inputs + (extra,)))


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


RNG_MODULE = "app.engine.rng"


def imported_modules(path, package):
    """Every module `path` imports, relative imports resolved against
    `package`, and `from X import Y` counted as both X and X.Y."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)

        if isinstance(node, ast.ImportFrom):
            base = package.split(".")[: len(package.split(".")) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)


def imports_rng(path, package="app"):
    return any(
        name == RNG_MODULE or name.startswith(f"{RNG_MODULE}.")
        for name in imported_modules(path, package)
    )


def package_of(path):
    return ".".join(path.relative_to(APP.parent).parent.parts)


def test_nothing_in_the_plant_draws_a_random_number_a_recording_would_miss():
    users = [
        str(path.relative_to(APP))
        for path in sorted(APP.rglob("*.py"))
        if path != RNG and imports_rng(path, package_of(path))
    ]

    assert users == [], (
        f"{users} use a SeededRNG: a recording carries no generator state, so "
        f"replay (app/scenarios/replay.py) must capture and restore it before "
        f"a random draw can reach a scenario run"
    )


def test_the_rng_guard_catches_each_way_of_importing_the_generator(tmp_path):
    module = tmp_path / "m.py"

    for source, package in (
        ("from app.engine.rng import SeededRNG", "app.scenarios"),
        ("import app.engine.rng", "app.scenarios"),
        ("import app.engine.rng as generator", "app.scenarios"),
        ("from app.engine import rng", "app.scenarios"),
        ("from .rng import SeededRNG", "app.engine"),
        ("from . import rng", "app.engine"),
        ("from ..engine.rng import SeededRNG", "app.scenarios"),
        ("from ..engine import rng", "app.scenarios"),
    ):
        module.write_text(source)
        assert imports_rng(module, package), source

    for source, package in (
        ("from app.engine.rngs import other", "app.scenarios"),
        ("from app.engine import clock", "app.scenarios"),
        ("from .rng import SeededRNG", "app.scenarios"),
    ):
        module.write_text(source)
        assert not imports_rng(module, package), source


def test_the_rng_guard_names_each_module_by_its_package():
    assert package_of(APP / "engine" / "engine.py") == "app.engine"
    assert package_of(APP / "scenarios" / "replay.py") == "app.scenarios"
