"""
Envelope limit loader (T9-2) - binds C3's `limits` key to T9-1's Evaluator.

`limits` is a passthrough section as far as `app/plant/loader.py` is
concerned (`PASSTHROUGH_SECTIONS`) - nothing there interprets it. This module
is the interpreter: one `Evaluator` per `(tag, variable)` pair, built from
the same config dict `app.plant.validate.validate()` already checked against
the C3 schema.

Each entry names up to four ISA-style bounds - `lo_lo`, `lo`, `hi`, `hi_hi` -
plus a `trip` flag and an optional `deadband`. `lo`/`hi` become T9-1's
`warning_lo`/`warning_hi`; `lo_lo`/`hi_hi` become `trip_lo`/`trip_hi` when
`trip` is true, or `alarm_lo`/`alarm_hi` otherwise. `trip` picks one severity
for both outer bounds on a single variable - a variable is either trip-rated
or it is not, not trip on one side and alarm on the other. Any bound left out
of an entry stays unreachable, exactly as an unset `Limits` field already
means in T9-1.

Ordering violations - `lo_lo` above `lo`, for instance - are caught by
`Limits.__post_init__` itself; this module only adds the offending tag and
variable to the message, since a plant config can define many entries and a
bare "X must not exceed Y" does not say which one failed.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

from app.envelope.evaluator import Evaluator, Limits

LimitKey = tuple[str, str]


def load_limits(config: Mapping[str, Any]) -> dict[LimitKey, Evaluator]:
    """Build one `Evaluator` per `(tag, variable)` named in `config["limits"]`.

    `config` is decoded JSON or YAML of the shape `validate()` checks - a
    reason for `Any` per .claude/rules/python.md. A config with no `limits`
    key at all yields an empty mapping: not every plant defines envelopes.
    """
    evaluators: dict[LimitKey, Evaluator] = {}

    for entry in config.get("limits", []):
        tag = entry["tag"]
        variable = entry["variable"]
        trip = entry.get("trip", False)
        lo_lo = entry.get("lo_lo")
        hi_hi = entry.get("hi_hi")

        try:
            limits = Limits(
                trip_lo=lo_lo if trip else None,
                alarm_lo=None if trip else lo_lo,
                warning_lo=entry.get("lo"),
                warning_hi=entry.get("hi"),
                alarm_hi=None if trip else hi_hi,
                trip_hi=hi_hi if trip else None,
            )
        except ValueError as exc:
            raise ValueError(f"limits entry {tag}.{variable}: {exc}") from exc

        evaluators[(tag, variable)] = Evaluator(limits, deadband=entry.get("deadband", 0.0))

    return evaluators


def get_limit(
    limits: Mapping[LimitKey, Evaluator],
    tag: str,
    variable: str,
) -> Evaluator | None:
    """Look up one tag's `Evaluator`, warning rather than crashing if absent.

    A plant config only defines envelopes for the variables someone has
    tuned; a caller asking about a tag nobody configured limits for is a gap
    worth surfacing to whoever is watching warnings, not a `KeyError` worth
    taking the request down over.
    """
    key = (tag, variable)

    if key not in limits:
        warnings.warn(
            f"no envelope limits configured for {tag}.{variable}",
            stacklevel=2,
        )
        return None

    return limits[key]
