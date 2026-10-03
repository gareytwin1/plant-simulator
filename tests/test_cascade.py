import pytest

from app.controls.cascade import Cascade
from app.controls.modes import Loop, Mode
from app.controls.pid import PID, Action

AREA = 5.0
Q_MAX = 200.0
LEVEL_SP = 2.0
DT = 0.1


class Tank:
    """Level integrator fed by a disturbance and one valve-driven flow."""

    def __init__(self, valve_lag: float = 1.0) -> None:
        self.level = LEVEL_SP
        self.valve_lag = valve_lag
        self.valve_gain = 1.0
        self.inlet_disturbance = 0.0
        self.flow = 100.0

    def step(self, valve: float, manipulate_outlet: bool, dt: float) -> None:
        target = valve * Q_MAX * self.valve_gain
        self.flow += dt * (target - self.flow) / self.valve_lag
        base = 100.0
        if manipulate_outlet:
            net = base + self.inlet_disturbance - self.flow
        else:
            net = self.flow - base + self.inlet_disturbance
        self.level += dt * net / (AREA * 60.0)


def make_cascade(manipulate_outlet: bool) -> Cascade:
    outer_action = Action.DIRECT if manipulate_outlet else Action.REVERSE
    outer = Loop(
        PID(kp=40.0, ki=1.0, kd=0.0, output_min=0.0, output_max=1.0,
            setpoint=LEVEL_SP, action=outer_action),
        mode=Mode.AUTO,
    )
    inner = Loop(
        PID(kp=0.004, ki=0.02, kd=0.0, output_min=0.0, output_max=1.0),
        mode=Mode.CASCADE,
    )
    return Cascade(outer, inner, inner_range=(0.0, Q_MAX))


def settle(cascade: Cascade, tank: Tank, manipulate_outlet: bool, seconds: float) -> list[float]:
    levels = []
    for _ in range(int(seconds / DT)):
        valve = cascade.compute(tank.level, tank.flow, DT)
        tank.step(valve, manipulate_outlet, DT)
        levels.append(tank.level)
    return levels


def run_single_loop(manipulate_outlet: bool, seconds: float) -> list[float]:
    action = Action.DIRECT if manipulate_outlet else Action.REVERSE
    loop = Loop(
        PID(kp=0.2, ki=0.002, kd=0.0, output_min=0.0, output_max=1.0,
            setpoint=LEVEL_SP, action=action),
        mode=Mode.AUTO,
    )
    tank = Tank()
    tank.inlet_disturbance = 30.0
    tank.valve_gain = 0.8
    levels = []
    for _ in range(int(seconds / DT)):
        tank.step(loop.compute(tank.level, DT), manipulate_outlet, DT)
        levels.append(tank.level)
    return levels


def disturb(tank: Tank, manipulate_outlet: bool) -> None:
    # A bigger inlet flow, and a valve that has lost a fifth of its gain.
    tank.inlet_disturbance = 30.0 if manipulate_outlet else -30.0
    tank.valve_gain = 0.8


@pytest.mark.parametrize("manipulate_outlet", [True, False])
def test_cascade_holds_level_against_an_inlet_flow_disturbance(manipulate_outlet):
    cascade = make_cascade(manipulate_outlet)
    tank = Tank()
    settle(cascade, tank, manipulate_outlet, 60.0)
    disturb(tank, manipulate_outlet)

    levels = settle(cascade, tank, manipulate_outlet, 600.0)

    assert max(abs(level - LEVEL_SP) for level in levels) < 0.1
    assert levels[-1] == pytest.approx(LEVEL_SP, abs=0.02)


@pytest.mark.parametrize("manipulate_outlet", [True, False])
def test_one_level_loop_on_the_valve_does_not_hold_the_same_disturbance(manipulate_outlet):
    levels = run_single_loop(manipulate_outlet, 600.0)

    assert max(abs(level - LEVEL_SP) for level in levels) > 1.0


