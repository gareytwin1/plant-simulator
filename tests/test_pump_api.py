import time

import pytest

from app import main


WAIT = 5.0


def session_for(client):
    return main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)


def wait_for_a_published_step(scheduler):
    deadline = time.monotonic() + WAIT

    while scheduler.snapshot().sim_time == 0.0:
        assert time.monotonic() < deadline
        time.sleep(0.01)


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


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"json": {}}, id="missing-field"),
        pytest.param({"data": "null", "content_type": "application/json"}, id="null-body"),
        pytest.param({"json": {"speed_target": "abc"}}, id="non-numeric-string"),
        pytest.param({"json": {"speed_target": "0.8"}}, id="numeric-string"),
        pytest.param({"json": {"speed_target": True}}, id="bool"),
        pytest.param({"json": {"speed_target": [1]}}, id="array"),
        pytest.param(
            {"data": '{"speed_target": NaN}', "content_type": "application/json"},
            id="raw-nan-literal",
        ),
        pytest.param({"data": "not json", "content_type": "text/plain"}, id="non-json-body"),
    ],
)
def test_pump_api_set_speed_rejects_invalid_body(kwargs):
    client = main.app.test_client()

    response = client.post("/api/pump/speed", **kwargs)

    assert response.status_code == 400
    assert "error" in response.get_json()

    state = client.get("/api/pump/state").get_json()
    assert state["speed_target"] == 0.0


def test_pump_page_renders():
    client = main.app.test_client()

    try:
        response = client.get("/pump")

        assert response.status_code == 200
    finally:
        # Rendering the page starts pump_scheduler's worker (T2-6). This is
        # the one test in the suite that renders a page, so it is the one
        # that must end its own session rather than leaking that thread.
        session_id = client.get_cookie(main.SESSION_COOKIE).value
        main.sessions.end(session_id)


def test_commands_show_in_the_response_while_the_scheduler_runs():
    # DEFECT REPRODUCTION
    client = main.app.test_client()
    client.get("/pump")
    session = session_for(client)

    try:
        wait_for_a_published_step(session.pump_scheduler)

        started = client.post("/api/pump/start").get_json()
        commanded = client.post("/api/pump/speed", json={"speed_target": 0.8}).get_json()

        assert started["running"] is True
        assert commanded["speed_target"] == pytest.approx(0.8)
    finally:
        session.end()


def test_manual_step_while_the_scheduler_runs_keeps_its_409_reason():
    # COMPATIBILITY
    client = main.app.test_client()
    client.get("/pump")
    session = session_for(client)

    try:
        response = client.post("/api/pump/step")

        assert response.status_code == 409
        assert response.get_json() == {"error": main.STEP_UNAVAILABLE_REASON}
    finally:
        session.end()


def test_manual_step_on_an_ended_session_is_refused_with_session_ended():
    # DEFECT REPRODUCTION
    client = main.app.test_client()
    client.get("/api/pump/state")
    session = session_for(client)
    session.pump_scheduler.close()

    response = client.post("/api/pump/step")

    assert response.status_code == 409
    assert response.get_json() == {"error": "session ended"}
