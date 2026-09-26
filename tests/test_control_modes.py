import pytest

from app.controls.modes import Loop, Mode
from app.controls.pid import PID


class FakeFirstOrderProcess:
    def __init__(self, gain: float, time_constant: float, initial: float = 0.0) -> None:
        self.gain = gain
        self.time_constant = time_constant
        self.value = initial

    def step(self, u: float, dt: float) -> float:
        self.value += dt * (self.gain * u - self.value) / self.time_constant
        return self.value


def test_manual_to_auto_produces_no_output_step():
    pid = PID(kp=1.0, ki=0.4, kd=0.0, output_min=0.0, output_max=100.0, setpoint=20.0)
    loop = Loop(pid, mode=Mode.MANUAL)
    loop.manual_output = 37.0

    dt = 1.0
    measurement = 15.0
    for _ in range(10):
        last_measurement = measurement
        output = loop.compute(measurement, dt)
        measurement += 0.2

    assert output == pytest.approx(37.0)

    loop.mode = Mode.AUTO
    output_on_transfer = loop.compute(last_measurement, dt)

    assert output_on_transfer == pytest.approx(37.0)


def test_auto_to_manual_to_auto_produces_no_output_step():
    process = FakeFirstOrderProcess(gain=2.0, time_constant=5.0, initial=5.0)
    pid = PID(kp=0.8, ki=0.4, kd=0.05, output_min=-100.0, output_max=100.0, setpoint=10.0)
    loop = Loop(pid, mode=Mode.AUTO)

    dt = 0.1
    measurement = process.value
    for _ in range(300):
        output = loop.compute(measurement, dt)
        measurement = process.step(output, dt)

    output_before_switch = loop.output

    # Switching to manual seeds manual_output from the loop's last output, so
    # the switch itself produces no step even before the operator moves it.
    loop.mode = Mode.MANUAL
    output_on_switch_to_manual = loop.compute(measurement, dt)
    assert output_on_switch_to_manual == pytest.approx(output_before_switch)

    for _ in range(20):
        output = loop.compute(measurement, dt)
        measurement = process.step(output, dt)

    output_before_return = loop.output

    loop.mode = Mode.AUTO
    output_on_return_to_auto = loop.compute(measurement, dt)
    assert output_on_return_to_auto == pytest.approx(output_before_return)


def test_integral_preloaded_correctly_on_transfer():
    # Isolates PID.track() itself: back-calculating the integral against a
    # held output must make the very next compute() call - same setpoint,
    # same measurement - reproduce that output exactly.
    pid = PID(kp=0.8, ki=0.4, kd=0.05, output_min=-100.0, output_max=100.0, setpoint=10.0)

    dt = 0.1
    measurement = 6.0
    for _ in range(5):
        pid.track(measurement, dt, 42.0)

    assert pid.compute(measurement, dt) == pytest.approx(42.0)


def test_cascade_slave_tracks_while_master_is_in_manual():
    process = FakeFirstOrderProcess(gain=2.0, time_constant=8.0, initial=20.0)
    slave_pid = PID(kp=0.5, ki=0.3, kd=0.0, output_min=0.0, output_max=100.0)
    slave = Loop(slave_pid, mode=Mode.CASCADE)
    slave.output = 30.0

    master_pid = PID(kp=1.0, ki=0.2, kd=0.0, output_min=0.0, output_max=100.0)
    master = Loop(master_pid, mode=Mode.MANUAL)
    master.output = 30.0

    dt = 1.0
    measurement = process.value
    for _ in range(50):
        last_measurement = measurement
        output = slave.compute(measurement, dt, master=master)
        measurement = process.step(output, dt)
        assert output == pytest.approx(30.0)

    # Master returns to auto holding the same output the slave was already
    # tracking toward, so re-engaging the cascade at the same measurement
    # produces no step either.
    master.mode = Mode.AUTO
    output_on_reengage = slave.compute(last_measurement, dt, master=master)
    assert output_on_reengage == pytest.approx(30.0)


def test_cascade_requires_a_master():
    pid = PID(kp=1.0, ki=1.0, kd=0.0, output_min=0.0, output_max=10.0)
    loop = Loop(pid, mode=Mode.CASCADE)

    with pytest.raises(ValueError):
        loop.compute(0.0, 1.0)


def test_cascade_active_follows_the_master_setpoint():
    process = FakeFirstOrderProcess(gain=1.0, time_constant=4.0, initial=0.0)
    slave_pid = PID(kp=1.0, ki=0.5, kd=0.0, output_min=-100.0, output_max=100.0)
    slave = Loop(slave_pid, mode=Mode.CASCADE)

    master_pid = PID(kp=1.0, ki=0.0, kd=0.0, output_min=-100.0, output_max=100.0)
    master = Loop(master_pid, mode=Mode.AUTO)
    master.output = 12.0

    dt = 0.1
    measurement = process.value
    for _ in range(500):
        output = slave.compute(measurement, dt, master=master)
        measurement = process.step(output, dt)

    assert measurement == pytest.approx(12.0, abs=0.01)
