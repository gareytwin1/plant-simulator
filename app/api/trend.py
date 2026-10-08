"""
Trend API (T17-3, contract C5's trend routes).

    GET /api/trend?tags=&from=&to=&max_points=   -> {point: [[t, v], ...]}
    GET /api/trend/points                        -> {"points": [point, ...],
        "limits": {point: {bound: v}}, "max_tags": n, "max_points": n}

A tag is a point, `<id>.<field>` (`app.historian.points`). `limits` carries the
configured envelope bounds of each point the engine evaluates, under the
`app.envelope.evaluator.Limits` field names and only those that are set, so a
display can draw bands without duplicating a limit; `max_tags` and
`max_points` are the bounds of `/api/trend`. Like the alarm
blueprint, `create_trend_blueprint` takes the plant's points and history as
callables resolved once per request, so this module assumes nothing about
where they live, and neither route starts a scheduler.

`from` and `to` are optional simulated seconds, inclusive. A range wholly or
partly outside what the buffer still holds returns the samples inside it,
possibly none: that is not an error. What is bounded is the request itself -
`max_points` (an integer from 2 to `TREND_MAX_POINTS`) and the tag count
(`TREND_MAX_TAGS`) are refused with a 400 beyond their limits, never clamped.
Each point's samples are sliced to the range and then decimated to
`max_points`, which keeps every bucket's extremes (`app.historian.decimate`).
A reading that is not a finite number is written as null.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from app import config
from app.api import validate
from app.historian.buffer import Sample
from app.historian.decimate import decimate


def _seconds(name: str) -> float | None | validate.ErrorResponse:
    text = request.args.get(name)

    if text is None:
        return None

    try:
        value = float(text)
    except ValueError:
        return validate.error_response(f"{name} must be a number")

    if not math.isfinite(value):
        return validate.error_response(f"{name} must be finite")

    return value


def _max_points() -> int | validate.ErrorResponse:
    text = request.args.get("max_points")

    if text is None:
        return config.TREND_DEFAULT_POINTS

    try:
        value = int(text)
    except ValueError:
        return validate.error_response("max_points must be an integer")

    if not 2 <= value <= config.TREND_MAX_POINTS:
        return validate.error_response(f"max_points must be from 2 to {config.TREND_MAX_POINTS}")

    return value


def _within(samples: Sequence[Sample], start: float | None, end: float | None) -> Sequence[Sample]:
    first = 0 if start is None else bisect_left(samples, start, key=lambda sample: sample.timestamp)
    last = len(samples) if end is None else bisect_right(samples, end, key=lambda sample: sample.timestamp)

    return samples[first:last]


def _pair(sample: Sample) -> list[float | None]:
    return [sample.timestamp, sample.value if math.isfinite(sample.value) else None]


def create_trend_blueprint(
    get_points: Callable[[], Sequence[str]],
    get_history: Callable[[Sequence[str]], dict[str, tuple[Sample, ...]]],
    get_limits: Callable[[], dict[str, dict[str, float]]],
) -> Blueprint:
    """Build the `/api/trend*` blueprint against the points and history of
    whichever plant the caller's session machinery has resolved for this
    request. `get_history` raises `KeyError` for a point its plant does not
    publish."""
    blueprint = Blueprint("trend", __name__)

    @blueprint.get("/api/trend/points")
    def get_trend_points() -> ResponseReturnValue:
        return jsonify({
            "points": list(get_points()),
            "limits": get_limits(),
            "max_tags": config.TREND_MAX_TAGS,
            "max_points": config.TREND_MAX_POINTS,
        }), 200

    @blueprint.get("/api/trend")
    def get_trend() -> ResponseReturnValue:
        tags = list(dict.fromkeys(tag for tag in request.args.get("tags", "").split(",") if tag))

        if not tags:
            return validate.error_response("tags is required: comma-separated trend points")

        if len(tags) > config.TREND_MAX_TAGS:
            return validate.error_response(f"at most {config.TREND_MAX_TAGS} tags per request")

        start = _seconds("from")
        if isinstance(start, tuple):
            return start

        end = _seconds("to")
        if isinstance(end, tuple):
            return end

        if start is not None and end is not None and start > end:
            return validate.error_response("from must not be after to")

        max_points = _max_points()
        if isinstance(max_points, tuple):
            return max_points

        known = set(get_points())
        unknown = next((tag for tag in tags if tag not in known), None)
        if unknown is not None:
            return validate.error_response(f"unknown trend point: {unknown!r}")

        try:
            history = get_history(tags)
        except KeyError as error:
            # The plant changed between the two calls above.
            return validate.error_response(f"unknown trend point: {error.args[0]!r}")

        return jsonify({
            tag: [_pair(sample) for sample in decimate(_within(history[tag], start, end), max_points)]
            for tag in tags
        }), 200

    return blueprint
