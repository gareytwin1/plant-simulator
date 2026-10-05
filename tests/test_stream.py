import itertools
import json
import socket
import threading
import time

import pytest
from flask import Flask, request
from werkzeug.serving import make_server

import app.api.stream as stream_module
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
    """A monotonic() the test drives explicitly: time moves only when
    something calls `advance()`, and every read in between returns exactly
    what it last was. `stream_events` calls `monotonic()` several times
    per cycle now (Cadence's own bookkeeping, plus the write-duration
    check), so a hand-counted list of return values is fragile; a clock
    the test steps by hand is not."""

    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


class RecordingSleep:
    """A sleep() paired with a FakeClock: recording what it was asked to
    wait for and advancing the shared clock by exactly that much, so real
    elapsed time (as the fake clock tells it) and "time spent sleeping"
    never disagree."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.advance(seconds)


def _clocked(start=0.0):
    """A matched (clock, sleep) pair for stream_events - the common case;
    a test that needs the clock alone still constructs FakeClock directly."""
    clock = FakeClock(start)
    return clock, RecordingSleep(clock)


def _payload(event):
    """The JSON off one SSE event's `data:` line, ignoring an optional
    leading `retry:` line (present only on a stream's first event)."""
    data_line = next(line for line in event.split("\n") if line.startswith("data: "))
    return json.loads(data_line[len("data: "):])


# ---- format_event ----


def test_format_event_is_one_sse_data_frame_carrying_c4_json():
    snapshot = build_snapshot(sim_time=3.0, speed=1.0, running=True, equipment={})

    event = format_event(snapshot)

    assert event.startswith("data: ")
    assert event.endswith("\n\n")
    assert _payload(event) == snapshot.as_dict()


def test_format_event_puts_a_retry_line_before_data_only_when_given_one():
    snapshot = build_snapshot(sim_time=0.0, speed=1.0, running=True, equipment={})

    assert format_event(snapshot, retry_ms=250) == f"retry: 250\ndata: {json.dumps(snapshot.as_dict())}\n\n"
    assert format_event(snapshot) == f"data: {json.dumps(snapshot.as_dict())}\n\n"


def test_format_event_maps_a_non_finite_reading_to_null_instead_of_invalid_json():
    # json.dumps's default allow_nan=True would write a bare NaN/Infinity
    # token - valid Python, not valid JSON, and rejected by a browser's
    # JSON.parse. C4 does not forbid a non-finite reading anywhere.
    snapshot = build_snapshot(
        sim_time=0.0,
        speed=1.0,
        running=True,
        equipment={"K-101": {"nan_reading": float("nan"), "inf_reading": float("inf")}},
    )

    event = format_event(snapshot)

    # json.loads would itself accept a bare NaN token (Python's decoder is
    # as permissive as its encoder), so parsing alone would not catch a
    # regression back to allow_nan=True - check the raw text has no such
    # token before trusting the parsed value.
    assert "NaN" not in event
    assert "Infinity" not in event

    reading = _payload(event)["equipment"]["K-101"]
    assert reading["nan_reading"] is None
    assert reading["inf_reading"] is None


def test_format_event_maps_a_non_finite_value_inside_a_tuple_to_null():
    # _finite_or_none's dict/list recursion would silently skip a tuple, so
    # this needs its own case, not just a dict of bare floats.
    snapshot = build_snapshot(
        sim_time=0.0,
        speed=1.0,
        running=True,
        equipment={"K-101": {"readings": (float("nan"), 1.0)}},
    )

    event = format_event(snapshot)
    data_line = next(line for line in event.split("\n") if line.startswith("data: "))

    def _reject_constant(token):
        raise AssertionError(f"non-finite JSON constant leaked through: {token}")

    # parse_constant makes a bare NaN/Infinity token raise; plain
    # json.loads would accept it.
    payload = json.loads(data_line[len("data: "):], parse_constant=_reject_constant)

    assert payload["equipment"]["K-101"]["readings"] == [None, 1.0]


# ---- _dropout_seconds ----


def test_dropout_seconds_floors_a_fast_interval_but_leaves_a_slow_one_alone():
    assert stream_module._dropout_seconds(0.01) == pytest.approx(stream_module.MIN_DROPOUT_SECONDS)
    assert stream_module._dropout_seconds(10.0) == pytest.approx(10.0 * stream_module.DROPOUT_INTERVALS)


# ---- stream_events: the pure core, no Flask involved ----


def test_stream_events_yields_the_first_event_immediately_then_paces_later_ones():
    source = FakeSource()
    clock, sleep = _clocked()

    events = list(itertools.islice(
        stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock),
        3,
    ))

    assert len(events) == 3
    # Cadence's deadline starts at "now", so the very first delay computes
    # to ~0 - a fresh connection is not kept blank for a whole interval
    # before its first event.
    assert sleep.calls[0] == pytest.approx(0.0)
    assert sleep.calls[1:] == [pytest.approx(1.0), pytest.approx(1.0)]


