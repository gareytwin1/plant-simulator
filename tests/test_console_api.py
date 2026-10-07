"""Console wiring - T16-9: the stream, action, alarm and scenario routes serve
the caller's training session, and /console is the one page that starts it."""

import itertools
import json
import pathlib
import shutil
import subprocess
import threading
import time

import pytest

from app import config, main
from app.engine.sessions import SessionRegistry
from app.scenarios.runner import scenario_key
from app.training.session import TrainingSession


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit", "ignore:interlock")

WAIT = 5.0


@pytest.fixture
def client():
    client = main.app.test_client()

    yield client

    cookie = client.get_cookie(main.SESSION_COOKIE)
    if cookie is not None:
        main.sessions.end(cookie.value)


def session_of(client):
    return main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)


def settle(session):
    """Stop the worker so the test steps the plant itself."""
    session.training_scheduler.stop()

    return session.training_scheduler


def poll_until(predicate, wait=WAIT, interval=0.02):
    deadline = time.monotonic() + wait

    while not predicate():
        if time.monotonic() >= deadline:
            return predicate()
        time.sleep(interval)

    return True


def events(response, count):
    chunks = itertools.islice(iter(response.response), count)
    payloads = []

    for chunk in chunks:
        (line,) = [part for part in chunk.decode().splitlines() if part.startswith("data:")]
        payloads.append(json.loads(line[len("data:") :]))

    return payloads


def live_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


def test_the_console_renders_and_starts_the_sessions_scheduler(client):
    response = client.get("/console")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert session_of(client).training_scheduler.running
    assert "alarms.js" in body and "alarms.css" in body and "tokens.css" in body


def test_the_console_mounts_the_graphic_on_the_connection_stream(client):
    body = client.get("/console").get_data(as_text=True)

    assert "graphic.js" in body
    assert '<div id="process-graphic" data-live-value>' in body
    assert "/static/graphics/plant.svg" in body
    assert "graphic.update(snapshot)" in body
    assert body.count("new EventSource") == 0
    assert client.get("/static/graphics/plant.svg").status_code == 200
    assert client.get("/static/js/graphic.js").status_code == 200


def test_the_console_mounts_the_faceplates_on_the_same_stream(client):
    body = client.get("/console").get_data(as_text=True)

    assert "faceplate.js" in body and "faceplate.css" in body
    assert 'id="faceplates"' in body
    assert "faceplates.update(snapshot)" in body
    assert body.count("new EventSource") == 0
    assert client.get("/static/js/faceplate.js").status_code == 200
    assert client.get("/static/css/faceplate.css").status_code == 200


def test_the_console_and_a_repeat_visit_share_one_worker(client):
    before = len(live_workers())

    client.get("/console")
    client.get("/console")

    assert len(live_workers()) == before + 1


def test_the_landing_page_and_snapshot_still_start_nothing(client):
    client.get("/")
    client.get("/api/snapshot")

    assert not session_of(client).training_scheduler.running


def test_the_header_links_the_console_from_every_page(client):
    assert 'href="/console"' in client.get("/").get_data(as_text=True)
    assert 'href="/console"' in client.get("/console").get_data(as_text=True)


def test_a_request_with_no_cookie_still_gets_a_new_session(client):
    first = main.app.test_client()
    second = main.app.test_client()

    try:
        first.get("/console")
        second.get("/console")

        assert first.get_cookie(main.SESSION_COOKIE).value != second.get_cookie(main.SESSION_COOKIE).value
    finally:
        for other in (first, second):
            main.sessions.end(other.get_cookie(main.SESSION_COOKIE).value)


def test_a_stream_event_is_the_operator_view_not_the_full_snapshot(client):
    client.get("/console")

    response = client.get("/api/stream")
    try:
        (event,) = events(response, 1)
    finally:
        response.close()

    assert set(event["equipment"]["LV-101"]) == {"position", "position_target"}
    assert "stuck" not in json.dumps(event["equipment"])


def test_an_action_shows_in_a_later_stream_event(client):
    client.get("/console")
    response = client.get("/api/stream")
    try:
        (before,) = events(response, 1)
        assert before["equipment"]["P-101"]["running"] is True

        taken = client.post("/api/action", json={"target": "P-101", "action": "stop", "value": None})
        assert taken.status_code == 200

        stopped = poll_until(lambda: events(response, 1)[0]["equipment"]["P-101"]["running"] is False)
    finally:
        response.close()

    assert stopped


def test_an_action_blueprint_needs_apply_or_both_getters():
    from app.api.action import create_action_blueprint

    for kwargs in ({}, {"get_engine": lambda: None}, {"get_log": lambda: None}):
        with pytest.raises(ValueError):
            create_action_blueprint(**kwargs)


