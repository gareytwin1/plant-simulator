import pytest
from app.equipment.compressor import GasCompressor

def test_initial_state():
    simulator = GasCompressor()

    assert simulator.running is False
    assert simulator.load == 0.0
    assert simulator.load_target == 0.0
    assert simulator.suction_pressure == 750.0
    assert simulator.discharge_pressure == 750.0
    assert simulator.spread == 0.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0

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
    simulator.step()
    simulator.stop()

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.05)
    assert simulator.load_target == 0.0

def test_step_while_stopped():
    simulator = GasCompressor()
    simulator.step()

    assert simulator.running is False
    assert simulator.load == 0.0
    assert simulator.suction_pressure == 750.0
    assert simulator.discharge_pressure == 750.0
    assert simulator.spread == 0.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0

def test_step_while_running():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.step()

    assert simulator.load == pytest.approx(0.05)
    assert simulator.flow == pytest.approx(5.0)
    assert simulator.suction_pressure == pytest.approx(749.8125)
    assert simulator.discharge_pressure == pytest.approx(750.3125)
    assert simulator.spread == pytest.approx(0.5)

def test_multiple_steps():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()

    simulator.step()
    simulator.step()

    assert simulator.load == pytest.approx(0.10)
    assert simulator.flow == pytest.approx(10.0)
    assert simulator.suction_pressure == pytest.approx(749.25)
    assert simulator.discharge_pressure == pytest.approx(751.25)
    assert simulator.spread == pytest.approx(2.0)

def test_reaches_normal_operating_target():
    simulator = GasCompressor()
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.load == pytest.approx(1.0)
    assert simulator.suction_pressure == pytest.approx(675.0)
    assert simulator.discharge_pressure == pytest.approx(875.0)
    assert simulator.spread == pytest.approx(200.0)
    assert simulator.temperature == pytest.approx(87.0)
    assert simulator.flow == pytest.approx(100.0)

def test_shutdown_reduces_load_and_flow():
    simulator = GasCompressor()
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    simulator.stop()
    simulator.step()

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.95)
    assert simulator.load_target == 0.0
    assert simulator.flow == pytest.approx(95.0)
    assert simulator.suction_pressure == pytest.approx(682.3125)
    assert simulator.discharge_pressure == pytest.approx(862.8125)
    assert simulator.spread == pytest.approx(180.5) 

def test_get_state():
    simulator = GasCompressor()
    state = simulator.get_state()

    assert state["running"] is False
    assert state["load"] == 0.0
    assert state["pressure"] == 750.0
    assert state["suction_pressure"] == 750.0
    assert state["discharge_pressure"] == 750.0
    assert state["spread"] == 0.0
    assert state["temperature"] == 75.0
    assert state["flow"] == 0.0

def test_set_load_target():
    simulator = GasCompressor()

    simulator.set_load_target(0.60)
    assert simulator.load_target == pytest.approx(0.60)

    simulator.set_load_target(1.50)
    assert simulator.load_target == 1.0

    simulator.set_load_target(-0.50)
    assert simulator.load_target == 0.0


def test_higher_discharge_header_reduces_flow():
    simulator = GasCompressor()

    simulator.downstream_boundary_pressure = 775.0
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.load == pytest.approx(1.0)
    assert simulator.flow == pytest.approx(94.1469)
    assert simulator.suction_pressure == pytest.approx(683.5227)
    assert simulator.discharge_pressure == pytest.approx(885.7955)
    assert simulator.spread == pytest.approx(202.2727)

def test_lower_supply_pressure_reduces_flow():
    simulator = GasCompressor()
    simulator.upstream_boundary_pressure = 725.0
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.load == pytest.approx(1.0)
    assert simulator.flow == pytest.approx(94.1469)
    assert simulator.suction_pressure == pytest.approx(658.5227)
    assert simulator.discharge_pressure == pytest.approx(860.7955)
    assert simulator.spread == pytest.approx(202.2727)

def test_compressor_pressure_rise_matches_process_spread():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()

    for _ in range(10):
        simulator.step()

    assert simulator.load == pytest.approx(0.50)
    assert simulator.flow == pytest.approx(50.0)
    assert simulator.compressor_pressure_rise == pytest.approx(50.0)
    assert simulator.spread == pytest.approx(50.0)
    assert simulator.compressor_pressure_rise == pytest.approx(
        simulator.spread
    )

def test_downstream_restriction_reduces_flow():
    simulator = GasCompressor()

    simulator.downstream_restriction = 0.01
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.load == pytest.approx(1.0)
    assert simulator.flow == pytest.approx(82.9156)
    assert simulator.suction_pressure == pytest.approx(698.4375)
    assert simulator.discharge_pressure == pytest.approx(904.6875)
    assert simulator.spread == pytest.approx(206.25)

def test_partially_closed_discharge_valve_reduces_flow():
    simulator = GasCompressor()

    simulator.set_discharge_valve_position(0.50)
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.load == pytest.approx(1.0)
    assert simulator.discharge_valve_position == pytest.approx(0.50)
    assert simulator.flow == pytest.approx(86.3575539)

def test_discharge_valve_pressure_drop():
    simulator = GasCompressor()

    simulator.set_discharge_valve_position(0.50)
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.valve_resistance == pytest.approx(0.0075)
    assert simulator.valve_pressure_drop == pytest.approx(55.9322)


def test_valve_target_stays_fixed_while_valve_moves():
    simulator = GasCompressor()

    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    simulator.set_discharge_valve_position(0.10)

    for _ in range(18):
        simulator.step()

    initial_flow = simulator.flow

    assert simulator.discharge_valve_position == pytest.approx(0.10)

    simulator.set_discharge_valve_position(0.70)

    assert simulator.discharge_valve_target == pytest.approx(0.70)
    assert simulator.discharge_valve_position == pytest.approx(0.10)

    simulator.step()

    first_flow = simulator.flow

    assert simulator.discharge_valve_target == pytest.approx(0.70)
    assert simulator.discharge_valve_position == pytest.approx(0.15)
    assert first_flow > initial_flow

    simulator.step()

    assert simulator.discharge_valve_target == pytest.approx(0.70)
    assert simulator.discharge_valve_position == pytest.approx(0.20)
    assert simulator.flow > first_flow


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
