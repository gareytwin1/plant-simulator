"""
Structured logging (T18-4) - one JSON object per line, in two streams.

The engine stream is everything the simulation core says (loggers under
`app.engine` and `plant.engine`: a slow step, a failed step). The request
stream is everything else, chiefly one line per HTTP request from
`log_requests`. Each record names its stream, so a collector can split them
without parsing messages, and `configure` can send them to different
destinations.

Every record carries `sim_time`, the simulated clock reading, beside the wall
timestamp. Sim time is never read from a clock here: a `SimTimeFilter` asks a
provider the caller supplies (in `app.main`, the session's last published
Snapshot) and a caller may override it with `extra={"sim_time": ...}`. A
record logged where no provider can name a plant carries `sim_time: null`,
never a guess. The scheduler worker passes the plant's time itself on its
slow-step and failed-step records (T18-6).

This module is `app.logging` and the standard library is `logging`; files in
`app/` use absolute imports, so `import logging` there is still the stdlib.
Logging reads the plant and never changes a step, so determinism is untouched.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import IO, Any

from flask import Flask, Response, g, request


ENGINE_STREAM = "engine"
REQUEST_STREAM = "request"

ENGINE_LOGGER = "plant.engine"
REQUEST_LOGGER = "plant.request"

# Prefixes that make a record part of the engine stream.
_ENGINE_PREFIXES = ("app.engine", ENGINE_LOGGER)

SimTimeProvider = Callable[[], float | None]

# Marks the handlers `configure` installed, so a second call replaces them
# rather than doubling every line.
_INSTALLED = "_plant_structured_handler"

# LogRecord attributes that are not caller-supplied fields.
_STANDARD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "sim_time", "stream"}


def stream_of(logger_name: str) -> str:
    """The stream a logger's records belong to."""
    if logger_name.startswith(_ENGINE_PREFIXES):
        return ENGINE_STREAM

    return REQUEST_STREAM


class SimTimeFilter(logging.Filter):
    """Stamps `sim_time` on a record from `provider`, unless the caller
    already did. A provider that raises or has no answer yields None."""

    def __init__(self, provider: SimTimeProvider) -> None:
        super().__init__()
        self._provider = provider

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "sim_time"):
            try:
                record.sim_time = self._provider()
            except Exception:
                record.sim_time = None

        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "stream": stream_of(record.name),
            "logger": record.name,
            "message": record.getMessage(),
            "sim_time": getattr(record, "sim_time", None),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                entry[key] = value

        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


class _StreamFilter(logging.Filter):
    def __init__(self, stream: str) -> None:
        super().__init__()
        self._stream = stream

    def filter(self, record: logging.LogRecord) -> bool:
        return stream_of(record.name) == self._stream


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever `sys.stdout` is at emit time, so a handler installed
    at import survives a test runner swapping the stream."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self) -> IO[str]:
        return sys.stdout


def configure(
    sim_time: SimTimeProvider = lambda: None,
    engine_stream: IO[str] | None = None,
    request_stream: IO[str] | None = None,
    level: int = logging.INFO,
) -> None:
    """Send structured records from the `app` and `plant` logger trees to the
    two destinations (stdout when None). Idempotent: calling it again
    replaces what the last call installed. Propagation is left on, so a test's
    `caplog` still sees every record."""
    for root_name in ("app", "plant"):
        root = logging.getLogger(root_name)

        for handler in [h for h in root.handlers if getattr(h, _INSTALLED, False)]:
            root.removeHandler(handler)

        root.setLevel(level)

        for stream, destination in (
            (ENGINE_STREAM, engine_stream),
            (REQUEST_STREAM, request_stream),
        ):
            sink: logging.Handler = (
                logging.StreamHandler(destination)
                if destination is not None
                else _StdoutHandler()
            )
            sink.setFormatter(JsonFormatter())
            sink.addFilter(SimTimeFilter(sim_time))
            sink.addFilter(_StreamFilter(stream))
            setattr(sink, _INSTALLED, True)
            root.addHandler(sink)


def log_requests(app: Flask, sim_time: SimTimeProvider = lambda: None) -> None:
    """Log one request-stream line per response: method, path, status and
    duration, stamped with the plant's sim time as `sim_time` supplies it."""
    request_logger = logging.getLogger(REQUEST_LOGGER)

    @app.before_request
    def start_timer() -> None:
        g.request_started = time.monotonic()

    @app.after_request
    def log_response(response: Response) -> Response:
        started = g.get("request_started")
        duration_ms = None if started is None else (time.monotonic() - started) * 1000.0

        request_logger.info(
            "%s %s -> %d",
            request.method,
            request.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "sim_time": sim_time(),
            },
        )

        return response
