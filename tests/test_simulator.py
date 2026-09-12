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

    assert simulator.running is True
    assert simulator.load == pytest.approx(0.05)
    assert simulator.suction_pressure == pytest.approx(746.25)
    assert simulator.discharge_pressure == pytest.approx(756.25)

def test_multiple_steps():
    simulator = PlantSimulator()
    simulator.set_load_target(0.50)
    simulator.start()

    simulator.step()
    simulator.step()

    assert simulator.load == pytest.approx(0.10)
    assert simulator.suction_pressure == pytest.approx(742.5)
    assert simulator.discharge_pressure == pytest.approx(762.5)
    assert simulator.spread == pytest.approx(20.0)
    assert simulator.temperature == pytest.approx(75.4)
    assert simulator.flow == pytest.approx(55.0)

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
    assert simulator.suction_pressure == pytest.approx(678.75)
    assert simulator.discharge_pressure == pytest.approx(868.75)
    assert simulator.spread == pytest.approx(190.0)
    assert simulator.flow == pytest.approx(38.0)

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

