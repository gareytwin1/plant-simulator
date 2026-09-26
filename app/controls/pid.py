"""
PID block (T8-1) - standalone, no plant dependency.

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
a negative ki would flip that inference and reopen the latch. Direct-acting
loops only, until a reverse-acting mode exists to flip the error's sign
instead.

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


class PID:
    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        output_min: float,
        output_max: float,
        setpoint: float = 0.0,
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
        self._integral = 0.0
        self._prev_measurement: float | None = None

    def compute(self, measurement: float, dt: float) -> float:
        if dt <= 0.0:
            raise ValueError(f"dt must be positive, got {dt}")

        error = self.setpoint - measurement

        if self._prev_measurement is None:
            derivative = 0.0
        else:
            derivative = -self.kd * (measurement - self._prev_measurement) / dt
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
        error = self.setpoint - measurement

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
