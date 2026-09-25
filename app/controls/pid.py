"""
PID block (T8-1) — standalone, no plant dependency.

Anti-windup is conditional integration: a step accumulates into the integral
only if doing so keeps the unclamped output inside [output_min, output_max].
A step that would saturate the output freezes the integral instead of growing
it further, so release from saturation produces no overshoot from a wound-up
term.

Derivative acts on measurement, not on error. A setpoint step changes the
proportional and integral terms immediately but leaves measurement unchanged,
so it produces no derivative kick.
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

        if output == unclamped:
            self._integral = candidate_integral

        return output
