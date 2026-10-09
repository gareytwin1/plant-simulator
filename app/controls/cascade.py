"""
Cascade control (T8-5) - standalone, no plant dependency.

A `Cascade` pairs an *outer* loop with an *inner* loop: the outer loop's output
is the inner loop's setpoint. The standard use is level to flow - the level
loop decides how much flow the vessel needs, and the flow loop makes the valve
deliver it, so a disturbance on the valve (a supply-pressure change) is
corrected by the fast inner loop before it reaches the level.

The mechanism lives in `Loop` (T8-2): a loop in `Mode.CASCADE` takes its
setpoint from another loop's `output` and tracks its own output while that
other loop is off `AUTO`, so transfer is bumpless both ways. `Cascade` adds
the two things `Loop` does not know:

- **Scaling.** The outer loop's output is in its own PID range, the inner
  setpoint is in the inner measurement's engineering unit (GPM for a flow), and
  the two coincide only by accident. `Loop` copies one into the other, so
  `Cascade` hands the inner loop a scaled view of the outer loop - a stand-in
  `Loop` carrying the scaled output and the outer loop's mode - and leaves
  `Loop` and `PID` untouched.
- **Safe mode.** Breaking the cascade (`break_cascade()`) puts the inner loop in
  `Mode.MANUAL`, seeded from its last output: the valve holds where it is with
  no step, the loop keeps tracking, and it stands on its own, so discarding the
  `Cascade` object afterwards leaves a working loop rather than a `CASCADE`
  loop with no outer loop to ask (which raises). An outer loop merely off
  `AUTO` needs no call: the inner loop already holds its last output while the
  outer loop is MANUAL, and follows again when the outer loop returns.
  `engage()` resumes the cascade without a step.

Not wired into the plant: the loader, C3 and `Engine` cannot create a cascade,
and a `controllers.pv` names only a node, never a device field such as a
level. A `Cascade` in an `Engine` would also need T12-5's save/restore to
cover its loops.
"""

from __future__ import annotations

from app.controls.modes import Loop, Mode
from app.controls.pid import PID


class Cascade:
    def __init__(
        self,
        outer: Loop,
        inner: Loop,
        inner_range: tuple[float, float],
    ) -> None:
        low, high = inner_range
        if low > high:
            raise ValueError(f"inner_range low ({low}) must not exceed high ({high})")
        if outer.pid.output_max <= outer.pid.output_min:
            raise ValueError("outer loop's output range must have a positive span")

        self.outer = outer
        self.inner = inner
        self.inner_range = inner_range
        self._outer_view = Loop(PID(0.0, 0.0, 0.0, low, high))

    @property
    def engaged(self) -> bool:
        return self.inner.mode is Mode.CASCADE

    def engage(self) -> None:
        self.inner.mode = Mode.CASCADE

    def break_cascade(self) -> None:
        self.inner.mode = Mode.MANUAL

    def inner_setpoint(self, outer_output: float) -> float:
        low, high = self.inner_range
        pid = self.outer.pid
        fraction = (outer_output - pid.output_min) / (pid.output_max - pid.output_min)
        return low + fraction * (high - low)

    def compute(self, outer_measurement: float, inner_measurement: float, dt: float) -> float:
        self.outer.compute(outer_measurement, dt)

        self._outer_view.output = self.inner_setpoint(self.outer.output)
        self._outer_view.mode = self.outer.mode

        # Loop.compute names the outer loop "master"; the keyword is its
        # existing spelling, not this module's.
        return self.inner.compute(inner_measurement, dt, master=self._outer_view)
