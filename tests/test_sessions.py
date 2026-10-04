import threading
import time

import pytest

from app.engine import sessions
from app.engine.sessions import Session, SessionRegistry


WAIT = 5.0


def live_scheduler_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


def test_create_returns_a_session_with_fresh_equipment():
    registry = SessionRegistry()

    session = registry.create("abc")

    assert isinstance(session, Session)
    assert session.compressor.running is False
    assert session.pump.running is False


def test_get_returns_the_same_session_on_repeated_lookups():
    registry = SessionRegistry()
    created = registry.create("abc")

    assert registry.get("abc") is created
    assert registry.get("abc") is created


def test_get_unknown_id_returns_none():
    registry = SessionRegistry()

    assert registry.get("nope") is None


def test_get_or_create_creates_once_then_reuses():
    registry = SessionRegistry()

    first = registry.get_or_create("abc")
    second = registry.get_or_create("abc")

    assert first is second
    assert len(registry) == 1


def test_two_sessions_have_independent_equipment_with_no_cross_talk():
    registry = SessionRegistry()
    session_a = registry.create("a")
    session_b = registry.create("b")

    session_a.compressor.set_load_target(0.8)
    session_a.compressor.start()
    session_a.compressor_scheduler.step_once()

    assert session_a.compressor.running is True
    assert session_b.compressor.running is False
    assert session_b.compressor.load_target == 0.0
    assert session_a.compressor is not session_b.compressor
    assert session_a.pump is not session_b.pump


def test_end_releases_the_session():
    registry = SessionRegistry()
    registry.create("abc")

    registry.end("abc")

    assert registry.get("abc") is None
    assert len(registry) == 0


def test_end_unknown_id_is_a_no_op():
    registry = SessionRegistry()

    registry.end("nope")

    assert len(registry) == 0


# Scheduler ownership (T2-6)


def test_session_construction_creates_schedulers_but_starts_neither():
    session = Session()

    assert session.compressor_scheduler.running is False
    assert session.pump_scheduler.running is False
    assert not live_scheduler_workers()


def test_session_end_stops_and_joins_both_workers_even_if_never_started():
    session = Session()

    session.end()

    assert session.compressor_scheduler.running is False
    assert session.pump_scheduler.running is False


def test_session_end_stops_and_joins_workers_that_were_started():
    session = Session()
    session.compressor_scheduler.start()
    session.pump_scheduler.start()

    assert session.compressor_scheduler.running is True
    assert session.pump_scheduler.running is True

    session.end()

    assert session.compressor_scheduler.running is False
    assert session.pump_scheduler.running is False
    assert not live_scheduler_workers()


def test_registry_end_stops_the_sessions_workers_before_dropping_it():
    registry = SessionRegistry()
    session = registry.create("abc")
    session.compressor_scheduler.start()

    registry.end("abc")

    assert registry.get("abc") is None
    assert session.compressor_scheduler.running is False
    assert not live_scheduler_workers()


def test_create_at_capacity_evicts_the_least_recently_touched_session():
    tick = iter(range(100))
    registry = SessionRegistry(max_sessions=2, monotonic=lambda: next(tick))

    session_a = registry.create("a")  # touched at 0
    session_b = registry.create("b")  # touched at 1

    session_a.compressor_scheduler.start()
    session_b.compressor_scheduler.start()

    registry.get("b")  # touched at 2, so "a" is now the LRU session

    session_c = registry.create("c")  # over capacity, evicts "a"

    assert registry.get("a") is None
    assert registry.get("b") is session_b
    assert registry.get("c") is session_c
    assert len(registry) == 2

    # Eviction stopped and joined "a"'s worker.
    assert session_a.compressor_scheduler.running is False

    session_b.end()
    session_c.end()


def test_touching_a_session_protects_it_from_eviction():
    tick = iter(range(100))
    registry = SessionRegistry(max_sessions=2, monotonic=lambda: next(tick))

    session_a = registry.create("a")  # touched at 0
    registry.create("b")  # touched at 1

    registry.get("a")  # touched at 2, "b" is now the LRU session

    registry.create("c")  # evicts "b", not "a"

    assert registry.get("a") is session_a
    assert registry.get("b") is None
    assert registry.get("c") is not None


def test_registry_rejects_a_capacity_below_one():
    # Capacity of zero would make eviction pick a session from an empty
    # registry, so it is refused at construction rather than at create().
    with pytest.raises(ValueError):
        SessionRegistry(max_sessions=0)


def test_a_command_reaches_the_published_state_without_advancing_time():
    # DEFECT REPRODUCTION
    session = Session()
    scheduler = session.compressor_scheduler

    scheduler.step_once()
    before = scheduler.snapshot().sim_time
    scheduler.command(session.compressor.start)

    assert session.compressor_state()["running"] is True
    assert scheduler.snapshot().sim_time == before


