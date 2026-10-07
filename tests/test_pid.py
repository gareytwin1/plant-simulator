import copy
import dataclasses

import pytest

from app.controls.pid import PID, Action


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


def test_track_rejects_non_positive_dt():
    pid = PID(kp=1.0, ki=1.0, kd=1.0, output_min=-10.0, output_max=10.0)

    with pytest.raises(ValueError):
        pid.track(0.0, 0.0, 5.0)


def test_track_clamps_the_preloaded_target_to_the_output_bounds():
    # A caller passing an out-of-range hold target (e.g. a loop's output was
    # left outside a since-narrowed range) should not preload an integral
    # aimed past output_max - the next compute() lands on the bound instead.
    pid = PID(kp=0.0, ki=1.0, kd=0.0, output_min=-10.0, output_max=10.0)

    pid.track(0.0, 1.0, 1000.0)

    assert pid.compute(0.0, 1.0) == pytest.approx(pid.output_max)


def test_track_with_kd_and_a_drifting_measurement_still_reproduces_the_held_output():
    # track() resets _prev_measurement every call, so a follow-up call with
    # the SAME measurement is always derivative = 0 - the preload has to be
    # solved for that guaranteed zero, not for whatever derivative this
    # call's own (about-to-be-discarded) measurement history implies.
    # Baking in the transient value would size the integral for a derivative
    # term the next call can never see, producing a bump on transfer for any
    # kd != 0 loop whose measurement drifts while held - the ordinary case
    # for a real process sitting in manual.
    pid = PID(kp=0.0, ki=1.0, kd=1.0, output_min=-1000.0, output_max=1000.0)

    dt = 1.0
    measurement = 0.0
    for _ in range(5):
        last_measurement = measurement
        pid.track(measurement, dt, 5.0)
        measurement += 2.0

    assert pid.compute(last_measurement, dt) == pytest.approx(5.0)


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


# --------------------------------------------------------------------------
# Action (T8-6): ISA direct / reverse
# --------------------------------------------------------------------------


def test_the_default_action_is_reverse():
    assert PID(kp=1.0, ki=1.0, kd=1.0, output_min=-10.0, output_max=10.0).action is Action.REVERSE


def test_a_reverse_acting_output_falls_as_the_measurement_rises():
    pid = PID(kp=1.0, ki=0.0, kd=0.0, output_min=-100.0, output_max=100.0, setpoint=10.0)

    assert pid.compute(12.0, 1.0) < pid.compute(8.0, 1.0)


def test_a_direct_acting_output_rises_as_the_measurement_rises():
    pid = PID(
        kp=1.0, ki=0.0, kd=0.0, output_min=-100.0, output_max=100.0,
        setpoint=10.0, action=Action.DIRECT,
    )

    assert pid.compute(12.0, 1.0) > pid.compute(8.0, 1.0)


def test_an_omitted_action_is_bit_identical_to_an_explicit_reverse():
    omitted = PID(kp=0.8, ki=0.4, kd=0.05, output_min=-5.0, output_max=5.0, setpoint=3.0)
    explicit = PID(
        kp=0.8, ki=0.4, kd=0.05, output_min=-5.0, output_max=5.0,
        setpoint=3.0, action=Action.REVERSE,
    )

    for measurement in [0.0, 1.5, 7.0, 2.0, 3.0, -4.0, 3.5]:
        assert omitted.compute(measurement, 0.1) == explicit.compute(measurement, 0.1)


def test_direct_and_reverse_differ_only_in_the_sign_of_the_error():
    """Mirroring the measurement about the setpoint mirrors the error, so a
    direct block fed the mirror image of a reverse block's measurements must
    produce exactly the same outputs - P, I, D and the clamp alike."""
    setpoint = 3.0
    reverse = PID(kp=0.8, ki=0.4, kd=0.05, output_min=-1.0, output_max=1.0, setpoint=setpoint)
    direct = PID(
        kp=0.8, ki=0.4, kd=0.05, output_min=-1.0, output_max=1.0,
        setpoint=setpoint, action=Action.DIRECT,
    )

    for measurement in [0.0, 1.5, 7.0, 2.0, 3.0, -4.0, 3.5, 20.0, 20.0, 2.9]:
        mirrored = 2.0 * setpoint - measurement

        assert direct.compute(mirrored, 0.1) == pytest.approx(reverse.compute(measurement, 0.1))