def test_the_console_carries_the_stream_interval_for_its_scripts(client):
    body = client.get("/console").get_data(as_text=True)

    assert f'data-interval-seconds="{config.STREAM_INTERVAL_SECONDS}"' in body


def test_a_refused_action_answers_400(client):
    client.get("/console")

    refused = client.post("/api/action", json={"target": "X-999", "action": "stop", "value": None})

    assert refused.status_code == 400


def test_an_alarm_raised_in_free_play_reaches_history_and_acknowledges(client):
    client.get("/console")
    scheduler = settle(session_of(client))

    assert client.post("/api/action", json={"target": "P-101", "action": "stop", "value": None}).status_code == 200
    for _ in range(600):
        scheduler.step_once()

    entries = client.get("/api/alarms/history").get_json()
    alarm = next(entry for entry in entries if entry["type"] == "alarm")

    acknowledged = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm["id"]})
    after = client.get("/api/alarms/history").get_json()

    assert acknowledged.status_code == 200
    assert len(after) > len(entries)
    assert after[-1]["type"] == "acknowledge"


def test_scenario_load_start_abort_and_unload_change_what_the_stream_shows(client):
    client.get("/console")
    scheduler = settle(session_of(client))
    free_time = scheduler.snapshot().sim_time

    def streamed():
        response = client.get("/api/stream")
        try:
            return events(response, 1)[0]
        finally:
            response.close()

    loaded = client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})
    in_scenario = streamed()

    assert loaded.status_code == 200
    assert in_scenario["sim_time"] != pytest.approx(free_time)
    assert in_scenario["equipment"]["P-101"]["running"] is False

    assert client.post("/api/scenario/start").status_code == 200
    assert client.post("/api/scenario/abort").status_code == 200
    assert client.post("/api/scenario/unload").status_code == 200

    assert streamed()["sim_time"] == pytest.approx(free_time)
    assert streamed()["equipment"]["P-101"]["running"] is True


def test_a_session_with_an_open_stream_is_not_reclaimed_while_idle(client, monkeypatch):
    now = [0.0]
    registry = SessionRegistry(
        factory=lambda: TrainingSession(main.library),
        monotonic=lambda: now[0],
        idle_seconds=60.0,
    )
    monkeypatch.setattr(main, "sessions", registry)

    other = main.app.test_client()
    other.get("/console")
    session = registry.get(other.get_cookie(main.SESSION_COOKIE).value)
    response = other.get("/api/stream")

    try:
        events(response, 1)
        now[0] = 600.0

        assert registry.reclaim_idle() == 0
        assert session.training_scheduler.closed is False
    finally:
        response.close()

    now[0] = 1200.0

    assert registry.reclaim_idle() == 1
    assert session.training_scheduler.closed is True


# ---- landing.js leaves for the console once its call succeeds ----

LANDING_JS = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "landing.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_landing(body):
    script = (
        f"const L = require({json.dumps(str(LANDING_JS))});"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )

    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


PAGE_STUB = """
const mk = () => ({ disabled: false, listeners: {}, textContent: "", hidden: true,
  setAttribute() {}, getAttribute: () => '{"idle": ""}',
  addEventListener(t, f) { this.listeners[t] = f; } });
const free = mk(), el = mk(), card = mk(), load = mk(), went = [];
card.getAttribute = k => (k === "data-title" ? "Pump trip" : "key");
card.querySelector = () => load;
const doc = {
  getElementById: id => (id === "free-play" ? free : el),
  querySelectorAll: sel => (sel === "main button" ? [free] : sel === ".scenario" ? [card] : []),
};
const ok = body => ({ ok: true, status: 200, json: async () => body });
"""


@needs_node
def test_free_play_goes_to_the_console_once_it_succeeds():
    went = run_landing(
        PAGE_STUB
        + """
        L.mount(doc, async url => ok({ sim_time: 1 }), url => went.push(url));
        free.listeners.click();
        await new Promise(r => setTimeout(r, 20));
        return went;
        """,
    )

    assert went == ["/console"]


@needs_node
def test_choosing_a_scenario_goes_to_the_console_once_it_loads():
    went = run_landing(
        PAGE_STUB
        + """
        L.mount(doc, async url => ok({ phase: "loaded", sim_time: 1 }), url => went.push(url));
        load.listeners.click();
        await new Promise(r => setTimeout(r, 20));
        return went;
        """,
    )

    assert went == ["/console"]


@needs_node
def test_a_refusal_keeps_the_landing_page():
    result = run_landing(
        PAGE_STUB
        + """
        const refuse = async () => ({ ok: false, status: 409, json: async () => ({}) });
        L.mount(doc, refuse, url => went.push(url));
        load.listeners.click();
        await new Promise(r => setTimeout(r, 20));
        return { went, shown: el.textContent };
        """,
    )

    assert result["went"] == []
    assert "running" in result["shown"]
