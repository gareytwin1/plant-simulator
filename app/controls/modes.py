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
from there. The equivalent entry seeding for `CASCADE` is not needed: its
tracking branch already targets `self.output`, so the first call after a
switch behaves identically whether or not a step just happened.
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

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        if value is Mode.MANUAL:
            self.manual_output = self.output
        self._mode = value

    def compute(self, measurement: float, dt: float, master: Loop | None = None) -> float:
        if self.mode is Mode.MANUAL:
            self.pid.track(measurement, dt, self.manual_output)
            self.output = self.manual_output
            return self.output

        if self.mode is Mode.CASCADE:
            if master is None:
                raise ValueError("Mode.CASCADE requires a master loop")

            self.pid.setpoint = master.output

            if master.mode is not Mode.AUTO:
                self.pid.track(measurement, dt, self.output)
                return self.output

        self.output = self.pid.compute(measurement, dt)
        return self.output
