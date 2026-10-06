import json
import threading
import time

import pytest

import app.main as main_module
from app.api.health import HealthMonitor, create_health_blueprint
from app.engine.scheduler import Scheduler
from app.engine.sessions import SessionRegistry
from app.engine.snapshot import build_snapshot, solver_status
from flask import Flask


class Solve:
    def __init__(self, converged=True, iterations=3, residual=1e-9):
        self.converged = converged
        self.iterations = iterations
        self.residual = residual


class FakeEngine:
    """Advances sim time by dt per step; can fail, hang or fail to converge."""

    def __init__(self):
        self.sim_time = 0.0
        self.converged = True
        self.fail = False
        self.gate = None
        self.speed = 1.0
        self.paused = False
        self.no_solve = False

    def _build(self):
        return build_snapshot(
            sim_time=self.sim_time,
            speed=self.speed,
            running=not self.paused,
            equipment={},
            solver={} if self.no_solve else solver_status(Solve(converged=self.converged)),
        )

    def step(self, dt):
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.fail:
            raise RuntimeError("solver blew up")
        self.sim_time += dt

        return self._build()

    def snapshot(self):
        return self._build()


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make(engine=None, **kw):
    engine = engine or FakeEngine()
    scheduler = Scheduler(engine, step_seconds=1.0)
    clock = Clock()
    monitor = HealthMonitor(monotonic=clock, **kw)

    return engine, scheduler, clock, monitor


def test_manual_steps_report_stopped_with_full_convergence():
    engine, scheduler, clock, monitor = make()
    scheduler.step_once()
    monitor.observe(scheduler)
    clock.now += 1
    scheduler.step_once()

    row = monitor.observe(scheduler)

    assert row["status"] == "stopped"  # manual stepping, no worker running
    assert row["sim_time"] == 2.0
    assert row["solver"]["convergence_rate"] == 1.0
    assert row["solver"]["samples"] == 2


def _running(scheduler, engine, until):
    scheduler.start()
    deadline = time.monotonic() + 5
    while engine.sim_time < until and time.monotonic() < deadline:
        time.sleep(0.005)


def test_health_responds_while_the_engine_runs():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.01)
    app = Flask(__name__)
    app.register_blueprint(create_health_blueprint(lambda: {"e": scheduler}, lambda: 1))

    try:
        _running(scheduler, engine, 0.05)
        response = app.test_client().get("/health/engine")
    finally:
        scheduler.close()

    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["engines"]["e"]["status"] == "ok"
    assert body["engines"]["e"]["running"] is True
    assert body["engines"]["e"]["sim_time"] > 0


def test_an_engine_that_hangs_shows_as_stalled():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.01)
    clock = Clock()
    monitor = HealthMonitor(monotonic=clock, stall_steps=5)
    app = Flask(__name__)
    app.register_blueprint(
        create_health_blueprint(lambda: {"e": scheduler}, lambda: 1, monitor)
    )
    client = app.test_client()

    try:
        _running(scheduler, engine, 0.03)
        engine.gate = threading.Event()  # the next step hangs
        time.sleep(0.1)
        assert client.get("/health/engine").status_code == 200  # first sight
        clock.now += 1.0
        response = client.get("/health/engine")
    finally:
        engine.gate.set()
        scheduler.close()

    body = response.get_json()
    assert response.status_code == 503
    assert body["status"] == "unhealthy"
    assert body["engines"]["e"]["status"] == "stalled"


def test_a_failed_worker_is_reported_with_its_error():
    engine = FakeEngine()
    engine.fail = True
    scheduler = Scheduler(engine, step_seconds=0.01)
    app = Flask(__name__)
    app.register_blueprint(create_health_blueprint(lambda: {"e": scheduler}, lambda: 1))

    scheduler.start()
    deadline = time.monotonic() + 5
    while scheduler.error is None and time.monotonic() < deadline:
        time.sleep(0.005)
    scheduler.close()
    response = app.test_client().get("/health/engine")

    row = response.get_json()["engines"]["e"]
    assert response.status_code == 503
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError: solver blew up"


def test_a_paused_clock_is_not_a_stall():
    engine, scheduler, clock, monitor = make()
    engine.paused = True
    scheduler.start()
    try:
        monitor.observe(scheduler)
        clock.now += 1000
        row = monitor.observe(scheduler)
    finally:
        scheduler.close()

    assert row["status"] == "paused"


