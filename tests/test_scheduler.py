import json
import logging
import threading
import time

import pytest

from app.engine.engine import Engine
from app.engine.network import SolverError
from app.engine.scheduler import Cadence, Scheduler
from app.engine.snapshot import build_snapshot


WAIT = 5.0


class FakeEngine:
    """Records steps and can hold one open, so tests coordinate with Events
    rather than sleeps. Counts every call to step() and records whether two
    ever ran at once."""

    def __init__(self, target=None):
        self.dts = []
        self.reached = threading.Event()
        self.target = target
        self.gate = None
        self.entered = threading.Event()
        self.fail_on = None
        self.calls = 0
        self.overlapped = False
        self._active = 0
        self._count = threading.Lock()

    def step(self, dt):
        with self._count:
            self.calls += 1
            self._active += 1
            self.overlapped = self.overlapped or self._active > 1

        try:
            return self._step(dt)
        finally:
            with self._count:
                self._active -= 1

    def _step(self, dt):
        self.entered.set()

        if self.gate is not None:
            assert self.gate.wait(WAIT)

        if self.fail_on == len(self.dts) + 1:
            raise RuntimeError("boom")

        self.dts.append(dt)

        if self.target is not None and len(self.dts) >= self.target:
            self.reached.set()

        return build_snapshot(sim_time=float(len(self.dts)), speed=1.0, running=True, equipment={})

    def snapshot(self):
        return build_snapshot(sim_time=float(len(self.dts)), speed=1.0, running=True, equipment={})


def live_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


@pytest.fixture(autouse=True)
def no_orphans():
    yield

    assert not live_workers()


# Cadence, on fake times


def test_on_time_steps_hold_the_deadline_grid_without_drift():
    cadence = Cadence(1.0, start=100.0)

    for n in range(1, 50):
        started = 100.0 + (n - 1) + 0.2
        assert cadence.complete(started, started + 0.3) is None
        assert cadence.deadline == pytest.approx(100.0 + n)


def test_delay_counts_down_to_the_deadline_and_floors_at_zero():
    cadence = Cadence(1.0, start=10.0)

    assert cadence.delay(9.25) == pytest.approx(0.75)
    assert cadence.delay(10.5) == 0.0


def test_step_finishing_past_the_next_deadline_reports_the_overrun():
    cadence = Cadence(1.0, start=0.0)

    overrun = cadence.complete(0.0, 1.4)

    assert overrun == pytest.approx(0.4)
    # Caught up by running the next step at once, not by stretching time.
    assert cadence.deadline == pytest.approx(1.0)
    assert cadence.delay(1.4) == 0.0


def test_overrun_of_a_whole_interval_reanchors_instead_of_bursting():
    cadence = Cadence(1.0, start=0.0)

    overrun = cadence.complete(0.0, 600.0)

    assert overrun == pytest.approx(599.0)
    assert cadence.deadline == pytest.approx(600.0)


def test_step_ending_exactly_on_the_next_deadline_is_not_slow():
    cadence = Cadence(1.0, start=0.0)

    assert cadence.complete(0.0, 1.0) is None


def test_non_positive_interval_is_refused():
    with pytest.raises(ValueError):
        Cadence(0.0, start=0.0)

    with pytest.raises(ValueError):
        Scheduler(FakeEngine(), step_seconds=-1.0)


# Lifecycle


def test_start_steps_the_engine_with_a_fixed_dt_and_stop_joins():
    engine = FakeEngine(target=5)
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    assert scheduler.running
    assert engine.reached.wait(WAIT)

    scheduler.stop()

    assert not scheduler.running
    assert not live_workers()
    assert set(engine.dts) == {0.001}


def test_double_start_does_not_create_a_second_worker():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.01)

    scheduler.start()
    scheduler.start()

    assert len(live_workers()) == 1

    scheduler.stop()


def test_repeated_stop_and_stop_before_start_are_safe():
    scheduler = Scheduler(FakeEngine(), step_seconds=0.01)

    scheduler.stop()
    scheduler.start()
    scheduler.stop()
    scheduler.stop()

    assert not scheduler.running


