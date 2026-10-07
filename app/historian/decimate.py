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

from collections.abc import Sequence

from app.historian.buffer import Sample


def decimate(samples: Sequence[Sample], max_points: int) -> tuple[Sample, ...]:
    """At most `max_points` samples that keep every bucket's extremes.

    Input at or under `max_points` is returned unchanged. Otherwise it is
    split into `max_points // 2` buckets and each contributes its first
    minimum and last maximum, in timestamp order. Every bucket then holds at
    least two samples, so the two picks are always distinct and the output is
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

        low = start
        high = start
        for i in range(start + 1, end):
            if samples[i].value < samples[low].value:
                low = i
            if samples[i].value >= samples[high].value:
                high = i

        kept.extend(samples[i] for i in sorted((low, high)))

    return tuple(kept)
