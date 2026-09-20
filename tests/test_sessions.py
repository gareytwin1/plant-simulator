import threading

from app.engine.sessions import Session, SessionRegistry


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
    session_a.step_compressor()

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
