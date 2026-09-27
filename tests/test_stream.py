import itertools
import json
import time

import pytest
from flask import Flask

from app.api.stream import create_stream_blueprint, format_event, stream_events
from app.engine.engine import Engine
from app.engine.scheduler import Scheduler
from app.engine.snapshot import build_snapshot
from app.equipment.compressor import GasCompressor


class FakeSource:
    """A snapshot source whose current value the test controls directly,
    with no queue or history behind it - exactly the shape stream_events
    is written against."""

    def __init__(self):
        self.sim_time = 0.0

    def snapshot(self):
        return build_snapshot(sim_time=self.sim_time, speed=1.0, running=True, equipment={})


class FakeClock:
    """A monotonic() that plays back a sequence of times, one per call, so
    a test can dictate exactly how long each yield's write appeared to
    take without a real socket or a real sleep. `times` may be an infinite
    iterator (e.g. itertools.count) - only ever pulled from lazily."""

    def __init__(self, times):
        self._times = iter(times)

    def __call__(self):
        return next(self._times)


class RecordingSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


# ---- format_event ----


def test_format_event_is_one_sse_data_frame_carrying_c4_json():
    snapshot = build_snapshot(sim_time=3.0, speed=1.0, running=True, equipment={})

    event = format_event(snapshot)

    assert event.startswith("data: ")
    assert event.endswith("\n\n")
    payload = json.loads(event[len("data: "):-2])
    assert payload == snapshot.as_dict()


# ---- stream_events: the pure core, no Flask involved ----


def test_stream_events_sleeps_the_configured_interval_before_each_event():
    source = FakeSource()
    sleep = RecordingSleep()
    clock = FakeClock([0.0, 0.1, 0.1, 0.2, 0.2, 0.3])

    events = list(itertools.islice(
        stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock),
        3,
    ))

    assert len(events) == 3
    assert sleep.calls == [1.0, 1.0, 1.0]


def test_stream_events_reflects_the_current_snapshot_not_a_queued_backlog():
    source = FakeSource()
    sleep = RecordingSleep()
    clock = FakeClock(itertools.count(0.0, 0.1))

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)

    source.sim_time = 1.0
    first = json.loads(next(gen)[len("data: "):-2])
    source.sim_time = 99.0
    second = json.loads(next(gen)[len("data: "):-2])

    assert first["sim_time"] == pytest.approx(1.0)
    assert second["sim_time"] == pytest.approx(99.0)


def test_stream_events_continues_when_the_write_lands_within_budget():
    source = FakeSource()
    sleep = RecordingSleep()
    # Every write appears to take 2s against a 5s dropout budget
    # (interval_seconds=1.0 * dropout_intervals=5.0) - well inside it.
    clock = FakeClock([0.0, 2.0, 2.0, 4.0, 4.0, 6.0, 6.0, 8.0])

    events = list(itertools.islice(
        stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0),
        4,
    ))

    assert len(events) == 4


def test_stream_events_ends_when_a_write_alone_exceeds_the_dropout_budget():
    source = FakeSource()
    sleep = RecordingSleep()
    # First write takes 1s (fine); second takes 9s against a 5s budget -
    # a wedged client, not a briefly slow one.
    clock = FakeClock([0.0, 1.0, 1.0, 10.0])

    events = list(stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0))

    assert len(events) == 2
    assert sleep.calls == [1.0, 1.0]


def test_stream_events_dropout_ignores_time_spent_sleeping():
    source = FakeSource()
    sleep = RecordingSleep()
    # The gap the dropout check measures starts *after* sleep() returns, so
    # a long interval_seconds must never by itself count as a slow write.
    clock = FakeClock([0.0, 0.05, 0.05, 0.1])

    events = list(itertools.islice(
        stream_events(source, interval_seconds=100.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0),
        2,
    ))

    assert len(events) == 2


def test_create_stream_blueprint_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        create_stream_blueprint(lambda: FakeSource(), interval_seconds=0.0)

    with pytest.raises(ValueError):
        create_stream_blueprint(lambda: FakeSource(), interval_seconds=-1.0)


# ---- the Flask blueprint: GET /api/stream end to end ----


def _read_events(response, count):
    """Pull `count` SSE frames off a streaming test-client response without
    ever fully consuming its (endless) generator."""
    chunks = itertools.islice(iter(response.response), count)

    return [json.loads(chunk.decode()[len("data: "):-2]) for chunk in chunks]


def build_app(interval_seconds=0.01):
    source = FakeSource()

    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: source, interval_seconds))

    return app, source


def test_get_stream_is_an_event_stream_of_snapshots():
    app, source = build_app()
    client = app.test_client()
    source.sim_time = 7.0

    # The test client's WSGI dispatch pulls the first chunk from the
    # response generator during get() itself (to catch a start_response
    # deferred to first iteration), so the source must already hold the
    # value under test before this call, not after it.
    response = client.get("/api/stream")
    try:
        assert response.mimetype == "text/event-stream"

        payloads = _read_events(response, 2)

        assert all(payload["sim_time"] == pytest.approx(7.0) for payload in payloads)
    finally:
        response.close()


def test_get_stream_disconnect_and_reconnect_each_see_the_current_state():
    app, source = build_app()
    client = app.test_client()

    first = client.get("/api/stream")
    source.sim_time = 1.0
    _read_events(first, 1)
    first.close()

    source.sim_time = 42.0
    second = client.get("/api/stream")
    try:
        payloads = _read_events(second, 1)

        assert payloads[0]["sim_time"] == pytest.approx(42.0)
    finally:
        second.close()


def test_a_slow_consumer_does_not_stall_the_engines_own_scheduler():
    engine = Engine(equipment=[GasCompressor()])
    scheduler = Scheduler(engine, step_seconds=0.01)
    scheduler.start()

    try:
        app = Flask(__name__)
        app.register_blueprint(create_stream_blueprint(lambda: scheduler, interval_seconds=0.01))
        client = app.test_client()

        response = client.get("/api/stream")
        try:
            _read_events(response, 1)
            before = scheduler.snapshot().sim_time

            # A "slow consumer" that never reads any more of this response;
            # nothing about that can be observed from here except that the
            # scheduler's own worker thread - unrelated to this connection -
            # keeps advancing regardless.
            time.sleep(0.2)

            after = scheduler.snapshot().sim_time
            assert after > before
        finally:
            response.close()
    finally:
        scheduler.close()
