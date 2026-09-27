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

Nothing here ever takes the engine's `step_lock` across a socket write.
`Scheduler.snapshot()` (or `Engine.snapshot()`) returns the last published
Snapshot without stepping and without a lock once one has been published, so
a client stalled on a slow read stalls only the thread serving it - never
the scheduler's own worker, which keeps stepping regardless of who is
reading.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Protocol

from flask import Blueprint, Response

from app.engine.snapshot import Snapshot


class SnapshotSource(Protocol):
    """What the stream needs of an engine or scheduler: the last published
    Snapshot, without stepping. Structural, so this module depends on
    neither `Engine` nor `Scheduler` in particular - both already satisfy
    it."""

    def snapshot(self) -> Snapshot: ...


# A client whose own socket write alone eats this many push intervals is
# wedged, not merely behind a briefly slow network. Chosen well above
# ordinary jitter so an occasional slow tick never trips it.
DROPOUT_INTERVALS = 5.0


def format_event(snapshot: Snapshot) -> str:
    """One SSE event: a `data:` line carrying a Snapshot's C4 JSON, closed
    by the blank line the protocol requires between events."""
    return f"data: {json.dumps(snapshot.as_dict())}\n\n"


def stream_events(
    source: SnapshotSource,
    interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    dropout_intervals: float = DROPOUT_INTERVALS,
) -> Iterator[str]:
    """Yield one formatted SSE event per `interval_seconds`, forever, until
    the consumer is gone or too slow to keep up.

    Each cycle sleeps, then reads whichever Snapshot is current - never a
    queued one - so a client that fell behind is shown the plant's present
    state rather than its history. The `yield` below hands a chunk to
    whatever is driving this generator (Flask/Werkzeug in production, a
    test in a unit test) and does not resume until that caller comes back
    for the next one - in production, that is exactly as long as the
    client's socket write took to drain. When that gap alone - not the
    sleep before it - exceeds `dropout_intervals` worth of
    `interval_seconds`, the client is wedged rather than momentarily slow,
    and this generator returns, ending the stream from the server side
    instead of parking its thread on a dead consumer forever.
    """
    dropout_seconds = interval_seconds * dropout_intervals

    while True:
        sleep(interval_seconds)
        event = format_event(source.snapshot())

        before = monotonic()
        yield event
        write_seconds = monotonic() - before

        if write_seconds > dropout_seconds:
            return


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
        # the generator's closure - stream_events never touches flask.g or
        # flask.request, so it needs no stream_with_context.
        source = get_source()

        return Response(
            stream_events(source, interval_seconds),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return blueprint
