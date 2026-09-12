from app.simulator import PlantSimulator

def test_initial_state():
    simulator = PlantSimulator()

    assert simulator.running is False
    assert simulator.pressure == 100.0
    assert simulator.temperature == 75.0
    assert simulator.flow == 0.0
