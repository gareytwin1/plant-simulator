import json
import threading
import time

import pytest

import app.main as main_module
from app.api.health import HealthMonitor, create_health_blueprint
from app.engine.scheduler import Scheduler
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

    def _build(self):
        return build_snapshot(
            sim_time=self.sim_time,
            speed=self.speed,
            running=not self.paused,
            equipment={},
            solver=solver_status(Solve(converged=self.converged)),
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


def test_engine_probe_with_a_session_reports_both_engines(client):
    client.get("/api/state")
    client.post("/api/step")
    body = client.get("/health/engine").get_json()

    assert set(body["engines"]) == {"compressor", "pump"}
    assert body["engines"]["compressor"]["sim_time"] > 0
