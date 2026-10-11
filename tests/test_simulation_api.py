"""Simulation API - T20-14: pause and resume over HTTP."""

import pytest
from flask import Flask

from app.api.action import create_action_blueprint
from app.api.scenario import create_scenario_blueprint
from app.api.simulation import create_simulation_blueprint
from app.scenarios.runner import ScenarioLibrary, scenario_key
from app.training.session import TrainingSession


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

KEY = scenario_key("pump-trip")


@pytest.fixture
def api(tmp_path):
    (tmp_path / "pump-trip.yaml").write_text(SCENARIO)
    library = ScenarioLibrary(scenarios=tmp_path)
    session = TrainingSession(library)

    app = Flask(__name__)
    app.register_blueprint(create_scenario_blueprint(lambda: session, library))
    app.register_blueprint(create_simulation_blueprint(lambda: session))
    app.register_blueprint(create_action_blueprint(apply=session.act))

    yield app.test_client(), session

    session.end()


def sim_time(session):
    return session.training_scheduler.snapshot().sim_time


def advance(session, count=5):
    for _ in range(count):
        session.training_scheduler.step_once()


def test_pausing_free_play_stops_sim_time_and_resuming_restarts_it(api):
    client, session = api
    advance(session)
    moving = sim_time(session)
    assert moving > 0

    paused = client.post("/api/simulation/pause")
    advance(session)

    assert paused.status_code == 200
    assert paused.get_json() == {"running": False}
    assert sim_time(session) == moving
    assert session.training_scheduler.snapshot().running is False

    resumed = client.post("/api/simulation/resume")
    advance(session)

    assert resumed.get_json() == {"running": True}
    assert sim_time(session) > moving
    assert session.training_scheduler.snapshot().running is True


def test_pause_and_resume_are_idempotent(api):
    client, _ = api

    assert [client.post("/api/simulation/pause").get_json() for _ in range(2)] == [{"running": False}] * 2
    assert [client.post("/api/simulation/resume").get_json() for _ in range(2)] == [{"running": True}] * 2


def test_a_running_scenario_pauses_and_resumes_without_moving_its_clock(api):
    client, session = api
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    advance(session)
    elapsed = client.get("/api/scenario/result").get_json()["elapsed_s"]

    assert client.post("/api/simulation/pause").get_json() == {"running": False}
    advance(session, 20)
    assert client.get("/api/scenario/result").get_json()["elapsed_s"] == elapsed

    assert client.post("/api/simulation/resume").get_json() == {"running": True}
    advance(session)
    assert client.get("/api/scenario/result").get_json()["elapsed_s"] > elapsed


@pytest.mark.parametrize("route", ["pause", "resume"])
def test_a_loaded_scenario_refuses_with_a_409(api, route):
    client, _ = api
    client.post("/api/scenario/load", json={"scenario": KEY})

    response = client.post(f"/api/simulation/{route}")

    assert response.status_code == 409
    assert "error" in response.get_json()


@pytest.mark.parametrize("route", ["pause", "resume"])
def test_an_aborted_scenario_refuses_with_a_409(api, route):
    client, _ = api
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    client.post("/api/scenario/abort")

    assert client.post(f"/api/simulation/{route}").status_code == 409


def test_unloading_hands_free_play_back_in_the_pause_state_it_was_left_in(api):
    client, session = api
    client.post("/api/simulation/pause")
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    assert session.training_scheduler.snapshot().running is True
    client.post("/api/scenario/abort")
    client.post("/api/scenario/unload")

    assert session.training_scheduler.snapshot().running is False

    client.post("/api/simulation/resume")
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/unload")

    assert session.training_scheduler.snapshot().running is True


def test_neither_route_is_an_operator_action(api):
    client, session = api
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    client.post("/api/simulation/pause")
    client.post("/api/simulation/resume")

    assert session.runner.actions.events == ()
    assert client.get("/api/scenario/result").get_json()["actions"] == []
