"""GET /api/snapshot (C5) and the retirement of the single-machine pages (T16-10)."""

import pytest

from app import main
from app.training.session import TrainingSession


def session_for(client):
    return main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)


def test_snapshot_answers_a_c4_snapshot_of_the_free_play_plant():
    client = main.app.test_client()

    response = client.get("/api/snapshot")

    assert response.status_code == 200
    body = response.get_json()
    assert {"sim_time", "equipment", "nodes", "streams", "alarms"} <= set(body)
    assert {"K-101", "P-101"} <= set(body["equipment"])
    session = session_for(client)
    assert body == session.operator_view(session.training_scheduler.snapshot())


def test_the_registry_builds_training_sessions():
    client = main.app.test_client()
    client.get("/api/snapshot")

    assert isinstance(session_for(client), TrainingSession)


def test_snapshot_keeps_one_plant_per_cookie_and_never_starts_the_scheduler():
    client = main.app.test_client()

    client.get("/api/snapshot")
    session = session_for(client)
    client.get("/api/snapshot")

    assert session_for(client) is session
    assert session.training_scheduler.running is False


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/compressor"),
        ("get", "/pump"),
        ("get", "/start"),
        ("get", "/stop"),
        ("get", "/api/state"),
        ("post", "/api/start"),
        ("post", "/api/stop"),
        ("post", "/api/step"),
        ("post", "/api/load"),
        ("get", "/api/pump/state"),
        ("post", "/api/pump/start"),
        ("post", "/api/pump/stop"),
        ("post", "/api/pump/step"),
        ("post", "/api/pump/speed"),
    ],
)
def test_a_legacy_route_answers_404(method, path):
    response = getattr(main.app.test_client(), method)(path)

    assert response.status_code == 404


def test_no_single_machine_session_names_remain_in_the_app():
    from pathlib import Path

    banned = (
        "compressor_state",
        "pump_state",
        "compressor_scheduler",
        "pump_scheduler",
        "COMPRESSOR_PLANT",
        "PUMP_PLANT",
    )
    root = Path(main.__file__).parent

    offenders = [
        f"{path.relative_to(root)}: {name}"
        for path in root.rglob("*.py")
        for name in banned
        if name in path.read_text()
    ]

    assert not offenders
