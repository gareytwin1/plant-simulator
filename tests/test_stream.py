import itertools
import json
import socket
import threading
import time

import pytest
from flask import Flask
from werkzeug.serving import make_server

from app.api.stream import create_stream_blueprint, format_event, stream_events
from app.engine.engine import Engine
from app.engine.scheduler import Scheduler
from app.engine.snapshot import build_snapshot
from app.equipment.compressor import GasCompressor


class FakeSource:
    """A snapshot source whose current value the test controls directly,
    with no queue or history behind it - exactly the shape stream_events
    is written against. `closed`/`error` default off, matching a source
    with no liveness signal of its own."""

    def __init__(self):
        self.sim_time = 0.0
        self.closed = False
        self.error = None

    def snapshot(self):
        return build_snapshot(sim_time=self.sim_time, speed=1.0, running=True, equipment={})


class PaddedFakeSource:
    """A snapshot source whose payload is large enough that a handful of
    them, written back to back with nobody reading, fill a real OS socket
    send buffer in well under a second - the only way to reproduce a write
    that genuinely blocks, rather than one that merely takes a while."""

    def snapshot(self):
        return build_snapshot(
            sim_time=0.0,
            speed=1.0,
            running=True,
            equipment={"PAD": {"filler": "x" * 4000}},
        )


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


def _payload(event):
    return json.loads(event[len("data: "):-2])


# ---- format_event ----


def test_format_event_is_one_sse_data_frame_carrying_c4_json():
    snapshot = build_snapshot(sim_time=3.0, speed=1.0, running=True, equipment={})

    event = format_event(snapshot)

    assert event.startswith("data: ")
    assert event.endswith("\n\n")
    assert _payload(event) == snapshot.as_dict()


# ---- stream_events: the pure core, no Flask involved ----


def test_stream_events_yields_the_first_event_immediately_then_sleeps_between_later_ones():
    source = FakeSource()
    sleep = RecordingSleep()
    clock = FakeClock([0.0, 0.1, 0.1, 0.2, 0.2, 0.3])

    events = list(itertools.islice(
        stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock),
        3,
    ))

    assert len(events) == 3
    # Three events need only two gaps between them - a fresh connection is
    # not kept blank for a whole interval before its first event.
    assert sleep.calls == [1.0, 1.0]


def test_stream_events_reflects_the_current_snapshot_not_a_queued_backlog():
    source = FakeSource()
    sleep = RecordingSleep()
    clock = FakeClock(itertools.count(0.0, 0.1))

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)

    source.sim_time = 1.0
    first = _payload(next(gen))
    source.sim_time = 99.0
    second = _payload(next(gen))

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
    # First write (the immediate one, no sleep before it) takes 1s, fine;
    # the second takes 9s against a 5s budget - a wedged client, not a
    # briefly slow one.
    clock = FakeClock([0.0, 1.0, 1.0, 10.0])

    events = list(stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0))

    assert len(events) == 2
    assert sleep.calls == [1.0]


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


def test_stream_events_ends_once_the_source_reports_itself_closed():
    source = FakeSource()
    sleep = RecordingSleep()
    clock = FakeClock(itertools.count(0.0, 0.1))

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)
    next(gen)

    source.closed = True

    with pytest.raises(StopIteration):
        next(gen)


def test_stream_events_ends_once_the_source_reports_a_worker_error():
    source = FakeSource()
    sleep = RecordingSleep()
    clock = FakeClock(itertools.count(0.0, 0.1))

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)
    next(gen)

    source.error = RuntimeError("boom")

    with pytest.raises(StopIteration):
        next(gen)


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

    return [_payload(chunk.decode()) for chunk in chunks]


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

    source.sim_time = 1.0
    first = client.get("/api/stream")
    try:
        assert _read_events(first, 1)[0]["sim_time"] == pytest.approx(1.0)
    finally:
        first.close()

    source.sim_time = 42.0
    second = client.get("/api/stream")
    try:
        assert _read_events(second, 1)[0]["sim_time"] == pytest.approx(42.0)
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


# ---- a client that never reads at all: the real transport backstop ----


def _wait_until_closed(sock, timeout_seconds):
    """Poll `sock` for the server having closed its end, without ever
    sending anything back ourselves - a real client that stops reading,
    not one that hangs up. Drains whatever was already buffered and
    returns as soon as either EOF or a reset is seen, or False once
    `timeout_seconds` passes with the connection still apparently open."""
    deadline = time.monotonic() + timeout_seconds
    sock.settimeout(0.2)

    while time.monotonic() < deadline:
        try:
            data = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return True

        if data == b"":
            return True

    return False


def test_a_client_that_never_reads_is_dropped_by_the_transport_backstop():
    # stream_events's own dropout check runs only once a write returns
    # control to it, so it cannot see a write that blocks forever - this
    # drives the real Werkzeug development server over a real socket and
    # never reads from it, to prove create_stream_blueprint's socket-timeout
    # backstop (not stream_events itself) is what bounds that case.
    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: PaddedFakeSource(), 0.001))

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.socket.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        try:
            client.sendall(
                b"GET /api/stream HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
            )

            # Give the server time to actually fill the socket's real send
            # buffer and hit the transport-level timeout - reading anything
            # before then would drain the buffer ourselves and mask the
            # very scenario under test (a client that never reads at all).
            time.sleep(3.0)

            assert _wait_until_closed(client, timeout_seconds=10.0), (
                "a client that never reads was never dropped by the server"
            )
        finally:
            client.close()
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