def test_stream_events_paces_against_a_deadline_grid_not_a_fixed_sleep_every_cycle():
    source = FakeSource()
    clock, sleep = _clocked()

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)

    next(gen)
    clock.advance(0.4)  # building and writing that event took 0.4s
    next(gen)

    # A plain sleep(interval_seconds) every cycle would wait a full 1.0s
    # here regardless of what the first cycle already spent; pacing off a
    # deadline grid waits only what is left of the interval.
    assert sleep.calls[1] == pytest.approx(0.6)


def test_stream_events_sets_a_retry_line_on_the_first_event_only():
    source = FakeSource()
    clock, sleep = _clocked()

    events = list(itertools.islice(
        stream_events(source, interval_seconds=2.0, sleep=sleep, monotonic=clock),
        3,
    ))

    assert events[0].startswith("retry: 2000\n")
    assert not events[1].startswith("retry:")
    assert not events[2].startswith("retry:")


def test_stream_events_floors_retry_well_above_a_fast_push_interval():
    # EventSource keeps whatever retry: value it was last given across
    # every later reconnect, including ones that hit an empty response -
    # tying it to a fast push interval would turn a source that goes from
    # healthy to merely erroring into a reconnect storm every interval.
    source = FakeSource()
    clock, sleep = _clocked()

    first = next(stream_events(source, interval_seconds=0.01, sleep=sleep, monotonic=clock))

    assert first.startswith(f"retry: {stream_module.MIN_RETRY_MS}\n")


def test_stream_events_reflects_the_current_snapshot_not_a_queued_backlog():
    source = FakeSource()
    clock, sleep = _clocked()

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)

    source.sim_time = 1.0
    first = _payload(next(gen))
    source.sim_time = 99.0
    second = _payload(next(gen))

    assert first["sim_time"] == pytest.approx(1.0)
    assert second["sim_time"] == pytest.approx(99.0)


def test_stream_events_continues_when_every_write_lands_within_budget():
    source = FakeSource()
    clock, sleep = _clocked()

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0)

    events = []
    for _ in range(4):
        events.append(next(gen))
        clock.advance(2.0)  # each write "takes" 2s, well inside the 5s budget

    assert len(events) == 4


def test_stream_events_ends_when_a_write_alone_exceeds_the_dropout_budget():
    source = FakeSource()
    clock, sleep = _clocked()

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0)

    next(gen)  # first write: instant, fine
    next(gen)  # second event delivered; its own write is the slow one
    clock.advance(9.0)  # ...9s against a 5s budget - wedged, not briefly slow

    with pytest.raises(StopIteration):
        next(gen)


def test_stream_events_floors_the_dropout_budget_for_a_fast_interval():
    # dropout_intervals * interval_seconds alone would give a 50ms budget
    # here - tight enough that ordinary WAN jitter, not a wedged client,
    # would end the stream. MIN_DROPOUT_SECONDS keeps that from happening,
    # without raising the budget so high that a write past the floor is
    # never caught either - checked on both sides of it below.
    source = FakeSource()
    clock, sleep = _clocked()

    gen = stream_events(source, interval_seconds=0.01, sleep=sleep, monotonic=clock, dropout_intervals=5.0)

    next(gen)
    clock.advance(0.3)  # a real jitter spike, far past the raw 50ms budget
    next(gen)  # still inside the 2s floor - must not have ended

    clock.advance(2.5)  # past the floor itself now - a genuinely wedged client
    with pytest.raises(StopIteration):
        next(gen)


def test_stream_events_dropout_ignores_time_spent_sleeping():
    source = FakeSource()
    clock, sleep = _clocked()

    # A 100s interval means a 100s sleep between events - the dropout
    # check must not mistake that wait for a slow write against its 500s
    # budget (5 * 100s): its own timestamp is taken only after sleep()
    # returns, right before the yield it is timing.
    events = list(itertools.islice(
        stream_events(source, interval_seconds=100.0, sleep=sleep, monotonic=clock, dropout_intervals=5.0),
        2,
    ))

    assert len(events) == 2


