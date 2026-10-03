"""
API input validation and rate limiting (T18-3).

Every handler reads its JSON body through this module so a bad request is a
4xx with `{"error": "<message>"}` and never a 500. The shape and the wording
are the ones `app/main.py` already used, so a valid request behaves exactly
as before and the messages existing clients see do not change.

`read_object` rejects a body that is not a JSON object and any key the
endpoint did not name; the field readers then check one value each. Each
returns the value or an `ErrorResponse` - a tuple, which no value here is -
so a handler stays a flat `if isinstance(x, tuple): return x`.

`install` wires the app-wide guards: a body size cap (413), a JSON error
body for any HTTP error under `/api/`, and a per-client token bucket (429).
Call it before registering any other `before_request`, so a flood is refused
before it can create a session.

`RateLimiter` takes its clock as an argument - a monotonic source, never
`time.time()` - so a test drives it with a fake one. The limiter state is
per process, which matches the one-worker deployment `SessionRegistry`
already assumes.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Collection

from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from app import config


ErrorResponse = tuple[Response, int]


def error_response(message: str, status: int = 400) -> ErrorResponse:
    return jsonify({"error": message}), status


def read_object(allowed: Collection[str]) -> dict[str, object] | ErrorResponse:
    """The request's JSON body as an object holding only `allowed` keys."""
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return error_response("request body must be a JSON object")

    unknown = sorted(set(body) - set(allowed))
    if unknown:
        return error_response(f"unknown field: {', '.join(unknown)}")

    return body


def number_problem(value: object, name: str) -> str | None:
    """Why `value` is not a usable number, or None if it is.

    The range test runs on the int itself: converting a huge int literal to a
    float, which `math.isfinite` does, raises OverflowError.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{name} must be a number"

    if isinstance(value, float) and not math.isfinite(value):
        return f"{name} must be finite"

    if abs(value) > config.API_MAX_MAGNITUDE:
        return f"{name} must be within +/-{config.API_MAX_MAGNITUDE:g}"

    return None


def check_number(value: object, name: str) -> float | ErrorResponse:
    """`value` as a float, or a 400 saying why it is not usable."""
    problem = number_problem(value, name)
    if problem is not None:
        return error_response(problem)

    assert isinstance(value, (int, float))

    return float(value)


def number_field(body: dict[str, object], field: str) -> float | ErrorResponse:
    if field not in body:
        return error_response(f"missing field: {field}")

    return check_number(body[field], field)


def string_field(
    body: dict[str, object],
    field: str,
    message: str | None = None,
) -> str | ErrorResponse:
    """A required string; `message` overrides the default wording."""
    value = body.get(field)

    if not isinstance(value, str):
        return error_response(message or f"{field} must be a string")

    return value


class RateLimiter:
    """A token bucket per client: `burst` requests at once, `rate` per second.

    Memory is bounded by `max_clients`: past it, the least recently seen
    client is forgotten and, if it returns, starts with a full burst. That
    only matters with more than `max_clients` clients active at once.
    """

    def __init__(
        self,
        rate: float,
        burst: float,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 1024,
    ) -> None:
        if rate <= 0 or burst < 1 or max_clients < 1:
            raise ValueError("rate must be positive, burst at least 1, max_clients at least 1")

        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._max_clients = max_clients
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> float:
        """Take a token for `key`: 0.0 if granted, else seconds until one is."""
        with self._lock:
            now = self._clock()
            tokens, stamp = self._buckets.pop(key, (self._burst, now))
            tokens = min(self._burst, tokens + max(0.0, now - stamp) * self._rate)

            if tokens >= 1.0:
                tokens -= 1.0
                wait = 0.0
            else:
                wait = (1.0 - tokens) / self._rate

            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self._max_clients:
                del self._buckets[next(iter(self._buckets))]

            return wait


def install(app: Flask, limiter: Callable[[], RateLimiter]) -> None:
    """Add the body cap, JSON API errors and rate limit to `app`.

    `limiter` is resolved on every request, so a caller can swap the live
    limiter without re-registering the hook.
    """
    app.config["MAX_CONTENT_LENGTH"] = config.API_MAX_BODY_BYTES

    @app.before_request
    def enforce_rate_limit() -> ResponseReturnValue | None:
        if request.endpoint == "static":
            return None

        wait = limiter().acquire(request.remote_addr or "unknown")
        if wait == 0.0:
            return None

        retry_after = max(1, math.ceil(wait))
        response = jsonify({"error": f"rate limit exceeded, retry in {retry_after} s"})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)

        return response

    @app.errorhandler(HTTPException)
    def api_error(error: HTTPException) -> ResponseReturnValue:
        if not request.path.startswith("/api/"):
            return error

        response = jsonify({"error": error.description or error.name})
        response.status_code = error.code or 500

        return response
