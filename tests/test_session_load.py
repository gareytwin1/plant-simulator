"""N concurrent sessions stay within the step budget (T18-5).

N is config.MAX_SESSIONS and each session runs two scheduler workers, so the
registry's worst case is 2N workers sharing one interpreter. The budget is the
simulation interval: everything one interval has to do must fit inside it.
"""

import logging
import threading
import time

from app import config
from app.engine.sessions import SessionRegistry

SESSIONS = config.MAX_SESSIONS
INTERVAL = config.SIMULATION_STEP_SECONDS
WAIT = 30.0


def live_scheduler_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


def schedulers_of(registry):
    return [
        scheduler
        for index in range(SESSIONS)
        for session in [registry.get(f"load-{index}")]
        for scheduler in (session.compressor_scheduler, session.pump_scheduler)
    ]


def filled_registry():
    registry = SessionRegistry()

    for index in range(SESSIONS):
        registry.create(f"load-{index}")

    assert len(registry) == SESSIONS

    return registry


def test_one_round_of_every_engine_fits_in_half_the_step_interval():
    # Measured without threads: the GIL serialises the workers, so one round
    # stepping all 2N engines is the real work due in each interval. Half the
    # interval leaves margin for a loaded machine.
    registry = filled_registry()
    schedulers = schedulers_of(registry)

    started = time.perf_counter()
    for scheduler in schedulers:
        assert scheduler.step_once() is not None
    elapsed = time.perf_counter() - started

    for index in range(SESSIONS):
        registry.end(f"load-{index}")

    assert elapsed < INTERVAL / 2, f"{len(schedulers)} engines took {elapsed:.3f}s per round"


def test_every_worker_keeps_stepping_and_all_shut_down_cleanly(caplog):
    registry = filled_registry()
    schedulers = schedulers_of(registry)
    workers_before = len(live_scheduler_workers())

    with caplog.at_level(logging.WARNING, logger="app.engine.scheduler"):
        for scheduler in schedulers:
            scheduler.start()

        assert len(live_scheduler_workers()) == workers_before + 2 * SESSIONS

        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if all(s.snapshot().sim_time >= 2 * INTERVAL for s in schedulers):
                break
            time.sleep(0.05)

    stalled = [s for s in schedulers if s.snapshot().sim_time < 2 * INTERVAL]
    failed = [s for s in schedulers if s.error is not None]
    slow = [r for r in caplog.records if r.levelno == logging.WARNING]

    for index in range(SESSIONS):
        registry.end(f"load-{index}")

    assert not stalled
    assert not failed
    assert not slow
    assert len(live_scheduler_workers()) == workers_before
    assert len(registry) == 0
