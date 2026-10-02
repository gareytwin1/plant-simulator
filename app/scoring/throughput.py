"""
Throughput shortfall tracker (T15-2) - production lost on one variable.

Same shape and contract as `ExcursionTracker` (T9-3): a pure accumulator fed
a value and an elapsed `dt` once per step by whoever owns the step loop, with
no wall clock and no plant access. It integrates only the shortfall below the
target, `max(0, target - value) * dt`, so running above target earns nothing
back: production lost is a loss, never a credit netted against one.

The unit is the variable's own unit times seconds (SCFM-seconds for a gas
flow), so two variables are reported separately and never summed. Which
variable is the throughput, and its target, is scenario configuration; this
class takes only the target.
"""

from __future__ import annotations


class ThroughputTracker:
    def __init__(self, target: float) -> None:
        self.target = target
        self._lost = 0.0

    @property
    def lost(self) -> float:
        return self._lost

    def update(self, value: float, dt: float) -> None:
        if dt < 0.0:
            raise ValueError(f"dt must be non-negative, got {dt}")

        self._lost += max(0.0, self.target - value) * dt

    def reset(self) -> None:
        self._lost = 0.0