def test_scheduler_can_start_again_after_stop():
    engine = FakeEngine(target=2)
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    assert engine.reached.wait(WAIT)
    scheduler.stop()

    engine.reached.clear()
    engine.target = len(engine.dts) + 2

    scheduler.start()
    assert engine.reached.wait(WAIT)
    scheduler.stop()


def test_stop_wakes_a_worker_sleeping_out_a_long_interval():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=3600.0)

    scheduler.start()
    assert engine.entered.wait(WAIT)

    began = time.monotonic()
    scheduler.stop()

    assert time.monotonic() - began < WAIT
    assert not live_workers()


def test_stop_from_the_worker_itself_is_refused():
    errors = []

    class Reentrant(FakeEngine):
        def step(self, dt):
            try:
                scheduler.stop()
            except RuntimeError as exc:
                errors.append(exc)

            return super().step(dt)

    engine = Reentrant(target=1)
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    assert engine.reached.wait(WAIT)
    scheduler.stop()

    assert errors


# Rate


def test_step_rate_holds_over_a_sustained_run():
    engine = FakeEngine(target=40)
    interval = 0.01
    scheduler = Scheduler(engine, step_seconds=interval)

    began = time.monotonic()
    scheduler.start()
    assert engine.reached.wait(WAIT)
    elapsed = time.monotonic() - began
    scheduler.stop()

    # 40 steps start at 0, interval, ... 39*interval. Deadline scheduling
    # cannot run early; broad upper bound leaves room for a loaded CI host.
    assert elapsed >= 39 * interval * 0.95
    assert elapsed < 39 * interval * 4


def test_worker_reads_time_only_through_the_injected_monotonic_clock():
    now = [0.0]
    engine = FakeEngine(target=3)
    scheduler = Scheduler(engine, step_seconds=1.0, monotonic=lambda: now[0])

    # Frozen time: the first step is due at once, the next never arrives.
    scheduler.start()
    assert engine.entered.wait(WAIT)
    scheduler.stop()

    assert len(engine.dts) == 1


# Slow steps


def test_slow_step_logs_a_warning_with_interval_and_duration(caplog):
    ticks = iter([0.0, 0.0, 0.0, 2.5])
    last = [0.0]

    def monotonic():
        last[0] = next(ticks, last[0])

        return last[0]

    engine = FakeEngine(target=1)
    scheduler = Scheduler(engine, step_seconds=1.0, monotonic=monotonic)

    with caplog.at_level(logging.WARNING, logger="app.engine.scheduler"):
        scheduler.start()
        assert engine.reached.wait(WAIT)
        scheduler.stop()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]

    assert len(warnings) == 1
    assert "slow simulation step" in warnings[0].getMessage()
    assert "interval 1.000s" in warnings[0].getMessage()
    assert "step took 2.500s" in warnings[0].getMessage()
    # dt is untouched by the lateness.
    assert set(engine.dts) == {1.0}


def test_on_time_steps_do_not_warn(caplog):
    engine = FakeEngine(target=10)
    scheduler = Scheduler(engine, step_seconds=0.05)

    with caplog.at_level(logging.WARNING, logger="app.engine.scheduler"):
        scheduler.start()
        assert engine.reached.wait(WAIT)
        scheduler.stop()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# Failure


def test_step_exception_is_logged_recorded_and_stops_the_worker(caplog):
    engine = FakeEngine()
    engine.fail_on = 2
    scheduler = Scheduler(engine, step_seconds=0.001)

    with caplog.at_level(logging.ERROR, logger="app.engine.scheduler"):
        scheduler.start()
        scheduler._thread.join(WAIT)

    assert not scheduler.running
    assert isinstance(scheduler.error, RuntimeError)
    assert any(r.exc_info for r in caplog.records)
    assert len(engine.dts) == 1

    scheduler.stop()


