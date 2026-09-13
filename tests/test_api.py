import pytest

from app import main
from app.simulator import PlantSimulator


@pytest.fixture(autouse=True)
def reset_simulator():
    main.simulator = PlantSimulator()


def test_api_state():
    client = main.app.test_client()

    response = client.get("/api/state")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is False
    assert state["load"] == 0.0
    assert state["suction_pressure"] == 750.0
    assert state["discharge_pressure"] == 750.0
    assert state["spread"] == 0.0
    assert state["temperature"] == 75.0
    assert state["flow"] == 0.0


def test_api_start():
    client = main.app.test_client()

    response = client.post("/api/start")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is True
    assert state["load"] == 0.0
    assert state["flow"] == 0.0


def test_api_stop():
    client = main.app.test_client()

    client.post(
        "/api/load",
        json={"load_target": 0.50},
    )
    client.post("/api/start")
    client.post("/api/step")

    response = client.post("/api/stop")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is False
    assert state["load"] == pytest.approx(0.05)
    assert state["load_target"] == 0.0


def test_api_step():
    client = main.app.test_client()

    client.post(
        "/api/load",
        json={"load_target": 0.50},
    )
    client.post("/api/start")

    response = client.post("/api/step")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is True
    assert state["load"] == pytest.approx(0.10)
    assert state["pressure"] == pytest.approx(751.25)   
    assert state["temperature"] == pytest.approx(75.04)

def test_api_set_load():
    client = main.app.test_client()

    response = client.post(
        "/api/load",
        json={"load_target": 0.60},
    )

    state = response.get_json()

    assert response.status_code == 200
    assert state["load_target"] == pytest.approx(0.60)