def test_unconverged_solves_lower_the_rate_and_mark_degraded():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=1.0)
    clock = Clock()
    monitor = HealthMonitor(monotonic=clock)
    scheduler.start = lambda: None  # keep the worker out of it

    results = []
    for converged in (True, True, False, False):
        engine.converged = converged
        scheduler.step_once()
        clock.now += 1
        results.append(monitor.observe(scheduler))

    assert results[-1]["solver"]["convergence_rate"] == 0.5
    assert results[-1]["solver"]["converged"] is False


def test_the_same_snapshot_is_not_counted_twice():
    engine, scheduler, clock, monitor = make()
    scheduler.step_once()

    for _ in range(5):
        row = monitor.observe(scheduler)

    assert row["solver"]["samples"] == 1


def test_stall_steps_must_be_positive():
    with pytest.raises(ValueError):
        HealthMonitor(stall_steps=0)


@pytest.fixture
def client():
    main_module.app.config["TESTING"] = True

    return main_module.app.test_client()


def test_live_probe_does_not_create_a_session(client):
    before = len(main_module.sessions)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert len(main_module.sessions) == before


def test_engine_probe_without_a_cookie_creates_no_session_and_reports_none(client):
    before = len(main_module.sessions)

    response = client.get("/health/engine")

    assert response.status_code == 200
    assert response.get_json()["engines"] == {}
    assert len(main_module.sessions) == before
    assert "Set-Cookie" not in response.headers


def test_engine_probe_with_a_session_reports_the_training_scheduler(client):
    client.get("/api/snapshot")
    session_id = client.get_cookie(main_module.SESSION_COOKIE).value
    main_module.sessions.get(session_id).training_scheduler.step_once()
    body = client.get("/health/engine").get_json()

    assert set(body["engines"]) == {"training"}
    assert body["engines"]["training"]["sim_time"] > 0


def test_health_probes_are_not_rate_limited(client):
    limiter = main_module.validate.RateLimiter(rate=0.001, burst=1)
    original = main_module.rate_limiter
    main_module.rate_limiter = limiter
    try:
        statuses = {client.get("/health/live").status_code for _ in range(10)}
    finally:
        main_module.rate_limiter = original

    assert statuses == {200}


def test_request_log_sim_time_follows_the_callers_plant(client):
    import io

    from app import logging as plant_logging

    request_out = io.StringIO()
    plant_logging.configure(
        sim_time=main_module._request_sim_time, request_stream=request_out
    )
    try:
        client.get("/api/snapshot")
        session_id = client.get_cookie(main_module.SESSION_COOKIE).value
        scheduler = main_module.sessions.get(session_id).training_scheduler
        scheduler.step_once()
        expected = scheduler.snapshot().sim_time
        client.get("/api/snapshot")
        client.get("/health/live")
    finally:
        plant_logging.configure(sim_time=main_module._request_sim_time)

    rows = [json.loads(line) for line in request_out.getvalue().splitlines()]
    snapshots = [row for row in rows if row["path"] == "/api/snapshot"]

    assert snapshots[-1]["sim_time"] == expected
    assert snapshots[0]["sim_time"] != expected
    assert [row for row in rows if row["path"] == "/health/live"][0]["sim_time"] is None


def test_a_snapshot_with_no_solve_degrades_gracefully():
    engine, scheduler, clock, monitor = make()
    engine.no_solve = True
    scheduler.step_once()

    row = monitor.observe(scheduler)

    assert row["status"] == "stopped"
    assert row["solver"]["converged"] is None
    assert row["solver"]["convergence_rate"] is None
    assert row["solver"]["samples"] == 0


def test_a_cookie_carrying_probe_neither_touches_its_session_nor_sweeps_others(client, monkeypatch):
    now = [1000.0]
    registry = SessionRegistry(
        factory=main_module.TrainingSession, monotonic=lambda: now[0], idle_seconds=60.0
    )
    monkeypatch.setattr(main_module, "sessions", registry)
    registry.create("probed")
    stale = registry.create("stale")
    client.set_cookie(main_module.SESSION_COOKIE, "probed")
    now[0] += 61.0

    response = client.get("/health/engine")

    assert set(response.get_json()["engines"]) == {"training"}
    assert len(registry) == 2
    assert not stale.training_scheduler.closed
    assert registry.reclaim_idle() == 2
