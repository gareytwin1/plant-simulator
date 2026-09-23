import pytest
from app.equipment.compressor import GasCompressor

def test_initial_state():
    simulator = GasCompressor()

    assert simulator.running is False
    assert simulator.load == 0.0
    assert simulator.load_target == 0.0


def test_the_compressor_owns_no_flow_or_pressure():
    """T4-4 moved the operating point to the solver. Flow and the two
    pressures live on the branch and its nodes, and a device that still
    carried them would be a second answer nothing reconciles.
    """
    simulator = GasCompressor()

    for attribute in (
        "flow",
        "suction_pressure",
        "discharge_pressure",
        "upstream_boundary_pressure",
        "downstream_boundary_pressure",
        "step",
    ):
        assert not hasattr(simulator, attribute)

def test_start():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()

    assert simulator.running is True
    assert simulator.load == 0.0
    assert simulator.load_target == pytest.approx(0.50)

def test_stop():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.integrate(1.0)
    simulator.stop()

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.05)
    assert simulator.load_target == 0.0

def test_integrate_while_stopped_moves_nothing():
    simulator = GasCompressor()
    simulator.integrate(1.0)

    assert simulator.running is False
    assert simulator.load == 0.0

def test_integrate_while_running_ramps_load():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.integrate(1.0)

    assert simulator.load == pytest.approx(0.05)

def test_multiple_integrations_accumulate():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()

    simulator.integrate(1.0)
    simulator.integrate(1.0)

    assert simulator.load == pytest.approx(0.10)

def test_load_reaches_its_target_and_holds():
    simulator = GasCompressor()
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.integrate(1.0)

    assert simulator.load == pytest.approx(1.0)
    assert simulator.characteristic(0.0) == pytest.approx(220.0)


def test_temperature_at_reads_the_spread_the_solver_found():
    """Temperature is a function of the process spread, and the spread is a
    solver output now — so the device is asked for it rather than holding it.
    """
    simulator = GasCompressor()

    assert simulator.temperature_at(0.0) == pytest.approx(75.0)
    assert simulator.temperature_at(200.0) == pytest.approx(87.0)
    assert simulator.temperature_at(1000.0) == pytest.approx(
        simulator.max_temperature,
    )

def test_shutdown_reduces_load():
    simulator = GasCompressor()
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.integrate(1.0)

    simulator.stop()
    simulator.integrate(1.0)

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.95)
    assert simulator.load_target == 0.0
    assert simulator.characteristic(0.0) < 220.0

def test_get_state():
    simulator = GasCompressor()
    state = simulator.get_state()

    assert state["running"] is False
    assert state["load"] == 0.0
    assert state["load_target"] == 0.0


def test_get_state_reports_no_flow_or_pressure():
    state = GasCompressor().get_state()

    for key in ("flow", "suction_pressure", "discharge_pressure", "spread"):
        assert key not in state

def test_set_load_target():
    simulator = GasCompressor()

    simulator.set_load_target(0.60)
    assert simulator.load_target == pytest.approx(0.60)

    simulator.set_load_target(1.50)
    assert simulator.load_target == 1.0

    simulator.set_load_target(-0.50)
    assert simulator.load_target == 0.0


def test_the_compressor_owns_no_valve_state():
    """T7-2 moved the discharge valve out of the machine and onto its own
    branch, where the solver can see it. A valve position left on the
    compressor would be a second, inert answer to a question the
    ControlValve on config/plants/olefins_lite.yaml now answers for real.
    """
    simulator = GasCompressor()

    for attribute in (
        "discharge_valve_position",
        "discharge_valve_target",
        "discharge_valve_rate",
        "valve_resistance",
        "set_discharge_valve_position",
    ):
        assert not hasattr(simulator, attribute)

    for key in ("discharge_valve_position", "discharge_valve_target"):
        assert key not in simulator.get_state()


def test_compressor_characteristic_never_rises_with_flow():
    device = GasCompressor()
    device.set_load_target(1.0)
    device.start()
    device.integrate(60.0)

    curve = [
        device.characteristic(flow)
        for flow in (-400.0, -100.0, -1.0, 0.0, 1.0, 100.0, 400.0)
    ]

    for lower, higher in zip(curve, curve[1:]):
        assert higher < lower


def test_compressor_holds_shutoff_rise_at_zero_flow():
    device = GasCompressor()
    device.set_load_target(1.0)
    device.start()
    device.integrate(60.0)

    assert device.load == pytest.approx(1.0)
    assert device.characteristic(0.0) == pytest.approx(220.0)


def test_compressor_rises_above_shutoff_when_flow_reverses():
    device = GasCompressor()
    device.set_load_target(1.0)
    device.start()
    device.integrate(60.0)

    forward = device.characteristic(100.0)
    reverse = device.characteristic(-100.0)

    assert forward < 220.0 < reverse
    assert reverse - 220.0 == pytest.approx(220.0 - forward)


def test_compressor_curve_is_not_clamped_past_runout():
    device = GasCompressor()
    device.set_load_target(0.1)
    device.start()
    device.integrate(60.0)

    overrun = device.max_flow

    assert device.characteristic(overrun) < 0.0
    assert device.characteristic(overrun) == pytest.approx(
        220.0 * device.load ** 2 - 0.002 * overrun ** 2,
    )
