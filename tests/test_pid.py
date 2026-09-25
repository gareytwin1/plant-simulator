import pytest

from app.controls.pid import PID


class FakeFirstOrderProcess:
    def __init__(self, gain: float, time_constant: float, initial: float = 0.0) -> None:
        self.gain = gain
        self.time_constant = time_constant
        self.value = initial

    def step(self, u: float, dt: float) -> float:
        self.value += dt * (self.gain * u - self.value) / self.time_constant
        return self.value


def test_non_positive_dt_is_rejected():
    pid = PID(kp=1.0, ki=1.0, kd=1.0, output_min=-10.0, output_max=10.0)

    with pytest.raises(ValueError):
        pid.compute(0.0, 0.0)


def test_step_response_reaches_setpoint_with_no_sustained_offset():
    process = FakeFirstOrderProcess(gain=2.0, time_constant=5.0)
    pid = PID(kp=0.8, ki=0.4, kd=0.05, output_min=-100.0, output_max=100.0)
    pid.setpoint = 10.0

    dt = 0.1
    measurement = 0.0
    for _ in range(3000):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)

    assert measurement == pytest.approx(10.0, abs=0.01)


def test_saturate_then_release_produces_no_windup_overshoot():
    process = FakeFirstOrderProcess(gain=1.0, time_constant=2.0)
    pid = PID(kp=1.0, ki=2.0, kd=0.0, output_min=0.0, output_max=1.0)
    pid.setpoint = 1000.0

    dt = 0.1
    measurement = 0.0
    for _ in range(300):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)

    achievable = measurement + 0.05
    pid.setpoint = achievable
    overshoot = 0.0
    for _ in range(500):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)
        overshoot = max(overshoot, measurement - achievable)

    assert overshoot < 0.02


def test_setpoint_change_produces_no_derivative_kick():
    pid = PID(kp=2.0, ki=0.0, kd=5.0, output_min=-1000.0, output_max=1000.0)
    measurement = 10.0
    dt = 1.0

    pid.setpoint = 10.0
    output_before = pid.compute(measurement, dt)

    pid.setpoint = 50.0
    output_after = pid.compute(measurement, dt)

    assert output_after == pytest.approx(pid.kp * 40.0)


def test_deliberately_bad_tuning_oscillates_as_expected():
    process = FakeFirstOrderProcess(gain=1.0, time_constant=1.0)
    pid = PID(kp=5.0, ki=80.0, kd=0.0, output_min=-1e9, output_max=1e9)
    pid.setpoint = 1.0

    dt = 0.05
    measurement = 0.0
    errors = []
    for _ in range(400):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)
        errors.append(pid.setpoint - measurement)

    sign_changes = sum(1 for a, b in zip(errors, errors[1:]) if a * b < 0)
    assert sign_changes >= 5