# Atomic admission and permanent closure (R7)


def constructing_together(monkeypatch, parties):
    """Make every Session construction wait until `parties` of them are
    under way, so racing admissions all build before any is admitted."""
    barrier = threading.Barrier(parties, timeout=WAIT)
    built = []

    class Rendezvous(Session):
        def __init__(self):
            super().__init__()
            built.append(self)
            barrier.wait()

    monkeypatch.setattr(sessions, "Session", Rendezvous)

    return built


def run_together(target, args_list):
    results = [None] * len(args_list)

    def run(i, args):
        results[i] = target(*args)

    threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(args_list)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(WAIT)
        assert not thread.is_alive()

    return results


def closed(session):
    return session.compressor_scheduler.closed and session.pump_scheduler.closed


def test_concurrent_creates_at_capacity_leave_one_session_and_close_the_rest(monkeypatch):
    # DEFECT REPRODUCTION
    ids = [f"s{i}" for i in range(8)]
    built = constructing_together(monkeypatch, len(ids))
    registry = SessionRegistry(max_sessions=1)

    run_together(registry.create, [(i,) for i in ids])

    assert len(registry) == 1
    (admitted,) = [registry.get(i) for i in ids if registry.get(i) is not None]
    assert [s for s in built if not closed(s)] == [admitted]


def test_concurrent_get_or_create_of_one_id_returns_one_object(monkeypatch):
    # DEFECT REPRODUCTION
    n = 8
    built = constructing_together(monkeypatch, n)
    registry = SessionRegistry()

    results = run_together(registry.get_or_create, [("abc",)] * n)

    assert all(result is results[0] for result in results)
    assert registry.get("abc") is results[0]
    assert len(registry) == 1
    assert [s for s in built if not closed(s)] == [results[0]]


def test_evicted_session_cannot_start_a_worker_once_create_returns():
    # DEFECT REPRODUCTION: a request that resolved the victim before it was
    # evicted must not be able to start a worker the registry no longer counts.
    registry = SessionRegistry(max_sessions=1)
    victim = registry.create("a")
    victim.compressor_scheduler.start()
    victim.pump_scheduler.start()

    survivor = registry.create("b")

    assert not live_scheduler_workers()
    assert closed(victim)

    victim.compressor_scheduler.start()
    victim.pump_scheduler.start()

    assert not live_scheduler_workers()

    survivor.end()


def test_session_end_then_start_creates_no_thread():
    # DEFECT REPRODUCTION
    session = Session()

    session.end()
    session.compressor_scheduler.start()
    session.pump_scheduler.start()

    assert not live_scheduler_workers()


def test_session_start_then_end_joins_both_workers():
    # DEFECT REPRODUCTION
    session = Session()
    session.compressor_scheduler.start()
    session.pump_scheduler.start()

    session.end()

    assert not live_scheduler_workers()
    assert closed(session)


