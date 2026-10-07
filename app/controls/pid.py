"""
PID block (T8-1) - standalone, no plant dependency.

Action (T8-6) follows the ISA convention: a *direct*-acting block's output
rises as its measurement rises, a *reverse*-acting one's falls. The two differ
only in the sign of the error - `sp - pv` for reverse, `pv - sp` for direct -
and every term below is written against that signed error, so anti-windup,
`track()` and derivative on measurement hold unchanged in both. Reverse is the
default because it is what `error = sp - pv` always computed: a heater or a
supply valve, where opening raises the measurement. A vent or a cooling valve,
where opening lowers it, needs direct.

Anti-windup is conditional integration: a step freezes the integral only when
the unclamped output is saturated *and* the current error would push it
further into that same saturation - never merely because the output happens
to be clamped this step. Freezing on clamped-ness alone would also block an
error that is already pulling the output back into range, so a large integral
built up before an unrelated change (a lowered output_max, a decayed
derivative) could latch the output at its limit forever with no way for the
error to unwind it. Checking the error's direction against the saturation
side is what lets a reversing error always drain the integral, however long
it takes, so saturation is escaped rather than latched.

Derivative acts on measurement, not on error. A setpoint step changes the
proportional and integral terms immediately but leaves measurement unchanged,
so it produces no derivative kick.

The saturation-direction check above infers which way the integral would push
the output from the sign of the error alone, which only holds for ki >= 0;
a negative ki would flip that inference and reopen the latch. That is why a
loop that needs its output to move the other way takes `Action.DIRECT`, which
flips the error's sign, rather than negative gains.

`track()` (T8-2) is the other side of the same equation compute() solves:
instead of deriving output from the integral, it derives the integral that
would have produced a given output, so that a later compute() call - same
setpoint, same measurement - reproduces it exactly with no step. That is what
"preloaded" state, called continuously, has to mean: a caller holding this
block on its own (an operator in manual, a cascade slave whose master is
off auto) tracks every step, not just once at the moment of transfer, so the
handoff is bumpless whenever it happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.statetypes import StateError


class Action(StrEnum):
    DIRECT = "direct"
    REVERSE = "reverse"


@dataclass(frozen=True)
class PIDCheckpoint:
    """Everything a `PID` needs to resume: its tuning, its setpoint and the
    two numbers `compute()` carries from one step to the next."""

    kp: float
    ki: float
    kd: float
    output_min: float
    output_max: float
    setpoint: float
    action: Action
    integral: float
    prev_measurement: float | None


class PID:
    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        output_min: float,
        output_max: float,
        setpoint: float = 0.0,
        action: Action = Action.REVERSE,
    ) -> None:
        if output_min > output_max:
            raise ValueError(
                f"output_min ({output_min}) must not exceed output_max ({output_max})"
            )
        if ki < 0.0:
            raise ValueError(f"ki must be non-negative, got {ki}")

        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.setpoint = setpoint
        self.action = action
        self._integral = 0.0
        self._prev_measurement: float | None = None

    def compute(self, measurement: float, dt: float) -> float:
        if dt <= 0.0:
            raise ValueError(f"dt must be positive, got {dt}")

        error = self._error(measurement)

        # Derivative on measurement is -d(error)/dt with the setpoint held
        # still, so it carries the error's sign: the same action flip.
        if self._prev_measurement is None:
            derivative = 0.0
        else:
            derivative = -self._sign * self.kd * (measurement - self._prev_measurement) / dt
        self._prev_measurement = measurement

        candidate_integral = self._integral + error * dt
        unclamped = self.kp * error + self.ki * candidate_integral + derivative
        output = min(max(unclamped, self.output_min), self.output_max)

        saturating_further = (unclamped > self.output_max and error > 0.0) or (
            unclamped < self.output_min and error < 0.0
        )
        if not saturating_further:
            self._integral = candidate_integral

        return output

    def track(self, measurement: float, dt: float, output: float) -> None:
        if dt <= 0.0:
            raise ValueError(f"dt must be positive, got {dt}")

        target = min(max(output, self.output_min), self.output_max)
        error = self._error(measurement)

        # track() always leaves _prev_measurement equal to this call's
        # measurement, so a follow-up call reusing it - the bumpless case
        # this exists for - is guaranteed derivative = 0. The preload has to
        # assume that same zero, not whatever derivative this call's own
        # (now-discarded) measurement history would have produced: baking in
        # a transient nonzero value here would size the integral for a
        # derivative term the next call can never actually see, producing a
        # bump equal to exactly that discarded term.
        self._prev_measurement = measurement

        # ki == 0 has no integral to preload through - a proportional-only
        # block cannot be made to land on an arbitrary target this way.
        #
        # compute() adds one more error * dt increment on top of _integral
        # before using it (candidate_integral), so the preload has to net
        # that increment out - otherwise the very next compute() call would
        # already have moved past the target by ki * error * dt.
        if self.ki != 0.0:
            self._integral = (target - self.kp * error) / self.ki - error * dt

    def retune(self, kp: float, ki: float, kd: float) -> None:
        """Change the gains without bumping the output (T16-14).

        The integral is re-solved so the proportional plus integral
        contribution at the last measurement is what it was, the same
        equation track() solves: a Kp or Ki change in AUTO moves the output
        only through what the next error does. With no measurement yet the
        integral term alone is held. A Kd change is not compensated - the
        derivative acts on the next measurement step, never on a stored one.

        Not bumpless with a new ki of 0: there is no integral to re-solve
        through, as in track(), so the old integral contribution and any Kp
        change at the held error step the output. Every gain must be
        non-negative; a loop that needs its output to move the other way
        takes Action.DIRECT (see the module docstring).
        """
        for name, gain in (("kp", kp), ("ki", ki), ("kd", kd)):
            if gain < 0.0:
                raise ValueError(f"{name} must be non-negative, got {gain}")

        held = self.ki * self._integral

        if self._prev_measurement is not None:
            held += (self.kp - kp) * self._error(self._prev_measurement)

        self.kp = kp
        self.kd = kd

        if ki != 0.0:
            self._integral = held / ki

        self.ki = ki

    def checkpoint(self) -> PIDCheckpoint:
        return PIDCheckpoint(
            kp=self.kp,
            ki=self.ki,
            kd=self.kd,
            output_min=self.output_min,
            output_max=self.output_max,
            setpoint=self.setpoint,
            action=self.action,
            integral=self._integral,
            prev_measurement=self._prev_measurement,
        )

    def validate_checkpoint(self, checkpoint: PIDCheckpoint) -> None:
        """Refuse a checkpoint the constructor would refuse, changing nothing."""
        if checkpoint.output_min > checkpoint.output_max:
            raise StateError(
                "",
                f"output_min {checkpoint.output_min!r} exceeds "
                f"output_max {checkpoint.output_max!r}",
            )

        if checkpoint.ki < 0.0:
            raise StateError("ki", f"{checkpoint.ki!r} is negative")

    def restore_checkpoint(self, checkpoint: PIDCheckpoint) -> None:
        self.validate_checkpoint(checkpoint)
        self.kp = checkpoint.kp
        self.ki = checkpoint.ki
        self.kd = checkpoint.kd
        self.output_min = checkpoint.output_min
        self.output_max = checkpoint.output_max
        self.setpoint = checkpoint.setpoint
        self.action = checkpoint.action
        self._integral = checkpoint.integral
        self._prev_measurement = checkpoint.prev_measurement

    @property
    def _sign(self) -> float:
        return 1.0 if self.action is Action.REVERSE else -1.0

    def _error(self, measurement: float) -> float:
        return self._sign * (self.setpoint - measurement)
