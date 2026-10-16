"""
Authored scenario pack - T14-6, and the separator's four (T20-18). The
build-plan tests: every scenario in `config/scenarios/` loads, runs and
completes; is solved by a scripted correct response; is not solved by doing
nothing; and, for the separator's, replays bit-identically.

Each script is what an operator would do once the diagnosis written in the
scenario's `description` is made: `{scenario time: [(device, action[, value])]}`.
Scenario time is seconds since the run began.
"""

import json
from pathlib import Path

import pytest
import yaml

from app.api.action import apply_action
from app.engine.persistence import capture_state
from app.plant.validate import validate
from app.scenarios.replay import Recording, replay
from app.scenarios.runner import Outcome, Phase, ScenarioRunner


CONFIG = Path(__file__).resolve().parent.parent / "config"
SCENARIOS = CONFIG / "scenarios"
SCHEMA = json.loads((CONFIG / "schema" / "scenario.schema.json").read_text())
DT = 1.0

RESTART_PUMP = [("P-101", "start"), ("P-101", "set_speed_target", 1.0)]

CORRECT_RESPONSES = {
    "pump_trip": {60: RESTART_PUMP},
    "compressor_trip": {60: [("K-101", "start"), ("K-101", "set_load_target", 1.0)]},
    "loss_of_feed": {60: [("LV-101", "set_position_target", 0.1)]},
    "blocked_drain": {60: [("LV-101", "set_position_target", 1.0)]},
    "restricted_discharge": {60: [("FV-201", "set_position_target", 1.0)]},
    "stuck_drain_valve": {5: [("LV-101", "set_position_target", 0.0)] + RESTART_PUMP},
    # The separator's faults land at 30 s; each response comes 30 s later.
    "separator_drain_restriction": {60: [("FIC-301", "set_setpoint", 30.0)]},
    "separator_vent_restriction": {60: [("FIC-301", "set_setpoint", 25.0)]},
    "separator_feed_signal_loss": {60: [("XV-301", "set_position_target", 0.22)]},
    "separator_drain_signal_loss": {60: [("FIC-301", "set_setpoint", 70.0)]},
}

IDS = sorted(CORRECT_RESPONSES)
SEPARATOR_IDS = [scenario_id for scenario_id in IDS if scenario_id.startswith("separator_")]


def run(scenario_id, script=None, limit=2000):
    runner = ScenarioRunner()
    runner.load(scenario_id)
    runner.start()
    script = script or {}
    engine = runner.engine

    for tick in range(limit):
        if runner.phase is not Phase.RUNNING:
            break

        for target, action, *value in script.get(tick, ()):
            apply_action(
                engine.equipment, runner.actions, engine.clock.sim_time,
                target, action, value[0] if value else None, loops=engine.loops,
            )

        runner.step(DT)
    else:
        raise AssertionError(f"{scenario_id} still running after {limit} steps")

    return runner


def plants():
    found = {}

    for path in SCENARIOS.glob("*.yaml"):
        found.setdefault(yaml.safe_load(path.read_text())["plant"], set()).add(path.stem)

    return found


def test_each_plants_pack_is_four_to_eight_scenarios_each_with_a_script():
    files = sorted(path.stem for path in SCENARIOS.glob("*.yaml"))

    assert files == IDS
    assert set(plants()["separator"]) == set(SEPARATOR_IDS)
    for plant, ids in plants().items():
        assert 4 <= len(ids) <= 8, plant


def test_olefins_lite_keeps_its_six():
    assert plants()["olefins_lite"] == {
        "blocked_drain", "compressor_trip", "loss_of_feed",
        "pump_trip", "restricted_discharge", "stuck_drain_valve",
    }


@pytest.mark.parametrize("scenario_id", IDS)
def test_each_scenario_file_is_valid_and_documents_its_diagnosis(scenario_id):
    config = yaml.safe_load((SCENARIOS / f"{scenario_id}.yaml").read_text())

    assert validate(config, SCHEMA) == []
    assert config["id"] == scenario_id
    assert "Diagnosis path" in config["description"]
    assert config["objectives"], "an exercise needs something to be scored on"


@pytest.mark.parametrize("scenario_id", IDS)
def test_each_scenario_loads_and_arms_without_moving_the_plant(scenario_id):
    runner = ScenarioRunner()

    result = runner.load(scenario_id)

    assert runner.phase is Phase.LOADED
    assert result.elapsed_s == pytest.approx(0.0)


@pytest.mark.parametrize("scenario_id", IDS)
def test_each_scenario_runs_to_completion_under_the_correct_response(scenario_id):
    runner = run(scenario_id, CORRECT_RESPONSES[scenario_id])

    result = runner.result()
    assert result.phase is Phase.COMPLETE
    assert result.outcome is Outcome.SUCCEEDED
    assert result.elapsed_s < result.time_limit_s


@pytest.mark.parametrize("scenario_id", IDS)
def test_each_scenario_fails_with_no_response(scenario_id):
    runner = run(scenario_id)

    result = runner.result()
    assert result.phase is Phase.COMPLETE
    assert result.outcome is Outcome.FAILED
    assert result.elapsed_s < result.time_limit_s


def test_a_stuck_valve_ignores_the_demand_to_close():
    runner = run("stuck_drain_valve", CORRECT_RESPONSES["stuck_drain_valve"])

    valve = runner.engine.equipment["LV-101"]
    assert valve.position == pytest.approx(0.5)
    assert valve.position_target < valve.position


def test_a_lost_feed_signal_opens_fv_301_against_its_loop_until_xv_301_takes_the_feed():
    runner = run("separator_feed_signal_loss", CORRECT_RESPONSES["separator_feed_signal_loss"])

    engine = runner.engine
    assert engine.equipment["FV-301"].signal_ok is False
    assert engine.equipment["FV-301"].position == 1.0
    assert engine.equipment["XV-301"].position == pytest.approx(0.22)
    assert runner.result().outcome is Outcome.SUCCEEDED


def test_a_lost_drain_signal_opens_lv_301_against_its_loop():
    runner = run("separator_drain_signal_loss", CORRECT_RESPONSES["separator_drain_signal_loss"])

    valve = runner.engine.equipment["LV-301"]
    assert valve.signal_ok is False
    assert valve.position == 1.0
    assert runner.result().outcome is Outcome.SUCCEEDED


def played(scenario_id):
    """A run along the diagnosis path, its actions taken through the runner
    so the recording carries them."""
    runner = ScenarioRunner()
    runner.load(scenario_id)
    runner.start()
    script = CORRECT_RESPONSES[scenario_id]

    for tick in range(2000):
        if runner.phase is not Phase.RUNNING:
            return runner

        for target, action, *value in script.get(tick, ()):
            runner.act(target, action, value[0] if value else None)

        runner.step(DT)

    raise AssertionError(f"{scenario_id} still running after 2000 steps")


@pytest.mark.parametrize("scenario_id", SEPARATOR_IDS)
def test_each_separator_scenario_replays_bit_identically(scenario_id):
    live = played(scenario_id)

    replayed = replay(Recording.of(live))

    assert live.result().outcome is Outcome.SUCCEEDED
    assert capture_state(replayed.engine) == capture_state(live.engine)
    assert replayed.snapshot().as_dict() == live.snapshot().as_dict()
    assert replayed.result().as_dict() == live.result().as_dict()
