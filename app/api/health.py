"""
Health endpoints (T18-4) - whether the engines are alive, and whether their
solves land.

    GET /health/live    the process answers; touches no plant
    GET /health/engine  per-engine liveness and solver convergence

Both are read-only and sit outside the session machinery: a probe must not
create, touch or evict a plant. `/health/engine` reports the schedulers the
caller's `get_schedulers` returns for this request (in `app.main`, the plant
of the session cookie the request carries, none when it carries none). The
process holds one plant per browser session, so "the engine" has no single
meaning here; a probe without a cookie learns the process is up and how many
sessions it holds, not the state of anyone's plant.

Everything is read from published state: `Scheduler.snapshot()`, `running`,
`closed` and `error`. Nothing here steps, locks or reaches into an Engine.
Alarms and scoring do not run against the live plant, so health says nothing
about them.

Per engine, `status` is the first that applies:

    failed    the worker stopped on an exception (`error` carries it)
    closed    the session ended
    stopped   no worker is running (never started, or stopped)
    paused    the clock is paused or its speed is zero
    stalled   the worker runs but sim time has not moved for `stall_after`
              wall seconds, a step that hangs rather than raises
    degraded  the last published solve did not converge
    ok

`failed` and `stalled` turn the response into 503; everything else is 200.
Stall detection compares sim time between observations, so it needs two
requests `stall_after` apart: the first sight of an engine only starts its
clock. `convergence_rate` is the share of distinct published snapshots this
monitor has seen whose solve converged, over a bounded window. It samples at
the rate health is polled, so it is a trend, not an exact count of solves.
"""

from __future__ import annotations

import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app.engine.snapshot import Snapshot
from app.statetypes import JSONValue


STALL_STEPS = 5.0
CONVERGENCE_WINDOW = 200

UNHEALTHY = frozenset({"failed", "stalled"})


class EngineSource(Protocol):
    """What health needs of a scheduler. `Scheduler` satisfies it."""

    step_seconds: float
    error: BaseException | None

    @property
    def running(self) -> bool: ...

    @property
    def closed(self) -> bool: ...

    def snapshot(self) -> Snapshot: ...


@dataclass
class _Watch:
    sim_time: float
    changed_at: float
    converged: deque[bool] = field(default_factory=lambda: deque(maxlen=CONVERGENCE_WINDOW))


class HealthMonitor:
    """Remembers, per scheduler, when sim time last moved and how recent
    solves went. State is held weakly, so it goes with the session."""

    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        stall_steps: float = STALL_STEPS,
    ) -> None:
        if stall_steps <= 0:
            raise ValueError(f"stall_steps must be positive, got {stall_steps}")

        self._monotonic = monotonic
        self._stall_steps = stall_steps
        self._watches: weakref.WeakKeyDictionary[EngineSource, _Watch] = (
            weakref.WeakKeyDictionary()
        )
        self._lock = threading.Lock()

    def observe(self, source: EngineSource) -> dict[str, JSONValue]:
        snapshot = source.snapshot()
        now = self._monotonic()
        converged = snapshot.solver["converged"] is True

        with self._lock:
            watch = self._watches.get(source)

            if watch is None:
                watch = _Watch(snapshot.sim_time, now)
                watch.converged.append(converged)
                self._watches[source] = watch
            elif snapshot.sim_time != watch.sim_time:
                watch.sim_time = snapshot.sim_time
                watch.changed_at = now
                watch.converged.append(converged)

            stalled_for = now - watch.changed_at
            window = list(watch.converged)

        advancing = source.running and snapshot.running and snapshot.speed > 0
        stalled = advancing and stalled_for >= self._stall_steps * source.step_seconds
        error = source.error

        if error is not None:
            status = "failed"
        elif source.closed:
            status = "closed"
        elif not source.running:
            status = "stopped"
        elif not snapshot.running or snapshot.speed <= 0:
            status = "paused"
        elif stalled:
            status = "stalled"
        elif not converged:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "running": source.running,
            "sim_time": snapshot.sim_time,
            "seconds_since_progress": stalled_for,
            "error": None if error is None else f"{type(error).__name__}: {error}",
            "solver": {
                "converged": converged,
                "iterations": snapshot.solver["iterations"],
                "residual": snapshot.solver["residual"],
                "convergence_rate": sum(window) / len(window),
                "samples": len(window),
            },
        }


def create_health_blueprint(
    get_schedulers: Callable[[], Mapping[str, EngineSource]],
    get_session_count: Callable[[], int],
    monitor: HealthMonitor | None = None,
) -> Blueprint:
    blueprint = Blueprint("health", __name__)
    monitor = monitor if monitor is not None else HealthMonitor()

    @blueprint.get("/health/live")
    def live() -> ResponseReturnValue:
        return jsonify({"status": "ok"}), 200

    @blueprint.get("/health/engine")
    def engine() -> ResponseReturnValue:
        engines = {name: monitor.observe(source) for name, source in get_schedulers().items()}
        unhealthy = any(row["status"] in UNHEALTHY for row in engines.values())

        return (
            jsonify(
                {
                    "status": "unhealthy" if unhealthy else "ok",
                    "sessions": get_session_count(),
                    "engines": engines,
                }
            ),
            503 if unhealthy else 200,
        )

    return blueprint
