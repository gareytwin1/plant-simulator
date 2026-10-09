"""Scenario disclosure - T16-12.

While a run is live the browser must not learn its cause. This walks every file
in config/scenarios, so a new scenario is covered without editing it.
"""

import re
from pathlib import Path

import pytest
import yaml

from app import main
from app.scenarios.runner import ScenarioLibrary, ScenarioResult, scenario_key


SCENARIOS = sorted(ScenarioLibrary().scenarios.glob("*.yaml"))
IDS = [path.stem for path in SCENARIOS]


def secrets(document):
    """Every string a live run must keep out of a body."""
    description = document["description"].strip()
    found = {document["id"], description}
    found.update(line.strip() for line in re.split(r"(?<=\.)\s+|\n", description) if len(line.strip()) > 20)
    found.update(t["id"] for t in document.get("triggers", []))
    found.update(o["id"] for o in document["objectives"])
    found.update(m["parameter"] for m in document.get("malfunctions", []))
    return found


def assert_discloses_nothing(response, document):
    body = response.get_data(as_text=True)

    for secret in secrets(document):
        assert secret not in body, f"{secret!r} reached the browser"


def session_of(client):
    return main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)


def run_to_the_end(session):
    for _ in range(5000):
        if session.result().phase.value != "running":
            return
        session.step(1.0)

    raise AssertionError("the run never ended")


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_a_live_run_names_nothing_of_its_cause(path):
    document = yaml.safe_load(path.read_text())
    client = main.app.test_client()
    key = scenario_key(path.stem)

    assert_discloses_nothing(client.get("/"), document)

    loaded = client.post("/api/scenario/load", json={"scenario": key})
    assert loaded.status_code == 200
    assert loaded.get_json()["key"] == key
    assert_discloses_nothing(loaded, document)
    assert_discloses_nothing(client.get("/"), document)

    started = client.post("/api/scenario/start")
    assert started.status_code == 200
    assert started.get_json()["phase"] == "running"
    assert_discloses_nothing(started, document)

    session = session_of(client)
    for _ in range(40):
        session.step(1.0)

    live = client.get("/api/scenario/result")
    assert live.get_json()["phase"] == "running"
    assert_discloses_nothing(live, document)
    assert_discloses_nothing(client.get("/"), document)

    for refusal in (
        client.post("/api/scenario/load", json={"scenario": key}),
        client.post("/api/scenario/unload"),
        client.post("/api/scenario/load", json={"scenario": path.stem}),
    ):
        assert refusal.status_code in (404, 409)
        assert_discloses_nothing(refusal, document)

    body = live.get_json()
    for hidden in ("scenario_id", "seed", "triggers_fired", "outcome"):
        assert hidden not in body
    assert all(set(objective) == {"status", "ended_at"} for objective in body["objectives"])


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_an_aborted_run_reveals_the_id_and_the_debrief(path):
    document = yaml.safe_load(path.read_text())
    client = main.app.test_client()
    client.post("/api/scenario/load", json={"scenario": scenario_key(path.stem)})
    client.post("/api/scenario/start")

    aborted = client.post("/api/scenario/abort").get_json()
    result = client.get("/api/scenario/result").get_json()

    for body in (aborted, result):
        assert body["scenario_id"] == path.stem
        assert body["outcome"] == "aborted"
        assert body["debrief"] == document["description"].strip()
        assert body["key"] == scenario_key(path.stem)


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_a_completed_run_reveals_the_id_and_the_debrief(path):
    document = yaml.safe_load(path.read_text())
    client = main.app.test_client()
    client.post("/api/scenario/load", json={"scenario": scenario_key(path.stem)})
    client.post("/api/scenario/start")

    run_to_the_end(session_of(client))

    body = client.get("/api/scenario/result").get_json()
    assert body["phase"] == "complete"
    assert body["scenario_id"] == path.stem
    assert body["debrief"] == document["description"].strip()
    assert [o["id"] for o in body["objectives"]] == [o["id"] for o in document["objectives"]]
    assert set(body["triggers_fired"]) <= {t["id"] for t in document.get("triggers", [])}


