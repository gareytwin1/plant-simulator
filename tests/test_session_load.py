"""N concurrent training sessions stay within the step budget (T18-5, T16-8).

N is config.MAX_SESSIONS and each session runs one scheduler worker over one
config-loaded plant, so the registry's worst case is N workers sharing one
interpreter. The budget is the simulation interval: everything one interval has
to do must fit inside it.
"""

import threading
import time

from app import config
from app.engine.sessions import SessionRegistry
from app.training.session import TrainingSession

SESSIONS = config.MAX_SESSIONS
INTERVAL = config.SIMULATION_STEP_SECONDS
WAIT = 30.0


def live_scheduler_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


def training_registry():
    registry = SessionRegistry(factory=TrainingSession)

    for index in range(SESSIONS):
        registry.create(f"train-{index}")

    assert len(registry) == SESSIONS

    return registry


def training_schedulers_of(registry):
    return [registry.get(f"train-{index}").training_scheduler for index in range(SESSIONS)]


def end_all_training(registry):
    for index in range(SESSIONS):
        registry.end(f"train-{index}")


def test_one_round_of_every_training_plant_fits_in_half_the_step_interval():
    registry = training_registry()

    try:
        schedulers = training_schedulers_of(registry)

        started = time.perf_counter()
        for scheduler in schedulers:
            assert scheduler.step_once() is not None
        elapsed = time.perf_counter() - started
    finally:
        end_all_training(registry)

    assert elapsed < INTERVAL / 2, f"{len(schedulers)} plants took {elapsed:.3f}s per round"


def test_every_training_worker_keeps_stepping_and_all_shut_down_cleanly():
    registry = training_registry()
    schedulers = training_schedulers_of(registry)
    workers_before = len(live_scheduler_workers())
    first = [scheduler.snapshot().sim_time for scheduler in schedulers]

    try:
        for scheduler in schedulers:
            scheduler.start()

        started_workers = len(live_scheduler_workers()) - workers_before

        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if all(s.snapshot().sim_time >= t + 2 * INTERVAL for s, t in zip(schedulers, first)):
                break
            time.sleep(0.05)

        stalled = [s for s, t in zip(schedulers, first) if s.snapshot().sim_time < t + 2 * INTERVAL]
        failed = [s for s in schedulers if s.error is not None]
    finally:
        end_all_training(registry)

    assert started_workers == SESSIONS
    assert not stalled
    assert not failed
    assert len(live_scheduler_workers()) == workers_before
    assert len(registry) == 0