def test_start_after_failure_clears_the_error():
    engine = FakeEngine(target=3)
    engine.fail_on = 1
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    scheduler._thread.join(WAIT)
    assert scheduler.error is not None

    engine.fail_on = None
    scheduler.start()
    assert engine.reached.wait(WAIT)
    scheduler.stop()

    assert scheduler.error is None


def test_a_solve_that_raises_stops_the_worker_and_keeps_the_last_snapshot_clean():
    # DEFECT REPRODUCTION (R2): a non-finite curve used to "converge" with
    # residual=nan, so the worker kept running and published a snapshot
    # that is not strict JSON.
    from tests.test_network_solver import single_branch

    topology = single_branch()
    engine = Engine(topology.devices.values(), topology=topology)
    stepped = threading.Event()
    real_step = engine.step

    def signalling_step(dt):
        snapshot = real_step(dt)
        stepped.set()

        return snapshot

    engine.step = signalling_step
    scheduler = Scheduler(engine, step_seconds=0.001)

    try:
        scheduler.start()
        assert stepped.wait(WAIT)

        with scheduler.step_lock:
            topology.branch("B-01").device.resistance = float("nan")

        scheduler._thread.join(WAIT)

        assert not scheduler.running
        assert isinstance(scheduler.error, SolverError)
        assert "B-01" in str(scheduler.error)

        published = scheduler.snapshot().as_dict()
        json.dumps(published, allow_nan=False)
        assert published["solver"]["converged"] is True
    finally:
        scheduler.stop()


# Snapshots


def test_snapshot_never_shows_a_step_in_progress():
    engine = FakeEngine()
    engine.gate = threading.Event()
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    assert engine.entered.wait(WAIT)

    # Mid-step nothing has been published, so a reader sees the pre-step
    # state, not a partial one.
    assert scheduler._latest is None

    engine.gate.set()

    deadline = time.monotonic() + WAIT
    while scheduler._latest is None and time.monotonic() < deadline:
        time.sleep(0.001)

    scheduler.stop()

    assert scheduler.snapshot().sim_time >= 1.0


def test_external_mutation_waits_for_the_step_lock():
    engine = FakeEngine()
    engine.gate = threading.Event()
    scheduler = Scheduler(engine, step_seconds=0.001)
    acquired = threading.Event()

    def mutate():
        with scheduler.step_lock:
            acquired.set()

    scheduler.start()
    assert engine.entered.wait(WAIT)

    thread = threading.Thread(target=mutate)
    thread.start()

    assert not acquired.wait(0.05)

    engine.gate.set()
    assert acquired.wait(WAIT)

    thread.join()
    scheduler.stop()


# Commands and manual steps


def test_command_publishes_without_advancing_time():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.001)
    applied = []

    scheduler.step_once()
    scheduler.step_once()
    published = scheduler.command(lambda: applied.append(True))

    assert applied == [True]
    assert published.sim_time == 2.0
    assert scheduler.snapshot() is published
    assert engine.calls == 2


def test_manual_step_steps_once_and_publishes():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.001)

    published = scheduler.step_once()

    assert published.sim_time == 1.0
    assert scheduler.snapshot() is published
    assert engine.dts == [0.001]


def test_manual_step_while_running_is_refused_without_stepping():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    engine.gate = threading.Event()
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()

    try:
        assert engine.entered.wait(WAIT)

        assert scheduler.step_once() is None
        assert engine.calls == 1
    finally:
        engine.gate.set()
        scheduler.stop()

    assert not engine.overlapped


def test_start_during_a_manual_step_waits_for_it_with_no_overlap():
    # DEFECT REPRODUCTION
    engine = FakeEngine(target=3)
    engine.gate = threading.Event()
    scheduler = Scheduler(engine, step_seconds=0.001)

    manual = threading.Thread(target=scheduler.step_once)
    manual.start()
    assert engine.entered.wait(WAIT)

    starter = threading.Thread(target=scheduler.start)
    starter.start()
    starter.join(0.05)

    assert starter.is_alive()
    assert not live_workers()

    engine.gate.set()
    manual.join(WAIT)
    starter.join(WAIT)

    assert engine.reached.wait(WAIT)
    scheduler.stop()

    assert not engine.overlapped