def test_an_unknown_key_is_a_404_naming_no_scenario():
    client = main.app.test_client()

    response = client.post("/api/scenario/load", json={"scenario": "0" * 12})

    assert response.status_code == 404
    body = response.get_data(as_text=True)
    assert not any(stem in body for stem in IDS)


def test_a_scenario_that_fails_to_set_up_is_a_generic_400_and_the_detail_is_logged(tmp_path, caplog):
    from flask import Flask

    from app.api.scenario import create_scenario_blueprint
    from app.scenarios.runner import ScenarioRunner

    source = Path(ScenarioLibrary().scenarios / "pump_trip.yaml").read_text()
    (tmp_path / "pump_trip.yaml").write_text(source.replace("V-101.level >= 0.3", "X-999.level >= 0.3"))
    library = ScenarioLibrary(scenarios=tmp_path)
    runner = ScenarioRunner(library)
    app = Flask(__name__)
    app.register_blueprint(create_scenario_blueprint(lambda: runner, library))

    with caplog.at_level("WARNING", logger="plant.request"):
        response = app.test_client().post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})

    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "X-999" not in body and "pump_trip" not in body
    assert "X-999" in caplog.text


def test_the_key_for_an_id_is_the_same_across_restarts():
    assert scenario_key("pump_trip") == "29b1dedb16ee"
    assert [e.key for e in ScenarioLibrary().catalogue()] == [e.key for e in ScenarioLibrary().catalogue()]


def test_the_server_side_result_keeps_the_real_ids():
    result = main.sessions.create("disclosure-check").load("pump_trip")

    assert isinstance(result, ScenarioResult)
    assert result.scenario_id == "pump_trip"


@pytest.mark.parametrize("replacement", [None, b"", b"- a\n- list\n", b"\xff\xfe not utf-8"])
def test_a_finished_run_whose_file_changed_still_answers_without_a_debrief(tmp_path, replacement):
    from flask import Flask

    from app.api.scenario import create_scenario_blueprint
    from app.scenarios.runner import ScenarioRunner

    target = tmp_path / "pump_trip.yaml"
    target.write_text((ScenarioLibrary().scenarios / "pump_trip.yaml").read_text())
    library = ScenarioLibrary(scenarios=tmp_path)
    runner = ScenarioRunner(library)
    app = Flask(__name__)
    app.register_blueprint(create_scenario_blueprint(lambda: runner, library))
    client = app.test_client()
    client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})
    client.post("/api/scenario/start")
    if replacement is None:
        target.unlink()
    else:
        target.write_bytes(replacement)

    response = client.post("/api/scenario/abort")

    assert response.status_code == 200
    assert response.get_json()["debrief"] == ""
    assert client.get("/api/scenario/result").status_code == 200


def test_a_finished_run_whose_file_cannot_be_read_still_answers_without_a_debrief(tmp_path, monkeypatch):
    from flask import Flask

    from app.api.scenario import create_scenario_blueprint
    from app.scenarios.runner import ScenarioRunner

    (tmp_path / "pump_trip.yaml").write_text((ScenarioLibrary().scenarios / "pump_trip.yaml").read_text())
    library = ScenarioLibrary(scenarios=tmp_path)
    runner = ScenarioRunner(library)
    app = Flask(__name__)
    app.register_blueprint(create_scenario_blueprint(lambda: runner, library))
    client = app.test_client()
    client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})
    client.post("/api/scenario/start")

    def unreadable(self, name):
        raise OSError("disk gone")

    monkeypatch.setattr(ScenarioLibrary, "scenario", unreadable)

    response = client.post("/api/scenario/abort")

    assert response.status_code == 200
    assert response.get_json()["debrief"] == ""