class ContendedLock:
    """A step_lock stand-in that reports when a second thread waits on it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()

    def __exit__(self, *exc_info):
        self._lock.release()


def test_eviction_while_the_victims_step_lock_is_held_completes_after_release():
    # DEFECT REPRODUCTION: a route mid-command on the victim holds its
    # step_lock and the victim's worker is queued behind it. Eviction must
    # wait for both, then leave the victim closed, not deadlock.
    registry = SessionRegistry(max_sessions=1)
    victim = registry.create("a")
    scheduler = victim.compressor_scheduler
    scheduler.step_lock = ContendedLock()

    holding = threading.Event()
    release = threading.Event()

    def slow_action():
        holding.set()
        assert release.wait(WAIT)

    commander = threading.Thread(target=scheduler.command, args=(slow_action,))
    commander.start()
    assert holding.wait(WAIT)

    # The worker's first step is due at once, so it queues on the held lock.
    scheduler.start()
    assert scheduler.step_lock.contended.wait(WAIT)

    evictor = threading.Thread(target=registry.create, args=("b",))
    evictor.start()

    deadline = time.monotonic() + WAIT
    while not scheduler.closed and time.monotonic() < deadline:
        time.sleep(0.001)

    assert scheduler.closed
    evictor.join(0.05)
    assert evictor.is_alive()

    release.set()
    commander.join(WAIT)
    evictor.join(WAIT)

    assert not commander.is_alive()
    assert not evictor.is_alive()
    assert registry.get("a") is None
    assert closed(victim)
    assert not live_scheduler_workers()

    registry.end("b")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def registry_with_clock(idle_seconds=60.0, max_sessions=8):
    clock = FakeClock()
    registry = SessionRegistry(
        max_sessions=max_sessions,
        monotonic=clock,
        idle_seconds=idle_seconds,
    )

    return registry, clock


def test_idle_session_is_reclaimed_and_its_workers_stopped():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("abandoned")
    session.compressor_scheduler.start()
    session.pump_scheduler.start()

    clock.advance(60.0)

    assert registry.reclaim_idle() == 1
    assert len(registry) == 0
    assert session.compressor_scheduler.closed and not session.compressor_scheduler.running
    assert session.pump_scheduler.closed and not session.pump_scheduler.running


def test_session_idle_for_less_than_the_timeout_is_kept():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    kept = registry.create("kept")

    clock.advance(59.9)

    assert registry.reclaim_idle() == 0
    assert registry.get("kept") is kept


def test_a_touched_session_is_not_reclaimed_while_a_stale_one_is():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    busy = registry.create("busy")
    registry.create("stale")

    clock.advance(40.0)
    registry.get("busy")
    clock.advance(40.0)

    assert registry.reclaim_idle() == 1
    assert registry.get("busy") is busy
    assert len(registry) == 1


def test_get_of_an_idle_id_finds_it_gone():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("abandoned")

    clock.advance(61.0)

    assert registry.get("abandoned") is None


def test_create_reclaims_idle_sessions_before_admitting_a_new_one():
    registry, clock = registry_with_clock(idle_seconds=60.0, max_sessions=2)
    old_a = registry.create("a")
    old_b = registry.create("b")

    clock.advance(61.0)
    fresh = registry.create("c")

    assert len(registry) == 1
    assert registry.get("c") is fresh
    assert old_a.compressor_scheduler.closed and old_b.compressor_scheduler.closed


def test_create_of_an_idle_id_returns_a_new_session_not_the_reclaimed_one():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    old = registry.create("abc")

    clock.advance(61.0)
    fresh = registry.create("abc")

    assert fresh is not old
    assert old.compressor_scheduler.closed
    assert not fresh.compressor_scheduler.closed


def test_reclaimed_session_is_released_from_memory():
    import gc
    import weakref

    registry, clock = registry_with_clock(idle_seconds=60.0)
    reference = weakref.ref(registry.create("abandoned"))

    clock.advance(61.0)
    registry.reclaim_idle()
    gc.collect()

    assert reference() is None


def test_a_request_holding_a_reclaimed_session_cannot_start_or_step_it():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    held = registry.create("abc")
    before = len(live_scheduler_workers())

    clock.advance(61.0)
    registry.reclaim_idle()

    held.compressor_scheduler.start()
    held.pump_scheduler.start()

    assert not held.compressor_scheduler.running
    assert held.compressor_scheduler.step_once() is None
    assert held.pump_scheduler.step_once() is None
    assert len(live_scheduler_workers()) == before


def test_the_default_idle_timeout_comes_from_config():
    from app import config

    registry = SessionRegistry()

    assert registry.idle_seconds == config.SESSION_IDLE_SECONDS


@pytest.mark.parametrize("idle_seconds", [0, -1.0])
def test_registry_rejects_a_non_positive_idle_timeout(idle_seconds):
    with pytest.raises(ValueError, match="idle_seconds"):
        SessionRegistry(idle_seconds=idle_seconds)


def test_reclaim_waits_out_a_step_in_progress_without_inverting_the_lock_order():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("abc")
    scheduler = session.compressor_scheduler
    reclaimed = threading.Event()

    def reclaim():
        registry.reclaim_idle()
        reclaimed.set()

    clock.advance(61.0)

    with scheduler.step_lock:
        # The worker's first step is due at once and queues on the held lock.
        scheduler.start()
        thread = threading.Thread(target=reclaim)
        thread.start()
        # The registry lock is taken before the scheduler's: reclaim holds it
        # and waits behind the step, it does not skip or deadlock.
        assert not reclaimed.wait(0.2)

    assert reclaimed.wait(WAIT)
    thread.join(WAIT)
    assert len(registry) == 0


def test_peek_returns_the_live_session_and_none_for_unknown_or_reclaimed_ids():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("a")

    assert registry.peek("a") is session
    assert registry.peek("unknown") is None

    clock.advance(61.0)
    registry.reclaim_idle()

    assert registry.peek("a") is None


def test_peek_does_not_move_the_idle_clock():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("probed")

    for _ in range(5):
        clock.advance(20.0)
        registry.peek("probed")

    assert registry.reclaim_idle() == 1
    assert registry.peek("probed") is None


def test_peek_returns_a_session_past_the_timeout_until_something_sweeps():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("stale")

    clock.advance(61.0)

    assert registry.peek("stale") is session
    assert len(registry) == 1


def test_peek_does_not_end_other_idle_sessions():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    stale = registry.create("stale")
    registry.create("probed")

    clock.advance(30.0)
    registry.get("probed")
    clock.advance(31.0)

    assert registry.peek("probed") is not None
    assert len(registry) == 2
    assert not stale.compressor_scheduler.closed