def test_stream_events_ends_once_the_source_reports_itself_closed():
    source = FakeSource()
    clock, sleep = _clocked()

    gen = stream_events(source, interval_seconds=1.0, sleep=sleep, monotonic=clock)
    next(gen)

    source.closed = True

    with pytest.raises(StopIteration):
        next(gen)


def test_stream_events_ends_once_the_source_reports_a_worker_error():
    source = FakeSource()
    clock, sleep = _clocked()

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


def test_an_open_stream_never_blocks_the_schedulers_own_worker():
    # The Flask test client has no real socket and so no real backpressure -
    # this does not exercise a slow *client*; see
    # test_a_client_that_never_reads_is_dropped_by_the_transport_backstop
    # for that. What this proves is narrower but still real: an open,
    # unread stream holds no lock the scheduler's worker thread needs, so
    # that thread keeps stepping regardless of whether this connection is
    # ever read again.
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

            time.sleep(0.2)

            after = scheduler.snapshot().sim_time
            assert after > before
        finally:
            response.close()
    finally:
        scheduler.close()


# ---- a client that never reads at all: the real transport backstop ----


def test_a_client_that_never_reads_is_dropped_by_the_transport_backstop(monkeypatch):
    # stream_events's own dropout check runs only once a write returns
    # control to it, so it cannot see a write that blocks forever - this
    # drives the real Werkzeug development server over a real socket and
    # never reads from it at all, to prove create_stream_blueprint's
    # socket-timeout backstop (not stream_events itself) is what bounds
    # that case. dropout_intervals is forced absurdly high on the real
    # stream_events so its own write-duration check cannot be what ends
    # this stream either - only the socket timeout may. Detection is via
    # an Event set in generate()'s own finally (wrapped around the real
    # stream_events here) rather than by racing a fixed sleep against
    # however long this host's real TCP buffers take to fill: a client
    # that drains anything to check for closure would itself relieve the
    # backpressure under test. The client's own receive buffer is shrunk
    # before connecting for the same reason a padded payload is used -
    # filling it in milliseconds keeps this test's runtime set by
    # MIN_DROPOUT_SECONDS, not by whatever tcp_rmem/tcp_wmem this host
    # happens to be tuned to.
    closed = threading.Event()
    real_stream_events = stream_module.stream_events

    def instrumented(*args, **kwargs):
        kwargs["dropout_intervals"] = 1_000_000.0

        try:
            yield from real_stream_events(*args, **kwargs)
        finally:
            closed.set()

    monkeypatch.setattr(stream_module, "stream_events", instrumented)

    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: PaddedFakeSource(), 0.001))

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.socket.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            client.settimeout(5.0)
            client.connect(("127.0.0.1", port))

            client.sendall(
                b"GET /api/stream HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
            )

            assert closed.wait(timeout=15.0), (
                "a client that never reads was never dropped by the server"
            )
        finally:
            client.close()
    finally:
        server.shutdown()
        thread.join(timeout=5.0)


# ---- liveness at connect: closed is permanent, a bare error is not ----


def test_dead_at_connect_gets_a_204_so_eventsource_stops_retrying():
    source = FakeSource()
    source.closed = True

    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: source, interval_seconds=0.01))
    client = app.test_client()

    response = client.get("/api/stream")

    assert response.status_code == 204


def test_a_transient_error_at_connect_ends_the_stream_without_a_204():
    # error alone (Scheduler.start() clears it on a restart) must never
    # tell EventSource to give up for good the way closed does - a 204
    # here would strand a client that connected during exactly the window
    # a restart was about to recover from.
    source = FakeSource()
    source.error = RuntimeError("boom")

    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: source, interval_seconds=0.01))
    client = app.test_client()

    response = client.get("/api/stream")
    try:
        assert response.status_code == 200
        assert list(response.response) == []
    finally:
        response.close()


# ---- the socket timeout: set lazily inside generate(), restored when it ends ----


class FakeSocket:
    """A raw-socket stand-in that records settimeout() calls and reports
    the timeout it had before generate() touched it - all generate() ever
    asks of one."""

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.calls = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.calls.append(value)
        self.timeout = value


def _first_event(app, **overrides):
    response = app.test_client().get("/api/stream", environ_overrides=overrides)
    try:
        next(iter(response.response))
    finally:
        response.close()


