"""
Peak-preserving decimation (T17-2) - shrink a tag's history to a requested
size for a zoomed-out trend without hiding an excursion. Averaging or
stride-sampling would drop a one-sample pressure spike, which is exactly the
evidence an operator needs, so each bucket keeps its minimum and its maximum.

Both survivors are real samples, never synthesised values: a peak keeps the
timestamp it was recorded at, so a bucket boundary cannot shift it. Buckets
partition the input by index into near-equal runs, so the result is
deterministic and independent of the sampling period.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.historian.buffer import Sample


def _extremes(samples: Sequence[Sample], start: int, end: int) -> tuple[int, int]:
    """Indices of the first minimum and last maximum in `samples[start:end]`,
    ignoring NaN, which compares false against everything and would otherwise
    pin a pick to the bucket's first sample. A bucket with fewer than two
    distinct picks (all NaN, or one finite sample) falls back to its first
    and last sample so the pair stays distinct."""
    low = high = -1
    for i in range(start, end):
        value = samples[i].value
        if math.isnan(value):
            continue
        if low < 0 or value < samples[low].value:
            low = i
        if high < 0 or value >= samples[high].value:
            high = i

    if low < 0:
        return start, end - 1
    if low == high:
        return (low, end - 1) if low != end - 1 else (start, low)
    return low, high


def decimate(samples: Sequence[Sample], max_points: int) -> tuple[Sample, ...]:
    """At most `max_points` samples that keep every bucket's extremes.

    Input at or under `max_points` is returned unchanged. Otherwise it is
    split into `max_points // 2` buckets and each contributes its first
    minimum and last maximum, in timestamp order. Every bucket then holds at
    least two samples, so the two picks are always distinct (NaN values are
    skipped when choosing extremes) and the output is
    exactly `2 * (max_points // 2)` long: `max_points` when it is even,
    `max_points - 1` when odd.
    """
    if max_points < 2:
        raise ValueError(f"max_points must be at least 2, got {max_points}")

    count = len(samples)
    if count <= max_points:
        return tuple(samples)

    buckets = max_points // 2
    kept: list[Sample] = []
    for bucket in range(buckets):
        start = bucket * count // buckets
        end = (bucket + 1) * count // buckets

        low, high = _extremes(samples, start, end)
        kept.extend(samples[i] for i in sorted((low, high)))

    return tuple(kept)
