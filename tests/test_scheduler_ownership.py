"""
Whole-stack coverage for T2-6: a rendered page starts its own background
scheduler, a browser that never comes back does not stop it, and nothing
short of the session's end() (direct, registry.end(), or LRU eviction) does.
See docs/T2-6_SCHEDULER_OWNERSHIP.md for the design these tests hold the
application to.
"""

import threading
import time

from app import main
from app.engine.sessions import SessionRegistry
from app.training.session import TrainingSession


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


def test_full_api_sequence_never_starts_a_scheduler():
    # Guard against a later task quietly reintroducing autostart and making
    # the suite flaky: a session driven purely through /api/* (no page ever
    # rendered) must never start a background worker. Only a page render does,
    # and none exists until T16-9's /console.
    client = main.app.test_client()

    client.get("/api/snapshot")
    client.get("/api/snapshot")

    session = session_for(client)

    assert session.training_scheduler.running is False
    assert not live_scheduler_workers()


def test_browser_independence_state_advances_with_no_further_requests():
    # A started plant keeps running on the server's own clock. No manual step
    # is made anywhere in this test, and sim_time still advances twice in a row
    # - proof the worker, not a client, is the one moving time. Deterministic
    # via poll-until-changed, not a fixed sleep. Read through
    # scheduler.snapshot(): reading the Engine directly while the worker steps
    # is the unsynchronized read scheduler.py forbids.
    session = TrainingSession()
    scheduler = session.training_scheduler
    started = scheduler.snapshot().sim_time
    scheduler.start()

    try:
        assert poll_until(lambda: scheduler.snapshot().sim_time > started)
        first = scheduler.snapshot().sim_time

        assert poll_until(lambda: scheduler.snapshot().sim_time > first)
    finally:
        session.end()


def test_session_end_stops_a_started_worker():
    session = TrainingSession()
    session.training_scheduler.start()

    assert poll_until(lambda: session.training_scheduler.running)

    session.end()

    assert session.training_scheduler.running is False
    assert not live_scheduler_workers()


def test_registry_lru_eviction_stops_a_started_worker():
    # End to end: a started scheduler is a real worker thread; eviction from
    # the registry must stop it, not just drop the session object.
    registry = SessionRegistry(max_sessions=1, factory=TrainingSession)
    session_a = registry.create("a")
    session_a.training_scheduler.start()

    assert poll_until(lambda: session_a.training_scheduler.running)

    registry.create("b")  # over capacity, evicts "a"

    assert registry.get("a") is None
    assert session_a.training_scheduler.running is False
    assert not live_scheduler_workers()

    registry.get("b").end()
