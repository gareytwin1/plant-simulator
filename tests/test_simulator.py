import pytest

from app.simulator import PlantSimulator

def test_initial_state():
    simulator = PlantSimulator()

    assert simulator.running is False
    assert simulator.load == 0.0
    assert simulator.load_target == 0.0
    assert simulator.suction_pressure == 750.0
    assert simulator.discharge_pressure == 750.0
    assert simulator.spread == 0.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0

def test_start():
    simulator = PlantSimulator()
    simulator.set_load_target(0.50)
    simulator.start()

    assert simulator.running is True
    assert simulator.load == 0.0
    assert simulator.load_target == pytest.approx(0.50)

def test_stop():
    simulator = PlantSimulator()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.step()
    simulator.stop()

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.05)
    assert simulator.load_target == 0.0

def test_step_while_stopped():
    simulator = PlantSimulator()
    simulator.step()

    assert simulator.running is False
    assert simulator.load == 0.0
    assert simulator.suction_pressure == 750.0
    assert simulator.discharge_pressure == 750.0
    assert simulator.spread == 0.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0

def test_step_while_running():
    simulator = PlantSimulator()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.step()

    assert simulator.load == pytest.approx(0.05)
    assert simulator.flow == pytest.approx(5.0)
    assert simulator.suction_pressure == pytest.approx(749.8125)
    assert simulator.discharge_pressure == pytest.approx(750.3125)
    assert simulator.spread == pytest.approx(0.5)

def test_multiple_steps():
    simulator = PlantSimulator()
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
    simulator = PlantSimulator()
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
    simulator = PlantSimulator()
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
    simulator = PlantSimulator()
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
    simulator = PlantSimulator()

    simulator.set_load_target(0.60)
    assert simulator.load_target == pytest.approx(0.60)

    simulator.set_load_target(1.50)
    assert simulator.load_target == 1.0

    simulator.set_load_target(-0.50)
    assert simulator.load_target == 0.0


def test_higher_discharge_header_reduces_flow():
    simulator = PlantSimulator()

    simulator.discharge_header_pressure = 775.0
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
    simulator = PlantSimulator()
    simulator.supply_pressure = 725.0
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
    simulator = PlantSimulator()
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
    simulator = PlantSimulator()

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
    simulator = PlantSimulator()

    simulator.set_discharge_valve_position(0.50)
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.load == pytest.approx(1.0)
    assert simulator.discharge_valve_position == pytest.approx(0.50)
    assert simulator.flow == pytest.approx(86.3575539)

def test_discharge_valve_pressure_drop():
    simulator = PlantSimulator()

    simulator.set_discharge_valve_position(0.50)
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.step()

    assert simulator.valve_resistance == pytest.approx(0.0075)
    assert simulator.valve_pressure_drop == pytest.approx(55.9322)


def test_valve_target_stays_fixed_while_valve_moves():
    simulator = PlantSimulator()

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