def test_a_direct_acting_loop_controls_a_process_with_negative_gain():
    # Opening a vent lowers the pressure: output up, measurement down.
    process = FakeFirstOrderProcess(gain=-2.0, time_constant=5.0, initial=0.0)
    pid = PID(
        kp=0.8, ki=0.4, kd=0.05, output_min=-100.0, output_max=100.0,
        setpoint=-10.0, action=Action.DIRECT,
    )

    dt = 0.1
    measurement = 0.0
    for _ in range(3000):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)

    assert measurement == pytest.approx(-10.0, abs=0.01)


def test_a_direct_acting_loop_saturates_then_releases_with_no_windup_overshoot():
    process = FakeFirstOrderProcess(gain=-1.0, time_constant=2.0)
    pid = PID(kp=1.0, ki=2.0, kd=0.0, output_min=0.0, output_max=1.0, action=Action.DIRECT)
    pid.setpoint = -1000.0

    dt = 0.1
    measurement = 0.0
    for _ in range(300):
        output = pid.compute(measurement, dt)
        measurement = process.step(output, dt)

    assert output == pytest.approx(pid.output_max)

    achievable = -0.5
    pid.setpoint = achievable
    output = pid.compute(measurement, dt)
    assert output < pid.output_max

    for _ in range(500):
        measurement = process.step(output, dt)
        output = pid.compute(measurement, dt)

    assert measurement == pytest.approx(achievable, abs=0.01)


def test_a_direct_acting_setpoint_change_produces_no_derivative_kick():
    pid = PID(
        kp=2.0, ki=0.0, kd=5.0, output_min=-1000.0, output_max=1000.0,
        action=Action.DIRECT,
    )
    dt = 1.0

    pid.setpoint = 10.0
    pid.compute(10.0, dt)

    pid.setpoint = 50.0
    assert pid.compute(10.0, dt) == pytest.approx(pid.kp * -40.0)

    # Derivative on measurement flips with the action: a rising measurement
    # raises a direct-acting output.
    assert pid.compute(11.0, dt) == pytest.approx(pid.kp * -39.0 + pid.kd * 1.0 / dt)


def test_a_direct_acting_track_reproduces_the_held_output():
    pid = PID(
        kp=0.5, ki=1.0, kd=1.0, output_min=-1000.0, output_max=1000.0,
        setpoint=4.0, action=Action.DIRECT,
    )

    dt = 1.0
    measurement = 0.0
    for _ in range(5):
        last_measurement = measurement
        pid.track(measurement, dt, 5.0)
        measurement += 2.0

    assert pid.compute(last_measurement, dt) == pytest.approx(5.0)


# ---- retune (T16-14) ----


def mid_run_pid():
    pid = PID(kp=1.0, ki=0.5, kd=0.0, output_min=-100.0, output_max=100.0, setpoint=10.0)

    for measurement in (0.0, 2.0, 4.0):
        pid.compute(measurement, 1.0)

    return pid


def test_retune_moves_the_next_output_only_by_the_new_integral_action():
    # Mid-run with an error of 6: a bare gain change would jump the output by
    # (3 - 1) * 6 plus the rescaled integral. Retuned, the next output at the
    # same measurement differs from the untouched block's only by the extra
    # integral action the new Ki takes on that error over one step.
    pid = mid_run_pid()
    untouched = copy.deepcopy(pid)

    pid.retune(kp=3.0, ki=2.0, kd=0.0)

    error = 10.0 - 4.0
    assert pid.compute(4.0, 1.0) == pytest.approx(untouched.compute(4.0, 1.0) + (2.0 - 0.5) * error)


def test_retune_from_no_integral_action_is_bumpless():
    pid = PID(kp=1.0, ki=0.0, kd=0.0, output_min=-100.0, output_max=100.0, setpoint=10.0)
    for measurement in (0.0, 2.0, 4.0):
        pid.compute(measurement, 1.0)
    untouched = copy.deepcopy(pid)

    pid.retune(kp=1.0, ki=0.5, kd=0.0)

    assert pid.compute(4.0, 1.0) == pytest.approx(untouched.compute(4.0, 1.0) + 0.5 * 6.0)


def test_retune_before_any_measurement_holds_the_integral_term():
    pid = PID(kp=1.0, ki=0.5, kd=0.0, output_min=-100.0, output_max=100.0)
    pid.restore_checkpoint(dataclasses.replace(pid.checkpoint(), integral=8.0, prev_measurement=None))

    pid.retune(kp=3.0, ki=2.0, kd=0.0)

    held = pid.checkpoint()
    assert held.ki * held.integral == pytest.approx(0.5 * 8.0)


def test_retune_refuses_a_negative_ki_and_changes_nothing():
    pid = mid_run_pid()
    before = pid.checkpoint()

    with pytest.raises(ValueError):
        pid.retune(kp=2.0, ki=-1.0, kd=0.0)

    assert pid.checkpoint() == before
