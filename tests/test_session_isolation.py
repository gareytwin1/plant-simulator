import pytest

from app import main


def session_for(client):
    return main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)


def test_response_sets_a_session_cookie():
    client = main.app.test_client()

    response = client.get("/api/snapshot")

    assert main.SESSION_COOKIE in response.headers.get("Set-Cookie", "")


def test_same_client_reuses_its_plant_across_requests():
    client = main.app.test_client()

    started = client.get("/api/snapshot").get_json()["sim_time"]
    session = session_for(client)
    assert session.training_scheduler.step_once() is not None

    snapshot = client.get("/api/snapshot").get_json()

    assert session_for(client) is session
    assert snapshot["sim_time"] == pytest.approx(session.training_scheduler.snapshot().sim_time)
    assert snapshot["sim_time"] > started


def test_two_clients_have_independent_training_plants():
    client_a = main.app.test_client()
    client_b = main.app.test_client()

    started_a = client_a.get("/api/snapshot").get_json()["sim_time"]
    started_b = client_b.get("/api/snapshot").get_json()["sim_time"]
    assert session_for(client_a) is not session_for(client_b)

    session_for(client_a).training_scheduler.step_once()

    assert client_a.get("/api/snapshot").get_json()["sim_time"] > started_a
    assert client_b.get("/api/snapshot").get_json()["sim_time"] == started_b


def test_no_module_global_equipment_remains():
    assert not hasattr(main, "compressor")
    assert not hasattr(main, "pump")


def test_session_teardown_releases_the_plant():
    client = main.app.test_client()
    client.get("/api/snapshot")

    session_id = client.get_cookie(main.SESSION_COOKIE).value
    assert main.sessions.get(session_id) is not None

    main.sessions.end(session_id)

    assert main.sessions.get(session_id) is None