def test_the_bound_outlives_the_generator_and_is_lifted_only_on_response_close():
    # Both servers write the closing chunk after the generator is
    # exhausted and before they close the response; that write needs the
    # bound too.
    source = FakeSource()
    fake_socket = FakeSocket(timeout=30.0)

    response = _stream_app(source).test_client().get(
        "/api/stream", environ_overrides={"gunicorn.socket": fake_socket}
    )
    iterator = iter(response.response)
    next(iterator)
    source.closed = True
    assert list(iterator) == []

    assert fake_socket.timeout == stream_module._dropout_seconds(0.1)
    response.close()
    assert fake_socket.timeout == 30.0


def test_a_204_never_builds_the_hold():
    source = FakeSource()
    source.closed = True
    built = []

    response = _stream_app(source, hold=lambda: built.append(1)).test_client().get("/api/stream")

    assert response.status_code == 204 and built == []


def _stream_app(source=None, **kwargs):
    app = Flask(__name__)
    app.register_blueprint(
        create_stream_blueprint(lambda: source or FakeSource(), interval_seconds=0.1, **kwargs)
    )
    return app


@pytest.mark.parametrize("key", ["werkzeug.socket", "gunicorn.socket"])
def test_the_sockets_timeout_is_bounded_while_streaming_then_restored(key):
    # Gunicorn's gthread worker reuses a kept-alive connection, so the
    # bound must not outlive the stream.
    fake_socket = FakeSocket(timeout=30.0)

    _first_event(_stream_app(), **{key: fake_socket})

    assert fake_socket.calls == [stream_module._dropout_seconds(0.1), 30.0]
    assert fake_socket.timeout == 30.0


def test_a_blocking_socket_is_restored_to_blocking():
    fake_socket = FakeSocket(timeout=None)

    _first_event(_stream_app(), **{"gunicorn.socket": fake_socket})

    assert fake_socket.timeout is None


def test_restoring_the_timeout_on_an_already_closed_socket_is_swallowed():
    class ClosedSocket(FakeSocket):
        def settimeout(self, value):
            if self.calls:
                raise OSError("closed")
            super().settimeout(value)

    _first_event(_stream_app(), **{"gunicorn.socket": ClosedSocket()})


def test_werkzeug_socket_wins_when_both_keys_are_present():
    werkzeug_socket, gunicorn_socket = FakeSocket(), FakeSocket()

    _first_event(_stream_app(), **{"werkzeug.socket": werkzeug_socket, "gunicorn.socket": gunicorn_socket})

    assert werkzeug_socket.calls and not gunicorn_socket.calls


# ---- hold: a context the stream keeps for its whole life ----


class RecordingHold:
    def __init__(self):
        self.events = []

    def __enter__(self):
        self.events.append("enter")

    def __exit__(self, *exc):
        self.events.append("exit")


def test_hold_is_entered_when_the_body_starts_and_exited_when_it_ends():
    hold = RecordingHold()
    app = _stream_app(hold=lambda: hold)

    response = app.test_client().get("/api/stream")
    next(iter(response.response))
    assert hold.events == ["enter"]
    response.close()

    assert hold.events == ["enter", "exit"]


def test_hold_is_exited_when_the_stream_ends_on_its_own():
    source = FakeSource()
    hold = RecordingHold()
    app = _stream_app(source, hold=lambda: hold)

    response = app.test_client().get("/api/stream")
    iterator = iter(response.response)
    next(iterator)
    source.closed = True
    assert list(iterator) == []

    assert hold.events == ["enter", "exit"]


def test_hold_is_resolved_under_the_request_context():
    seen = []

    def hold():
        seen.append(request.path)
        return RecordingHold()

    _first_event(_stream_app(hold=hold))

    assert seen == ["/api/stream"]


def test_a_head_request_never_enters_hold():
    hold = RecordingHold()

    response = _stream_app(hold=lambda: hold).test_client().head("/api/stream")
    response.close()

    assert hold.events == []


def test_a_head_request_never_touches_the_sockets_timeout():
    # Flask auto-registers HEAD for a GET route, and Werkzeug never
    # iterates a HEAD response's body iterator at all - generate() must
    # therefore never run, so the socket this fake stands in for is left
    # exactly as it was found, not set once and left unrestored.
    source = FakeSource()
    fake_socket = FakeSocket()

    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: source, interval_seconds=0.1))
    client = app.test_client()

    response = client.head("/api/stream", environ_overrides={"werkzeug.socket": fake_socket})
    response.close()

    assert fake_socket.calls == []
