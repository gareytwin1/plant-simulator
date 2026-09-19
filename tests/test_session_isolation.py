import pytest

from app import main


def test_response_sets_a_session_cookie():
    client = main.app.test_client()

    response = client.get("/api/state")

    assert main.SESSION_COOKIE in response.headers.get("Set-Cookie", "")


def test_same_client_reuses_its_session_across_requests():
    client = main.app.test_client()

    client.post("/api/load", json={"load_target": 0.5})
    client.post("/api/start")
    client.post("/api/step")

    response = client.get("/api/state")
    state = response.get_json()

    assert state["running"] is True
    assert state["load"] == pytest.approx(0.05)


def test_two_clients_have_independent_compressor_state():
    client_a = main.app.test_client()
    client_b = main.app.test_client()

    client_a.post("/api/load", json={"load_target": 0.8})
    client_a.post("/api/start")
    client_a.post("/api/step")

    state_a = client_a.get("/api/state").get_json()
    state_b = client_b.get("/api/state").get_json()

    assert state_a["running"] is True
    assert state_b["running"] is False
    assert state_b["load_target"] == 0.0


def test_two_clients_have_independent_pump_state():
    client_a = main.app.test_client()
    client_b = main.app.test_client()

    client_a.post("/api/pump/speed", json={"speed_target": 1.0})
    client_a.post("/api/pump/start")
    client_a.post("/api/pump/step")

    state_a = client_a.get("/api/pump/state").get_json()
    state_b = client_b.get("/api/pump/state").get_json()

    assert state_a["running"] is True
    assert state_b["running"] is False
    assert state_b["speed_target"] == 0.0


def test_no_module_global_equipment_remains():
    assert not hasattr(main, "compressor")
    assert not hasattr(main, "pump")


def test_session_teardown_releases_the_plant():
    client = main.app.test_client()
    client.get("/api/state")

    session_id = client.get_cookie(main.SESSION_COOKIE).value
    assert main.sessions.get(session_id) is not None

    main.sessions.end(session_id)

    assert main.sessions.get(session_id) is None
