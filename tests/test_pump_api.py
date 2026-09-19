import pytest

from app import main


def test_pump_api_state():
    client = main.app.test_client()

    response = client.get("/api/pump/state")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is False
    assert state["speed"] == 0.0
    assert state["suction_pressure"] == 50.0
    assert state["discharge_pressure"] == 50.0
    assert state["flow"] == 0.0


def test_pump_api_start():
    client = main.app.test_client()

    response = client.post("/api/pump/start")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is True
    assert state["speed"] == 0.0
    assert state["flow"] == 0.0


def test_pump_api_stop():
    client = main.app.test_client()

    client.post(
        "/api/pump/speed",
        json={"speed_target": 1.0},
    )
    client.post("/api/pump/start")

    for _ in range(10):
        client.post("/api/pump/step")

    response = client.post("/api/pump/stop")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is False
    assert state["speed_target"] == 0.0
    assert state["speed"] == pytest.approx(1.0)


def test_pump_api_step():
    client = main.app.test_client()

    client.post(
        "/api/pump/speed",
        json={"speed_target": 0.50},
    )
    client.post("/api/pump/start")

    response = client.post("/api/pump/step")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is True
    assert state["speed"] == pytest.approx(0.10)


def test_pump_api_set_speed():
    client = main.app.test_client()

    response = client.post(
        "/api/pump/speed",
        json={"speed_target": 0.60},
    )

    state = response.get_json()

    assert response.status_code == 200
    assert state["speed_target"] == pytest.approx(0.60)


def test_pump_api_speed_target_clamps_above_one():
    client = main.app.test_client()

    response = client.post(
        "/api/pump/speed",
        json={"speed_target": 1.5},
    )

    state = response.get_json()

    assert state["speed_target"] == pytest.approx(1.0)


def test_pump_api_speed_target_clamps_below_zero():
    client = main.app.test_client()

    response = client.post(
        "/api/pump/speed",
        json={"speed_target": -0.5},
    )

    state = response.get_json()

    assert state["speed_target"] == pytest.approx(0.0)


def test_pump_page_renders():
    client = main.app.test_client()

    response = client.get("/pump")

    assert response.status_code == 200