def test_inner_setpoint_scales_the_outer_output_into_the_inner_range():
    outer = Loop(PID(1.0, 0.0, 0.0, output_min=0.0, output_max=100.0), mode=Mode.MANUAL)
    inner = Loop(PID(1.0, 0.1, 0.0, output_min=0.0, output_max=1.0), mode=Mode.CASCADE)
    cascade = Cascade(outer, inner, inner_range=(50.0, 250.0))

    assert cascade.inner_setpoint(0.0) == pytest.approx(50.0)
    assert cascade.inner_setpoint(25.0) == pytest.approx(100.0)
    assert cascade.inner_setpoint(100.0) == pytest.approx(250.0)

    outer.manual_output = 25.0
    cascade.compute(0.0, 80.0, 1.0)

    assert inner.pid.setpoint == pytest.approx(100.0)


def test_a_degenerate_range_is_rejected():
    inner = Loop(PID(1.0, 0.1, 0.0, output_min=0.0, output_max=1.0), mode=Mode.CASCADE)
    flat_outer = Loop(PID(1.0, 0.0, 0.0, output_min=1.0, output_max=1.0))
    outer = Loop(PID(1.0, 0.0, 0.0, output_min=0.0, output_max=1.0))

    with pytest.raises(ValueError):
        Cascade(flat_outer, inner, inner_range=(0.0, 200.0))
    with pytest.raises(ValueError):
        Cascade(outer, inner, inner_range=(200.0, 0.0))


def running_cascade() -> tuple[Cascade, Tank]:
    cascade = make_cascade(manipulate_outlet=True)
    tank = Tank()
    settle(cascade, tank, True, 60.0)
    tank.inlet_disturbance = 20.0
    settle(cascade, tank, True, 30.0)
    return cascade, tank


def test_outer_loop_to_manual_holds_the_inner_loop_output_with_no_step():
    cascade, tank = running_cascade()
    held = cascade.inner.output

    cascade.outer.mode = Mode.MANUAL
    for _ in range(50):
        valve = cascade.compute(tank.level, tank.flow, DT)
        tank.step(valve, True, DT)
        assert valve == pytest.approx(held)

    assert cascade.engaged


def test_outer_loop_back_to_auto_resumes_with_no_inner_step():
    cascade, tank = running_cascade()
    cascade.outer.mode = Mode.MANUAL
    for _ in range(50):
        tank.step(cascade.compute(tank.level, tank.flow, DT), True, DT)
    # Bumpless means no step at the same measurements, as in test_control_modes.
    held = cascade.compute(tank.level, tank.flow, DT)

    cascade.outer.mode = Mode.AUTO
    valve = cascade.compute(tank.level, tank.flow, DT)

    assert valve == pytest.approx(held, abs=1e-3)


def test_breaking_the_cascade_leaves_the_inner_loop_in_manual_holding_its_output():
    cascade, tank = running_cascade()
    last = cascade.inner.output

    cascade.break_cascade()

    assert not cascade.engaged
    assert cascade.inner.mode is Mode.MANUAL
    for _ in range(50):
        valve = cascade.compute(tank.level, tank.flow, DT)
        tank.step(valve, True, DT)
        assert valve == pytest.approx(last)


def test_inner_loop_stands_alone_after_the_cascade_is_discarded():
    cascade, tank = running_cascade()
    inner = cascade.inner
    last = inner.output
    cascade.break_cascade()
    del cascade

    # A CASCADE loop with no outer loop raises; the broken one just runs.
    assert inner.compute(tank.flow, DT) == pytest.approx(last)

    inner.mode = Mode.AUTO
    assert inner.compute(tank.flow, DT) == pytest.approx(last)


def test_engaging_again_resumes_with_no_inner_step_and_holds_level():
    cascade, tank = running_cascade()
    cascade.break_cascade()
    for _ in range(50):
        tank.step(cascade.compute(tank.level, tank.flow, DT), True, DT)
    held = cascade.compute(tank.level, tank.flow, DT)

    cascade.engage()
    assert cascade.engaged
    assert cascade.compute(tank.level, tank.flow, DT) == pytest.approx(held)

    levels = settle(cascade, tank, True, 600.0)
    assert levels[-1] == pytest.approx(LEVEL_SP, abs=0.02)
