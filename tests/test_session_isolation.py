import pytest

from app import main
from app.api import validate
from app.engine.sessions import SessionRegistry


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


def test_another_address_minting_sessions_leaves_an_idle_trainees_plant_alone(monkeypatch):
    # T18-11: with the pool full, a churning address ends its own sessions,
    # not the least recently touched one in the whole registry.
    registry = SessionRegistry(factory=main._new_session, max_sessions=4)
    monkeypatch.setattr(main, "sessions", registry)
    trainee = main.app.test_client()
    trainee.get("/api/snapshot", environ_base={"REMOTE_ADDR": "10.0.0.1"})
    cookie = trainee.get_cookie(main.SESSION_COOKIE).value
    churner = main.app.test_client(use_cookies=False)

    for _ in range(4):
        assert churner.get("/api/snapshot", environ_base={"REMOTE_ADDR": "10.0.0.2"}).status_code == 200

    trainee.get("/api/snapshot", environ_base={"REMOTE_ADDR": "10.0.0.1"})

    assert trainee.get_cookie(main.SESSION_COOKIE).value == cookie
    assert len(registry) == 4


def test_a_session_stays_owned_by_the_address_that_created_it(monkeypatch):
    registry = SessionRegistry(factory=main._new_session, max_sessions=3)
    monkeypatch.setattr(main, "sessions", registry)
    trainee = main.app.test_client()
    trainee.get("/api/snapshot", environ_base={"REMOTE_ADDR": "10.0.0.1"})
    cookie = trainee.get_cookie(main.SESSION_COOKIE).value
    # The same browser, now on another network.
    trainee.get("/api/snapshot", environ_base={"REMOTE_ADDR": "10.0.0.9"})
    other = main.app.test_client(use_cookies=False)

    # Had the session moved to 10.0.0.9, it would be that owner's oldest and go first.
    for _ in range(3):
        other.get("/api/snapshot", environ_base={"REMOTE_ADDR": "10.0.0.9"})

    assert registry.peek(cookie) is not None


def test_a_session_behind_a_trusted_proxy_is_owned_by_the_forwarded_client(monkeypatch):
    # Every request here reaches the app from the proxy's address; if the owner
    # were that peer, all clients would be one owner and eviction plain LRU.
    proxies = validate.parse_trusted_proxies(["10.0.0.0/24"])
    real_client_key = validate.client_key
    monkeypatch.setattr(validate, "client_key", lambda remote, forwarded, _: real_client_key(remote, forwarded, proxies))
    registry = SessionRegistry(factory=main._new_session, max_sessions=4)
    monkeypatch.setattr(main, "sessions", registry)
    peer = {"REMOTE_ADDR": "10.0.0.5"}
    trainee = main.app.test_client()
    trainee.get("/api/snapshot", environ_base=peer, headers={"X-Forwarded-For": "203.0.113.1"})
    cookie = trainee.get_cookie(main.SESSION_COOKIE).value
    churner = main.app.test_client(use_cookies=False)

    for _ in range(4):
        churner.get("/api/snapshot", environ_base=peer, headers={"X-Forwarded-For": "203.0.113.2"})

    assert registry.peek(cookie) is not None
    assert len(registry) == 4