def test_close_during_a_manual_step_completes_once_the_step_finishes():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    engine.gate = threading.Event()
    scheduler = Scheduler(engine, step_seconds=0.001)
    results = []

    manual = threading.Thread(target=lambda: results.append(scheduler.step_once()))
    manual.start()
    assert engine.entered.wait(WAIT)

    closer = threading.Thread(target=scheduler.close)
    closer.start()
    engine.gate.set()
    closer.join(WAIT)
    manual.join(WAIT)

    assert not closer.is_alive()
    assert scheduler.closed
    assert results[0].sim_time == 1.0


def test_manual_step_after_close_is_refused_without_stepping():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.close()

    assert scheduler.step_once() is None
    assert engine.calls == 0


def test_close_stops_a_running_worker():
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    assert engine.entered.wait(WAIT)
    scheduler.close()

    assert not scheduler.running
    assert not live_workers()


def test_start_after_close_creates_no_worker():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.close()
    scheduler.start()

    assert not scheduler.running
    assert not live_workers()
    assert engine.calls == 0


def test_close_after_start_joins_the_worker_and_stays_closed():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    assert engine.entered.wait(WAIT)
    scheduler.close()
    assert not live_workers()

    scheduler.start()

    assert not scheduler.running
    assert not live_workers()


def test_manual_step_is_allowed_after_the_worker_stops_on_an_error():
    # DEFECT REPRODUCTION
    engine = FakeEngine()
    engine.fail_on = 1
    scheduler = Scheduler(engine, step_seconds=0.001)

    scheduler.start()
    scheduler._thread.join(WAIT)

    assert not scheduler.running
    assert scheduler.error is not None

    engine.fail_on = None
    published = scheduler.step_once()

    assert published.sim_time == 1.0
    assert scheduler.snapshot() is published

    scheduler.stop()


# Real engine


def test_scheduled_engine_matches_manual_stepping():
    from app.plant.loader import load_plant
    from app.engine.sessions import COMPRESSOR_PLANT

    scheduled = Engine.from_plant(load_plant(COMPRESSOR_PLANT))
    manual = Engine.from_plant(load_plant(COMPRESSOR_PLANT))
    reached = threading.Event()
    calls = [0]
    real_step = scheduled.step

    def counting_step(dt):
        result = real_step(dt)
        calls[0] += 1

        if calls[0] >= 5:
            reached.set()

        return result

    scheduled.step = counting_step

    scheduler = Scheduler(scheduled, step_seconds=0.001)
    scheduler.start()
    assert reached.wait(WAIT)
    scheduler.stop()

    for _ in range(calls[0]):
        manual.step(0.001)

    assert scheduled.snapshot().sim_time == manual.snapshot().sim_time
    assert scheduled.snapshot().equipment == manual.snapshot().equipment
    assert scheduled.snapshot().nodes == manual.snapshot().nodes


def test_paused_clock_does_not_advance_and_resume_continues():
    from app.plant.loader import load_plant
    from app.engine.sessions import COMPRESSOR_PLANT

    engine = Engine.from_plant(load_plant(COMPRESSOR_PLANT))
    counted = threading.Event()
    calls = [0]
    real_step = engine.step

    def counting_step(dt):
        result = real_step(dt)
        calls[0] += 1

        if calls[0] >= 3:
            counted.set()

        return result

    engine.step = counting_step
    engine.stop()

    scheduler = Scheduler(engine, step_seconds=0.001)
    scheduler.start()
    assert counted.wait(WAIT)

    with scheduler.step_lock:
        assert engine.clock.sim_time == 0.0
        assert not scheduler.snapshot().running

        engine.start()
        calls[0] = 0
        counted.clear()

    assert counted.wait(WAIT)
    scheduler.stop()

    assert engine.clock.sim_time > 0.0
