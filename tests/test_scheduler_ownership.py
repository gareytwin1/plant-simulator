"""
Whole-stack coverage for T2-6: a rendered page starts its own background
scheduler, a browser that never comes back does not stop it, and nothing
short of Session.end() (direct, registry.end(), or LRU eviction) does.
See docs/T2-6_SCHEDULER_OWNERSHIP.md for the design these tests hold the
application to.
"""

import threading
import time

from app import main
from app.engine.sessions import SessionRegistry


WAIT = 5.0


def live_scheduler_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


def poll_until(predicate, wait=WAIT, interval=0.02):
    deadline = time.monotonic() + wait

    while True:
        if predicate():
            return True

        if time.monotonic() >= deadline:
            return predicate()

        time.sleep(interval)


def session_for(client):
    session_id = client.get_cookie(main.SESSION_COOKIE).value
    return main.sessions.get(session_id)


def test_rendering_compressor_page_starts_only_its_own_scheduler():
    client = main.app.test_client()
    client.get("/compressor")
    session = session_for(client)

    try:
        assert session.compressor_scheduler.running is True
        assert session.pump_scheduler.running is False
    finally:
        session.end()


def test_rendering_pump_page_starts_only_its_own_scheduler():
    client = main.app.test_client()
    client.get("/pump")
    session = session_for(client)

    try:
        assert session.pump_scheduler.running is True
        assert session.compressor_scheduler.running is False
    finally:
        session.end()


def test_browser_independence_state_advances_with_no_further_requests():
    # A rendered page's plant keeps running on the server's own clock. No
    # /api/step request is made anywhere in this test, and sim_time still
    # advances twice in a row -- proof the worker, not a client, is the one
    # moving time. Deterministic via poll-until-changed, not a fixed sleep.
    client = main.app.test_client()
    client.get("/compressor")
    session = session_for(client)

    try:
        assert poll_until(lambda: session.compressor_engine.snapshot().sim_time > 0.0)
        first = session.compressor_engine.snapshot().sim_time

        # Client-gone: no further request of any kind for this session, yet
        # a second advance still happens on its own.
        assert poll_until(lambda: session.compressor_engine.snapshot().sim_time > first)
    finally:
        session.end()


def test_manual_step_is_refused_with_409_while_the_scheduler_runs():
    client = main.app.test_client()
    client.get("/compressor")
    session = session_for(client)

    try:
        response = client.post("/api/step")

        assert response.status_code == 409
        assert "error" in response.get_json()
    finally:
        session.end()


def test_manual_pump_step_is_refused_with_409_while_the_scheduler_runs():
    client = main.app.test_client()
    client.get("/pump")
    session = session_for(client)

    try:
        response = client.post("/api/pump/step")

        assert response.status_code == 409
        assert "error" in response.get_json()
    finally:
        session.end()


def test_full_api_sequence_never_starts_a_scheduler():
    # Guard against a later task quietly reintroducing autostart and making
    # the suite flaky: a session driven purely through /api/* (no page ever
    # rendered) must never start a background worker.
    client = main.app.test_client()

    client.get("/api/state")
    client.post("/api/start")
    client.post("/api/step")

    session = session_for(client)

    assert session.compressor_scheduler.running is False


def test_session_end_after_page_render_leaves_no_worker_threads():
    client = main.app.test_client()
    client.get("/compressor")
    session = session_for(client)

    assert session.compressor_scheduler.running is True

    session.end()

    assert session.compressor_scheduler.running is False
    assert session.pump_scheduler.running is False
    assert not live_scheduler_workers()


def test_registry_lru_eviction_stops_a_page_started_worker():
    # End to end: rendering a page starts a real scheduler; eviction from
    # the registry must stop that worker, not just drop the Session object.
    registry = SessionRegistry(max_sessions=1)
    session_a = registry.create("a")
    session_a.compressor_scheduler.start()

    assert poll_until(lambda: session_a.compressor_scheduler.running)

    registry.create("b")  # over capacity, evicts "a"

    assert registry.get("a") is None
    assert session_a.compressor_scheduler.running is False
    assert not live_scheduler_workers()

    registry.get("b").end()
