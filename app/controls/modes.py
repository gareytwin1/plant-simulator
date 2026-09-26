"""
Control modes and bumpless transfer (T8-2) - standalone, no plant dependency.

Operators switch modes constantly during upsets, and a bump on every transfer
would make manual intervention worse than doing nothing. A `Loop` wraps a
`PID` (T8-1) with three modes:

- `MANUAL`: the operator's `manual_output` is the loop's output directly. The
  block's own state tracks it every step via `PID.track()`, so whenever the
  loop later returns to `AUTO` the preloaded integral reproduces that same
  output with no step.
- `AUTO`: the wrapped `PID` controls to its own `setpoint` normally.
- `CASCADE`: the setpoint tracks a master `Loop`'s `output` every step,
  whether or not that master is in `AUTO` - so the value it would drive to
  never jumps at the moment the master resumes. While the master is off
  `AUTO`, the slave doesn't act on that setpoint; it falls back to tracking
  its own last output instead, the same mechanism as `MANUAL` but aimed at
  itself rather than at an operator's command, and is ready to resume the
  instant the master returns to `AUTO` because both the setpoint and the
  integral it would imply were kept current throughout.

Switching *into* `MANUAL` seeds `manual_output` from the loop's last output,
so the transfer is bumpless by default; the operator moves it deliberately
from there. `CASCADE` needs an equivalent seed for one specific case: if the
master already happens to be in `AUTO` at the moment the switch is made, the
new setpoint has never been tracked against, so the first `compute()` call
would jump straight to it against a stale integral. `mode`'s setter can't do
this seeding itself - it has no `measurement`/`dt` to call `PID.track()`
with - so it only flags the transition, and `compute()` treats that first
call after any switch into `CASCADE` as one more tracking step before
control resumes, exactly as if the master were still off `AUTO`.
"""

from __future__ import annotations

from enum import StrEnum

from app.controls.pid import PID


class Mode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"
    CASCADE = "cascade"


class Loop:
    def __init__(self, pid: PID, mode: Mode = Mode.MANUAL) -> None:
        self.pid = pid
        self.output = min(max(0.0, pid.output_min), pid.output_max)
        self.manual_output = self.output
        self._mode = mode
        self._entering = False

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        if value is Mode.MANUAL:
            self.manual_output = self.output
        self._entering = value is not self._mode
        self._mode = value

    def compute(self, measurement: float, dt: float, master: Loop | None = None) -> float:
        entering, self._entering = self._entering, False

        if self.mode is Mode.MANUAL:
            self.pid.track(measurement, dt, self.manual_output)
            self.output = self.manual_output
            return self.output

        if self.mode is Mode.CASCADE:
            if master is None:
                raise ValueError("Mode.CASCADE requires a master loop")

            self.pid.setpoint = master.output

            if entering or master.mode is not Mode.AUTO:
                self.pid.track(measurement, dt, self.output)
                return self.output

        self.output = self.pid.compute(measurement, dt)
        return self.output
