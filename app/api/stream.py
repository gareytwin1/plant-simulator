"""
Snapshot push transport (T16-2) - server-sent events replacing polling, so a
browser reflects the plant without a request loop.

    GET /api/stream

One SSE connection per client. Each event is the plant's current Snapshot
(C4) as JSON - the full state, never a delta - so a reconnect after a drop
needs no replay: the very next event is a complete picture again.

Backpressure is conflated, not queued: every tick sends whatever Snapshot is
current, so a client that falls behind is shown the plant's present state,
not a backlog of the steps it missed. `create_stream_blueprint` takes its
snapshot source as a callable for the same reason `app.api.action` takes its
`Engine`: no session shape that carries a config-loaded, multi-device plant
exists on `main.py` yet, so this module makes no assumption about where its
`SnapshotSource` comes from.

Only a source that publishes a Snapshot independently of live stepping -
`Scheduler.snapshot()`, which returns the last snapshot a `step_lock`-held
step already finished building - is safe to read from this module's own
thread while something else steps the plant concurrently. A bare `Engine`
satisfies the same `.snapshot()` shape structurally, but `Engine.snapshot()`
walks live devices on every call with no lock of its own; passing one still
being stepped by a `Scheduler` elsewhere is the caller's bug, not this
module's, in exactly the way `Scheduler`'s own docstring already warns
`step_lock` callers about.

Nothing here holds a lock across the socket write. Two backstops keep a
stuck reader from parking this module's thread forever, since a `yield`
that never regains control cannot run any check written after it:

- A dropout check inside `stream_events` measures how long each `yield`
  took to return control - in production, exactly how long the client's
  socket write took to drain - and ends the stream once that alone
  exceeds `dropout_intervals` worth of `interval_seconds`. This catches a
  write that is slow but still completes.
- `create_stream_blueprint` additionally sets a send timeout on the raw
  socket, when the WSGI server hands one through (Werkzeug's development
  server does, under `environ["werkzeug.socket"]`). A write that never
  completes at all - a client that stops draining its socket entirely -
  cannot return control to `stream_events` for its own check to run; the
  socket timeout is what bounds that case instead, by making the blocked
  write itself raise. A WSGI server that does not expose its socket this
  way has no backstop against that specific case here; closing that gap
  for such a server is deployment's job (see T18-1/T18-2), not this
  module's.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol

from flask import Blueprint, Response, request

from app.engine.scheduler import Cadence
from app.engine.snapshot import Snapshot


class SnapshotSource(Protocol):
    """What the stream needs of an engine or scheduler: the last published
    Snapshot, without stepping. Structural, so this module depends on
    neither `Engine` nor `Scheduler` in particular - see the module
    docstring for which of them is actually safe to pass while something
    else steps it concurrently.

    `closed` and `error` are optional (`_is_closed`/`_is_dead` read them
    defensively): `Scheduler` carries both, so a stream built on one stops
    pushing once the scheduler is closed or its worker has failed, rather
    than repeating the same stale snapshot forever. The two are not the
    same thing - `error` alone can clear on a restart (`Scheduler.start()`
    does exactly that), `closed` never does - and only `_is_closed` may
    ever be used to decide whether a *reconnect* is worth trying again."""

    def snapshot(self) -> Snapshot: ...


# A client whose own socket write alone eats this many push intervals is
# wedged, not merely behind a briefly slow network. Chosen well above
# ordinary jitter so an occasional slow tick never trips it.
DROPOUT_INTERVALS = 5.0


def format_event(snapshot: Snapshot, retry_ms: int | None = None) -> str:
    """One SSE event: a `data:` line carrying a Snapshot's C4 JSON, closed
    by the blank line the protocol requires between events. `retry_ms`,
    given only on the first event of a connection, sets EventSource's
    reconnect delay explicitly rather than leaving a dropped stream to its
    browser-default retry.

    A `nan` or infinite reading is not disallowed anywhere in C4, and
    `json.dumps` would otherwise write it as a bare `NaN`/`Infinity` token
    - valid Python, not valid JSON, and rejected outright by a browser's
    `JSON.parse`. Serializing with `allow_nan=False` catches that case and
    falls back to sending the same snapshot with every non-finite reading
    mapped to `null`, rather than silently shipping a payload the client
    cannot parse."""
    retry_line = f"retry: {retry_ms}\n" if retry_ms is not None else ""
    payload = snapshot.as_dict()

    try:
        body = json.dumps(payload, allow_nan=False)
    except ValueError:
        body = json.dumps(_finite_or_none(payload))

    return f"{retry_line}data: {body}\n\n"


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite_or_none(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_or_none(item) for item in value]
    return value


def _is_closed(source: SnapshotSource) -> bool:
    """True only once `source` will never advance again -
    `Scheduler.close()` is permanent, unlike `error`, which
    `Scheduler.start()` clears on a restart. This is the one liveness
    check allowed to tell a client to stop reconnecting for good; see
    `_is_dead` for the broader "not advancing right now" check that a
    restart can still recover from."""
    return bool(getattr(source, "closed", False))


def _is_dead(source: SnapshotSource) -> bool:
    """True once `source` has stopped advancing for now - closed, or its
    worker failed - rather than merely being paused or between steps.
    `snapshot()` alone cannot tell any of this apart: it keeps returning
    its last value regardless. This ends an open stream in either case,
    but must never by itself decide whether a *reconnect* should keep
    trying - see `_is_closed` for that."""
    return _is_closed(source) or getattr(source, "error", None) is not None


def stream_events(
    source: SnapshotSource,
    interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    dropout_intervals: float = DROPOUT_INTERVALS,
) -> Iterator[str]:
    """Yield one formatted SSE event per `interval_seconds`, starting
    immediately on connect, until the source is dead or the consumer is
    too slow to keep up.

    Paced against a deadline grid with `Cadence` - the same primitive
    `Scheduler` uses for the same reason - rather than a plain
    `sleep(interval_seconds)` every cycle: the latter drifts steadily
    behind the configured rate by however long building and writing each
    event takes, cycle after cycle, while a deadline grid does not. On
    connect, `Cadence`'s deadline starts at "now", so the first delay
    computes to (approximately) zero and the first event goes out at once.

    Each cycle reads whichever Snapshot is current - never a queued one -
    so a client that fell behind is shown the plant's present state rather
    than its history. The `yield` below hands a chunk to whatever is
    driving this generator (Flask/Werkzeug in production, a test in a unit
    test) and does not resume until that caller comes back for the next
    one - in production, that is exactly as long as the client's socket
    write took to drain. When that gap alone - not the wait before it -
    exceeds `dropout_intervals` worth of `interval_seconds`, the client is
    wedged rather than momentarily slow, and this generator returns,
    ending the stream from the server side. A write that never returns
    control at all cannot be caught here; see the module docstring for the
    transport-level backstop that covers it.
    """
    dropout_seconds = interval_seconds * dropout_intervals
    retry_ms = max(1, round(interval_seconds * 1000))
    cadence = Cadence(interval_seconds, monotonic())
    first = True

    while True:
        sleep(cadence.delay(monotonic()))

        if _is_dead(source):
            return

        started = monotonic()
        event = format_event(source.snapshot(), retry_ms=retry_ms if first else None)
        first = False

        before = monotonic()
        yield event
        write_seconds = monotonic() - before

        cadence.complete(started, monotonic())

        if write_seconds > dropout_seconds:
            return


def _socket_of(environ: Mapping[str, Any]) -> Any:
    """The raw socket behind this request, if the WSGI server hands one
    through - Werkzeug's development server does, under this environ key.
    `environ` is a WSGI environ - a mapping of a shape nothing here types -
    so both the key lookup and the return are `Any`; a server that does not
    expose this key yields `None`, which every caller here treats as "no
    transport-level backstop available", not an error.
    """
    return environ.get("werkzeug.socket")


def create_stream_blueprint(
    get_source: Callable[[], SnapshotSource],
    interval_seconds: float,
) -> Blueprint:
    """Build the `/api/stream` blueprint against a `SnapshotSource` resolved
    on demand - once per request, so each call to `get_source` reaches
    whichever plant the caller's own session machinery has already resolved
    for this request, the same discipline `create_action_blueprint` follows
    for `Engine`.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be positive, got {interval_seconds}")

    blueprint = Blueprint("stream", __name__)

    @blueprint.get("/api/stream")
    def get_stream() -> Response:
        # Resolved here, under the real request context, and captured by
        # generate()'s closure - stream_events never touches flask.g or
        # flask.request itself, so it needs no stream_with_context. Only a
        # *permanently* dead source gets a 204 here: EventSource treats
        # that as "stop reconnecting for good", which would strand a
        # client that happened to connect during a transient `error` -
        # `stream_events` still ends that stream (via the broader
        # `_is_dead`), but leaves the client free to retry once whatever
        # restarted the source clears it. See _is_closed vs. _is_dead.
        source = get_source()

        if _is_closed(source):
            return Response(status=204)

        # Only the lookup needs the real request context; set and restored
        # together inside generate(), so a response that is never iterated
        # (HEAD, for one) never touches the socket.
        sock = _socket_of(request.environ)

        def generate() -> Iterator[str]:
            previous_timeout = sock.gettimeout() if sock is not None else None

            if sock is not None:
                # Best-effort transport-level backstop for a write that
                # never completes at all: `stream_events`'s own dropout
                # check only runs once a write returns control to it, so a
                # client whose socket is simply never drained parks this
                # thread on that write forever otherwise; nothing in a
                # generator can interrupt a blocking call it does not
                # itself make. A socket timeout bounds *any* blocking
                # operation on it, including the write this module never
                # sees.
                sock.settimeout(interval_seconds * DROPOUT_INTERVALS)

            try:
                yield from stream_events(source, interval_seconds)
            finally:
                # This connection may be kept alive past this stream
                # (HTTP/1.1 keep-alive) and reused for something else
                # entirely - restore what was there before so a later
                # request or idle read on the same socket does not inherit
                # a push-rate-scaled timeout that has nothing to do with it.
                if sock is not None:
                    sock.settimeout(previous_timeout)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return blueprint
