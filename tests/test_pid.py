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


def test_output_min_above_output_max_is_rejected():
    with pytest.raises(ValueError):
        PID(kp=1.0, ki=1.0, kd=1.0, output_min=10.0, output_max=-10.0)


def test_negative_ki_is_rejected():
    with pytest.raises(ValueError):
        PID(kp=1.0, ki=-1.0, kd=1.0, output_min=-10.0, output_max=10.0)


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
    # A setpoint of 1000 is unreachable (output tops out at 1.0 into this
    # process), so this saturates the output for the whole build-up phase and
    # would let the integral wind up unboundedly without anti-windup.
    process = FakeFirstOrderProcess(gain=1.0, time_constant=2.0)
    pid = PID(kp=1.0, ki=2.0, kd=0.0, output_min=0.0, output_max=1.0)
    pid.setpoint = 1000.0

    dt = 0.1
    measurement = 0.0
    for _ in range(300):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)

    # Release to a setpoint the process can actually reach. A wound-up
    # integral would keep the output pinned at output_max regardless of this
    # new, achievable target (see test_a_lowered_output_max_does_not_latch_
    # the_integral_forever for the pinned-forever case in isolation).
    achievable = 0.5
    pid.setpoint = achievable
    output = pid.compute(measurement, dt)
    assert output < pid.output_max

    for _ in range(500):
        measurement = process.step(output, dt)
        output = pid.compute(measurement, dt)

    assert measurement == pytest.approx(achievable, abs=0.01)


def test_a_lowered_output_max_does_not_latch_the_integral_forever():
    # Isolates the anti-windup mechanism from process dynamics: build up a
    # large integral while comfortably inside the output range, then shrink
    # output_max out from under it and reverse the error. Freezing the
    # integral just because the output happens to be clamped (rather than
    # because the error is pushing further into that clamp) would pin the
    # output at output_max forever, since nothing would ever let the integral
    # drain back down.
    pid = PID(kp=0.0, ki=1.0, kd=0.0, output_min=-1000.0, output_max=1000.0)
    dt = 1.0

    for _ in range(10):
        pid.compute(-10.0, dt)

    pid.output_max = 5.0
    output = 5.0
    for _ in range(5):
        output = pid.compute(50.0, dt)

    assert output < pid.output_max


def test_setpoint_change_produces_no_derivative_kick():
    pid = PID(kp=2.0, ki=0.0, kd=5.0, output_min=-1000.0, output_max=1000.0)
    dt = 1.0

    pid.setpoint = 10.0
    pid.compute(10.0, dt)

    pid.setpoint = 50.0
    output_on_setpoint_step = pid.compute(10.0, dt)
    assert output_on_setpoint_step == pytest.approx(pid.kp * 40.0)

    # A measurement change, unlike the setpoint change above, does move the
    # derivative term - showing the no-kick result isn't just because kd
    # never contributes anything.
    output_on_measurement_step = pid.compute(11.0, dt)
    assert output_on_measurement_step == pytest.approx(pid.kp * 39.0 - pid.kd * 1.0 / dt)


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
