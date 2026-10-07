"""
Ring-buffer historian (T17-1) - fixed-size per-tag history recorded at a
configurable rate. Pure data structure: no plant dependency, no wall clock.
A caller supplies simulated time itself as `timestamp`, the same discipline
`SimulationClock` and `SeededRNG` follow - "Time is owned, not observed"
(AGENTS.md) - so `Historian` never drifts from replayed simulated time.

Fixed memory footprint over an arbitrarily long run comes from two bounds
together: `capacity` caps samples per tag, oldest evicted first, and
`sample_period` throttles how often a call to `record()` actually appends
one. A caller stepping every simulated second for six hours does not grow a
tag's buffer past `capacity` regardless of run length - decimation happens on
write, so memory is bounded from the first sample, not just at steady state.

`record()` and `history()` share one lock per `Historian` so a concurrent
write from the engine's stepping thread and a read from a Flask request
thread (see `Scheduler`) never observe a half-mutated buffer; `history()`
takes a snapshot copy under the same lock rather than exposing the live
deque.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


# Simulated time is a float sum, so a step of exactly one period can arrive as
# 0.9999999999999999 of it; without slack that sample would be dropped.
_PERIOD_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Sample:
    timestamp: float
    value: float


class _TagBuffer:
    """Fixed-capacity, oldest-evicted-first history for one tag."""

    def __init__(self, capacity: int) -> None:
        self._samples: deque[Sample] = deque(maxlen=capacity)

    def append(self, timestamp: float, value: float) -> None:
        self._samples.append(Sample(timestamp, value))

    def samples(self) -> tuple[Sample, ...]:
        return tuple(self._samples)


class Historian:
    """Fixed-size per-tag history recorded at a configurable rate.

    `record()` is safe to call more often than `sample_period`; calls that
    land inside the period are dropped, not queued or averaged - a decimated
    trend is the point, and preserving a peak within a bucket is T17-2's job,
    not this one's. `sample_period=0.0` (the default) records every call.
    """

    def __init__(self, capacity: int, sample_period: float = 0.0) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")
        if sample_period < 0.0:
            raise ValueError(f"sample_period must be non-negative, got {sample_period}")

        self._capacity = capacity
        self._sample_period = sample_period
        self._lock = threading.Lock()
        self._buffers: dict[str, _TagBuffer] = {}
        self._last_seen: dict[str, float] = {}
        self._last_recorded: dict[str, float] = {}

    def record(self, tag: str, timestamp: float, value: float) -> None:
        """Record `value` for `tag` at `timestamp`, subject to
        `sample_period`. `timestamp` must never decrease for a given tag
        across *any* call, recorded or dropped by throttling - simulated
        time never runs backwards, and a call silently swallowed by
        `sample_period` must not hide a caller bug that would otherwise
        raise."""
        with self._lock:
            last_seen = self._last_seen.get(tag)
            if last_seen is not None and timestamp < last_seen:
                raise ValueError(
                    f"timestamp must be non-decreasing for tag {tag!r}, "
                    f"got {timestamp} after {last_seen}"
                )
            self._last_seen[tag] = timestamp

            last_recorded = self._last_recorded.get(tag)
            if last_recorded is not None and timestamp - last_recorded < self._sample_period - _PERIOD_TOLERANCE:
                return

            buffer = self._buffers.get(tag)
            if buffer is None:
                buffer = _TagBuffer(self._capacity)
                self._buffers[tag] = buffer

            buffer.append(timestamp, value)
            self._last_recorded[tag] = timestamp

    def history(self, tag: str) -> tuple[Sample, ...]:
        """`tag`'s retained samples, oldest first. Empty if `tag` has never
        been recorded."""
        with self._lock:
            buffer = self._buffers.get(tag)
            if buffer is None:
                return ()

            return buffer.samples()
