"""Scenario API - T14-4, contract C5's scenario routes."""

import pytest
from flask import Flask

from app.api.action import create_action_blueprint
from app.api.scenario import create_scenario_blueprint
from app.scenarios.runner import Phase, ScenarioLibrary, ScenarioRunner


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

SCENARIO = (
    "id: pump-trip\n"
    "plant: olefins_lite\n"
    "initial_condition: {condition: feed_pump_trip}\n"
    "objectives:\n"
    "  - id: recover-level\n"
    "    success: {condition: 'V-101.level >= 0.25', hold_duration_s: 20}\n"
    "    failure: {condition: 'V-101.level <= 0.1'}\n"
    "time_limit_s: 900\n"
    "difficulty: easy\n"
    "seed: 7\n"
)


@pytest.fixture
def api(tmp_path):
    (tmp_path / "pump-trip.yaml").write_text(SCENARIO)
    (tmp_path / "bad-tag.yaml").write_text(
        SCENARIO.replace("V-101.level >= 0.25", "X-999.level >= 0.25").replace("pump-trip", "bad-tag"),
    )
    runner = ScenarioRunner(ScenarioLibrary(scenarios=tmp_path))

    app = Flask(__name__)
    app.register_blueprint(create_scenario_blueprint(lambda: runner))
    app.register_blueprint(create_action_blueprint(lambda: runner.engine, lambda: runner.actions, runner.act))

    return app.test_client(), runner


def test_load_answers_with_the_armed_result(api):
    client, runner = api

    response = client.post("/api/scenario/load", json={"scenario": "pump-trip"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["scenario_id"] == "pump-trip"
    assert body["phase"] == "loaded"
    assert body["outcome"] is None
    assert body["elapsed_s"] == pytest.approx(0.0)
    assert runner.phase is Phase.LOADED


def test_a_full_run_over_http_from_load_to_a_succeeded_result(api):
    client, runner = api
    client.post("/api/scenario/load", json={"scenario": "pump-trip"})

    started = client.post("/api/scenario/start")
    assert started.get_json()["phase"] == "running"

    assert client.post("/api/action", json={"target": "P-101", "action": "start"}).status_code == 200
    assert client.post(
        "/api/action", json={"target": "P-101", "action": "set_speed_target", "value": 1.0},
    ).status_code == 200

    while runner.phase is Phase.RUNNING:
        runner.step(1.0)

    body = client.get("/api/scenario/result").get_json()
    assert body["phase"] == "complete"
    assert body["outcome"] == "succeeded"
    assert [action["action"] for action in body["actions"]] == ["start", "set_speed_target"]


def test_abort_over_http(api):
    client, _ = api
    client.post("/api/scenario/load", json={"scenario": "pump-trip"})
    client.post("/api/scenario/start")

    response = client.post("/api/scenario/abort")

    assert response.status_code == 200
    assert response.get_json()["outcome"] == "aborted"


def test_an_unknown_scenario_is_a_404(api):
    client, _ = api

    response = client.post("/api/scenario/load", json={"scenario": "nope"})

    assert response.status_code == 404
    assert "nope" in response.get_json()["error"]


def test_a_scenario_name_that_climbs_out_of_the_library_is_a_404(api):
    client, _ = api

    response = client.post("/api/scenario/load", json={"scenario": "../plants/olefins_lite"})

    assert response.status_code == 404


def test_a_scenario_naming_a_tag_the_plant_lacks_is_a_400(api):
    client, runner = api

    response = client.post("/api/scenario/load", json={"scenario": "bad-tag"})

    assert response.status_code == 400
    assert "X-999" in response.get_json()["error"]
    assert runner.phase is Phase.IDLE


@pytest.mark.parametrize("body", [None, [], {}, {"scenario": 3}])
def test_load_needs_a_string_scenario(api, body):
    client, _ = api

    assert client.post("/api/scenario/load", json=body).status_code == 400


def test_a_call_the_phase_does_not_allow_is_a_409(api):
    client, _ = api

    assert client.post("/api/scenario/start").status_code == 409
    assert client.post("/api/scenario/abort").status_code == 409
    assert client.get("/api/scenario/result").status_code == 409

    client.post("/api/scenario/load", json={"scenario": "pump-trip"})
    client.post("/api/scenario/start")

    assert client.post("/api/scenario/start").status_code == 409
    assert client.post("/api/scenario/load", json={"scenario": "pump-trip"}).status_code == 409


def test_an_action_with_nothing_loaded_is_a_400_not_a_500(api):
    client, _ = api

    response = client.post("/api/action", json={"target": "P-101", "action": "start"})

    assert response.status_code == 400
    assert "no scenario" in response.get_json()["error"]
