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

`client_key` names the bucket (T18-8). X-Forwarded-For is client-supplied, so
it is read only when the peer itself is a configured trusted proxy, and then
only right to left past trusted hops: everything left of the first untrusted
entry was written by whoever that client is. With no trusted proxy the key is
the peer address, as before. An allow-list of addresses rather than werkzeug's
ProxyFix hop count, because a hop count trusts the header from any peer, and
the Gunicorn port may be reachable without the proxy.
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections.abc import Callable, Collection, Iterable

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


Address = ipaddress.IPv4Address | ipaddress.IPv6Address
TrustedProxies = tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


def parse_trusted_proxies(entries: Iterable[str]) -> TrustedProxies:
    """Each entry as a network; an address or CIDR, anything else raises ValueError."""
    return tuple(ipaddress.ip_network(entry.strip(), strict=False) for entry in entries)


def _address(text: str) -> Address | None:
    try:
        address = ipaddress.ip_address(text.strip())
    except ValueError:
        return None

    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped

    return address


def _is_trusted(address: Address, proxies: TrustedProxies) -> bool:
    return any(address in network for network in proxies)


def client_key(
    remote_addr: str | None,
    forwarded_for: str | None,
    proxies: TrustedProxies,
) -> str:
    """The rate-limit key for a request: the nearest address no trusted proxy vouches for.

    An entry that is not an address stops the walk, and the key is the
    trusted hop that reported it - never a value the client could choose.
    """
    peer = remote_addr or "unknown"
    hop = _address(peer)

    if forwarded_for is None or hop is None or not _is_trusted(hop, proxies):
        return peer

    for entry in reversed(forwarded_for.split(",")):
        address = _address(entry)
        if address is None:
            break

        hop = address
        if not _is_trusted(hop, proxies):
            break

    return str(hop)


def install(
    app: Flask,
    limiter: Callable[[], RateLimiter],
    trusted_proxies: Iterable[str] = (),
) -> None:
    """Add the body cap, JSON API errors and rate limit to `app`.

    `limiter` is resolved on every request, so a caller can swap the live
    limiter without re-registering the hook. `trusted_proxies` are the peers
    whose X-Forwarded-For is believed; a bad entry raises here, at startup.
    """
    app.config["MAX_CONTENT_LENGTH"] = config.API_MAX_BODY_BYTES
    proxies = parse_trusted_proxies(trusted_proxies)

    @app.before_request
    def enforce_rate_limit() -> ResponseReturnValue | None:
        # A probe that is throttled cannot tell a busy server from a dead one.
        if request.endpoint == "static" or request.blueprint == "health":
            return None

        forwarded = request.headers.getlist("X-Forwarded-For")
        key = client_key(request.remote_addr, ",".join(forwarded) if forwarded else None, proxies)

        wait = limiter().acquire(key)
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
        # Allow on a 405, for one: the exception carries headers a client needs.
        response.headers.extend(
            (key, value)
            for key, value in error.get_headers()
            if key.lower() not in ("content-type", "content-length")
        )

        return response
