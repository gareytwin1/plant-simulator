"""Run history per browser - T20-16: the operator cookie, recording a completed
run, GET /api/history, and a database that outlives the process."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import MappingProxyType

import pytest
from app import config, main
from app.api import history
from app.configversion import ConfigVersion, read_config_version
from app.scenarios.replay import Recording, replay
from app.scenarios.runner import Phase, scenario_key
from app.scoring.metrics import RunMetrics
from app.scoring.score import Score
from app.scoring.store import ScoreStore

ROOT = Path(__file__).resolve().parent.parent

# The catalogue scenario that fails fastest when left alone (90 steps).
QUICK = "stuck_drain_valve"


def metrics(outcome="succeeded"):
    return RunMetrics(
        alarm_count=0,
        trip_count=0,
        time_to_recognise_s=None,
        time_to_stabilise_s=None,
        peak_excursions=MappingProxyType({}),
        time_outside_envelope_s=0.0,
        production_lost=MappingProxyType({}),
        unnecessary_actions=0,
        outcome=outcome,
    )


def score(total):
    return Score(total=total, penalties=MappingProxyType({}))


def operator_of(client):
    return client.get_cookie(history.OPERATOR_COOKIE).value


def session_of(client):
    return main.sessions.peek(client.get_cookie(main.SESSION_COOKIE).value)


def entry(scenario_id):
    return next(e for e in main.CATALOGUE if e.id == scenario_id)


def finish(client, scenario_id=QUICK):
    """Load and start `scenario_id` through the API, then step it to the end
    with nobody acting, the way the scheduler would."""
    assert client.post("/api/scenario/load", json={"scenario": scenario_key(scenario_id)}).status_code == 200
    assert client.post("/api/scenario/start").status_code == 200
    session = session_of(client)

    for _ in range(2000):
        if session.runner.phase is Phase.COMPLETE:
            return session
        session.training_scheduler.step_once()

    raise AssertionError(f"{scenario_id} did not complete")


@pytest.fixture
def client():
    made = main.app.test_client()
    made.get("/")

    yield made

    cookie = made.get_cookie(main.SESSION_COOKIE)
    if cookie is not None:
        main.sessions.end(cookie.value)


def test_completing_a_run_stores_one_result_and_reading_or_replaying_it_stores_none(client, empty_score_store):
    session = finish(client)

    (stored,) = empty_score_store.recent(operator_of(client), 100)
    assert stored.scenario == QUICK
    assert stored.config_version == read_config_version()
    assert stored.metrics.outcome == "failed"
    assert stored.score == session.result().score

    for _ in range(5):
        session.training_scheduler.step_once()
    client.get("/api/scenario/result")
    client.get("/api/history")
    client.get("/")
    replay(Recording.of(session.runner), main.library)

    assert len(empty_score_store.recent(operator_of(client), 100)) == 1


def test_an_aborted_run_is_not_stored(client, empty_score_store):
    client.post("/api/scenario/load", json={"scenario": scenario_key(QUICK)})
    client.post("/api/scenario/start")
    session_of(client).training_scheduler.step_once()
    assert client.post("/api/scenario/abort").status_code == 200

    assert empty_score_store.recent(operator_of(client), 100) == []
    assert client.get("/api/history").get_json() == {"recent": [], "best": {}}


def test_history_lists_a_completed_run_by_title_and_its_best_by_key(client):
    finish(client)

    body = client.get("/api/history").get_json()

    quick = entry(QUICK)
    (recent,) = body["recent"]
    assert set(recent) == {"title", "difficulty", "total", "outcome", "recorded_at"}
    assert recent["title"] == quick.title
    assert recent["difficulty"] == quick.difficulty
    assert recent["outcome"] == "failed"
    assert body["best"] == {quick.key: {"title": quick.title, "total": pytest.approx(recent["total"])}}


def test_history_is_only_the_callers_newest_first_and_capped(client, empty_score_store):
    other = main.app.test_client()
    other.get("/")
    mine, theirs = operator_of(client), operator_of(other)
    assert mine != theirs

    for n in range(config.HISTORY_RECENT_LIMIT + 2):
        empty_score_store.record(mine, QUICK, float(n), score(float(n)), metrics())
    empty_score_store.record(theirs, "pump_trip", 99.0, score(100.0), metrics())

    body = client.get("/api/history").get_json()

    assert [row["recorded_at"] for row in body["recent"]] == [
        pytest.approx(float(n)) for n in range(config.HISTORY_RECENT_LIMIT + 1, 1, -1)
    ]
    assert list(body["best"]) == [entry(QUICK).key]
    assert body["best"][entry(QUICK).key]["total"] == pytest.approx(config.HISTORY_RECENT_LIMIT + 1)

    main.sessions.end(other.get_cookie(main.SESSION_COOKIE).value)


def test_a_result_from_another_config_major_is_listed_but_is_never_a_best(client, tmp_path, monkeypatch):
    path = tmp_path / "scores.db"
    current = read_config_version()
    older = ConfigVersion(current.major - 1, 0, 0)

    with ScoreStore(path, config_version=older) as old:
        old.record(operator_of(client), QUICK, 1.0, score(99.0), metrics())

    store = ScoreStore(path)
    monkeypatch.setattr(main, "store", store)

    body = client.get("/api/history").get_json()

    assert [row["total"] for row in body["recent"]] == [pytest.approx(99.0)]
    assert body["best"] == {}

    store.record(operator_of(client), QUICK, 2.0, score(10.0), metrics())
    best = client.get("/api/history").get_json()["best"]
    assert best == {entry(QUICK).key: {"title": entry(QUICK).title, "total": pytest.approx(10.0)}}
    store.close()


def test_a_run_whose_scenario_file_is_gone_is_left_out(client, empty_score_store):
    empty_score_store.record(operator_of(client), "retired_scenario", 1.0, score(50.0), metrics())

    assert client.get("/api/history").get_json() == {"recent": [], "best": {}}


@pytest.mark.parametrize("cookie", [None, "", "not-an-id", "A" * 32, "0" * 31, "0" * 33, "g" * 32])
def test_a_missing_or_malformed_operator_cookie_gets_a_fresh_id_and_an_empty_history(cookie, empty_score_store):
    browser = main.app.test_client()
    if cookie is not None:
        browser.set_cookie(history.OPERATOR_COOKIE, cookie)
        # Whatever a malformed id would have named, nothing is filed under it.
        empty_score_store.record(cookie, QUICK, 1.0, score(50.0), metrics())

    response = browser.get("/api/history")

    assert response.get_json() == {"recent": [], "best": {}}
    issued = operator_of(browser)
    assert history.valid_operator_id(issued)
    assert issued != cookie

    (header,) = [h for h in response.headers.getlist("Set-Cookie") if h.startswith(history.OPERATOR_COOKIE + "=")]
    attributes = {part.strip().split("=")[0].lower(): part for part in header.split(";")}
    assert "httponly" in attributes
    assert attributes["samesite"].strip() == "SameSite=Lax"
    assert attributes["max-age"].strip() == f"Max-Age={config.OPERATOR_COOKIE_SECONDS}"

    main.sessions.end(browser.get_cookie(main.SESSION_COOKIE).value)


def test_a_valid_operator_cookie_is_kept_across_sessions(empty_score_store):
    operator = "0123456789abcdef0123456789abcdef"
    first = main.app.test_client()
    first.set_cookie(history.OPERATOR_COOKIE, operator)

    response = first.get("/")

    assert not [h for h in response.headers.getlist("Set-Cookie") if h.startswith(history.OPERATOR_COOKIE + "=")]
    assert session_of(first).operator_id == operator
    finish(first)
    main.sessions.end(first.get_cookie(main.SESSION_COOKIE).value)

    # A new session - the old one reclaimed, the browser restarted - keeps the id.
    later = main.app.test_client()
    later.set_cookie(history.OPERATOR_COOKIE, operator)

    assert [row["title"] for row in later.get("/api/history").get_json()["recent"]] == [entry(QUICK).title]
    main.sessions.end(later.get_cookie(main.SESSION_COOKIE).value)


def test_a_live_sessions_operator_wins_over_a_changed_cookie(client, empty_score_store):
    operator = operator_of(client)
    client.set_cookie(history.OPERATOR_COOKIE, "f" * 32)

    client.get("/")

    assert operator_of(client) == operator
    finish(client)
    assert len(client.get("/api/history").get_json()["recent"]) == 1


def test_no_history_response_names_a_scenario_id_while_it_is_loaded_or_running(client):
    finish(client, "pump_trip")
    ids = [e.id for e in main.CATALOGUE]

    client.post("/api/scenario/load", json={"scenario": scenario_key(QUICK)})
    loaded = client.get("/api/history").get_data(as_text=True)
    client.post("/api/scenario/start")
    running = client.get("/api/history").get_data(as_text=True)

    assert json.loads(running)["recent"]
    for body in (loaded, running):
        for scenario_id in ids:
            assert scenario_id not in body


def test_the_score_database_path_comes_from_the_environment():
    def resolved(env):
        command = [sys.executable, "-c", "from app import config; print(config.SCORE_DB_PATH)"]
        environment = {k: v for k, v in os.environ.items() if k != "PLANT_SCORE_DB"} | env
        return subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=True).stdout

    assert resolved({"PLANT_SCORE_DB": "/somewhere/else.db"}).strip() == "/somewhere/else.db"
    assert resolved({}).strip() == str(ROOT / "data" / "scores.sqlite3")


RESTART = textwrap.dedent(
    """
    import json, sys
    from app import main
    from app.api import history
    from app.scenarios.runner import Phase, scenario_key

    client = main.app.test_client()
    client.set_cookie(history.OPERATOR_COOKIE, sys.argv[1])
    client.get("/")
    if sys.argv[2] == "run":
        client.post("/api/scenario/load", json={"scenario": scenario_key("stuck_drain_valve")})
        client.post("/api/scenario/start")
        session = main.sessions.peek(client.get_cookie(main.SESSION_COOKIE).value)
        while session.runner.phase is not Phase.COMPLETE:
            session.training_scheduler.step_once()
    print(json.dumps(client.get("/api/history").get_json()))
    """
)


def test_results_survive_an_app_restart_at_the_configured_path(tmp_path):
    operator = "fedcba9876543210fedcba9876543210"
    environment = os.environ | {"PLANT_SCORE_DB": str(tmp_path / "nested" / "scores.sqlite3")}

    def app_process(mode):
        done = subprocess.run(
            [sys.executable, "-c", RESTART, operator, mode],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(done.stdout.strip().splitlines()[-1])

    first = app_process("run")
    after_restart = app_process("read")

    assert len(first["recent"]) == 1
    assert after_restart == first
    assert (tmp_path / "nested" / "scores.sqlite3").is_file()

