from app.main import app


def test_api_state():
    client = app.test_client()

    response = client.get("/api/state")

    assert response.status_code == 200
    assert response.get_json() == {
        "running": False,
        "pressure": 100.0,
        "suction_pressure": 100.0,
        "discharge_pressure": 100.0,
        "temperature": 75.0,
        "flow": 0.0,
    }


def test_api_start():
    client = app.test_client()

    response = client.post("/api/start")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is True
    assert state["flow"] == 50.0

    client.post("/api/stop")



def test_api_stop():
    client = app.test_client()

    client.post("/api/start")
    response = client.post("/api/stop")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is False
    assert state["flow"] == 0.0


def test_api_step():
    client = app.test_client()

    client.post("/api/start")
    response = client.post("/api/step")
    state = response.get_json()

    assert response.status_code == 200
    assert state["running"] is True
    assert state["pressure"] == 105.0
    assert state["suction_pressure"] == 100.0
    assert state["discharge_pressure"] == 105.0
    assert state["temperature"] == 75.5
    assert state["flow"] == 55.0

    client.post("/api/stop")



