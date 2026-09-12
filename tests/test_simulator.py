from app.simulator import PlantSimulator

def test_initial_state():
    simulator = PlantSimulator()

    assert simulator.running is False
    assert simulator.pressure == 100.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0


def test_start():
    simulator = PlantSimulator()
    simulator.start()

    assert simulator.running is True
    assert simulator.flow == 50.0


def test_stop():
    simulator = PlantSimulator()
    simulator.start()
    simulator.stop()

    assert simulator.running is False
    assert simulator.flow ==0.0


def test_step_while_stopped():
    simulator = PlantSimulator()
    simulator.step()

    assert simulator.running is False
    assert simulator.pressure == 100.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0


def test_step_while_running():
    simulator = PlantSimulator()
    simulator.start()
    simulator.step()

    assert simulator.running is True
    assert simulator.pressure == 101.0
    assert simulator.temperature == 75.5
    assert simulator.flow == 52.0


def test_multiple_steps():
    simulator = PlantSimulator()
    simulator.start()

    simulator.step()
    simulator.step()

    assert simulator.running is True
    assert simulator.pressure == 102.0
    assert simulator.temperature == 76.0
    assert simulator.flow == 54.0


def test_get_state():
    simulator = PlantSimulator()
    state = simulator.get_state()

    assert state == {
            "running": False,
            "pressure": 100.0,
            "temperature": 75.0,
            "flow": 0.0,
    }
