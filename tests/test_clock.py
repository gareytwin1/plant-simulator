"""
Tests for SimulationClock.

Verify that sim_time advances by dt * speed, pause/resume work correctly,
and speed changes mid-run produce no discontinuities.
"""

import pytest

from app.engine.clock import SimulationClock


def test_clock_starts_at_zero():
    clock = SimulationClock()

    assert clock.sim_time == 0.0
    assert clock.speed == 1.0
    assert clock.paused is False


def test_step_advances_sim_time_at_one_x_speed():
    clock = SimulationClock()

    for _ in range(100):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(100.0)


def test_step_advances_sim_time_at_ten_x_speed():
    clock = SimulationClock()
    clock.set_speed(10.0)

    for _ in range(10):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(100.0)


def test_hundred_steps_at_one_x_and_ten_x_give_identical_step_counts():
    """Verify both clocks complete the same number of iterations.

    100 steps at 1x advances sim_time by 100.
    100 steps at 10x advances sim_time by 1000.
    But both have done exactly 100 step() calls — the step count is identical.
    """
    clock_1x = SimulationClock()
    clock_10x = SimulationClock()
    clock_10x.set_speed(10.0)

    step_count_1x = 0
    step_count_10x = 0

    for _ in range(100):
        clock_1x.step(1.0)
        step_count_1x += 1

    for _ in range(100):
        clock_10x.step(1.0)
        step_count_10x += 1

    assert step_count_1x == step_count_10x == 100
    assert clock_1x.sim_time == pytest.approx(100.0)
    assert clock_10x.sim_time == pytest.approx(1000.0)


def test_pause_freezes_sim_time():
    clock = SimulationClock()

    clock.step(1.0)
    assert clock.sim_time == pytest.approx(1.0)

    clock.pause()
    clock.step(1.0)

    assert clock.sim_time == pytest.approx(1.0)


def test_resume_unfreezes_sim_time():
    clock = SimulationClock()

    clock.step(1.0)
    clock.pause()
    clock.step(1.0)

    assert clock.sim_time == pytest.approx(1.0)

    clock.resume()
    clock.step(1.0)

    assert clock.sim_time == pytest.approx(2.0)


def test_pause_and_resume_cycle():
    clock = SimulationClock()

    clock.step(1.0)
    assert clock.sim_time == pytest.approx(1.0)

    clock.pause()
    clock.step(1.0)
    clock.step(1.0)
    assert clock.sim_time == pytest.approx(1.0)

    clock.resume()
    clock.step(1.0)
    assert clock.sim_time == pytest.approx(2.0)

    clock.pause()
    clock.step(1.0)
    assert clock.sim_time == pytest.approx(2.0)

    clock.resume()
    clock.step(1.0)
    assert clock.sim_time == pytest.approx(3.0)


def test_speed_change_mid_run_does_not_skip_or_repeat():
    clock = SimulationClock()

    for _ in range(10):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(10.0)

    clock.set_speed(2.0)

    for _ in range(10):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(30.0)

    clock.set_speed(0.5)

    for _ in range(10):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(35.0)


def test_get_state_returns_flat_dict():
    clock = SimulationClock()

    clock.step(1.0)
    clock.set_speed(2.5)
    clock.pause()

    state = clock.get_state()

    assert isinstance(state, dict)
    assert state == {
        "sim_time": pytest.approx(1.0),
        "speed": 2.5,
        "paused": True,
    }


def test_clock_state_is_json_serialisable():
    import json

    clock = SimulationClock()

    clock.step(5.5)
    clock.set_speed(3.14159)

    state = clock.get_state()

    serialised = json.dumps(state)
    deserialised = json.loads(serialised)

    assert deserialised["sim_time"] == pytest.approx(5.5)
    assert deserialised["speed"] == pytest.approx(3.14159)
    assert deserialised["paused"] is False


def test_zero_speed_freezes_advance():
    clock = SimulationClock()

    clock.step(1.0)
    assert clock.sim_time == pytest.approx(1.0)

    clock.set_speed(0.0)

    for _ in range(100):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(1.0)


def test_negative_speed_reverses_time():
    clock = SimulationClock()

    for _ in range(10):
        clock.step(1.0)

    assert clock.sim_time == pytest.approx(10.0)

    clock.set_speed(-1.0)
    clock.step(5.0)

    assert clock.sim_time == pytest.approx(5.0)


def test_fractional_dt():
    clock = SimulationClock()

    for _ in range(10):
        clock.step(0.5)

    assert clock.sim_time == pytest.approx(5.0)

    clock.set_speed(2.0)

    for _ in range(10):
        clock.step(0.1)

    assert clock.sim_time == pytest.approx(7.0)
