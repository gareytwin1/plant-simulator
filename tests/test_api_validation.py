import math
from pathlib import Path

import pytest
from flask import Flask

from app import config, main
from app.alarms.acknowledge import Acknowledged
from app.api import validate
from app.api.action import create_action_blueprint
from app.api.alarms import create_alarm_blueprint
from app.api.scenario import create_scenario_blueprint
from app.engine.engine import Engine
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.scenarios.runner import ScenarioLibrary, ScenarioRunner
from app.scoring.actionlog import ActionLog


def unlimited():
    return validate.RateLimiter(rate=1e9, burst=1e9)


def raw(client, path, data, content_type="application/json"):
    return client.post(path, data=data, content_type=content_type)


# Bodies no endpoint may answer with a 500, whichever field it reads. `{f}` is
# the endpoint's own field name.
BAD_BODIES = [
    pytest.param("{not json", "application/json", "request body must be a JSON object", id="malformed"),
    pytest.param("", "application/json", "request body must be a JSON object", id="empty"),
    pytest.param("[1, 2]", "application/json", "request body must be a JSON object", id="array"),
    pytest.param("null", "application/json", "request body must be a JSON object", id="null"),
    pytest.param("3", "application/json", "request body must be a JSON object", id="scalar"),
    pytest.param('{"{f}": 0.5}', "text/plain", "request body must be a JSON object", id="wrong-content-type"),
    pytest.param("{}", "application/json", "missing field: {f}", id="missing"),
    pytest.param('{"{f}": "0.5"}', "application/json", "{f} must be a number", id="string"),
    pytest.param('{"{f}": true}', "application/json", "{f} must be a number", id="bool"),
    pytest.param('{"{f}": null}', "application/json", "{f} must be a number", id="null-value"),
    pytest.param('{"{f}": [0.5]}', "application/json", "{f} must be a number", id="list-value"),
    pytest.param('{"{f}": NaN}', "application/json", "{f} must be finite", id="nan"),
    pytest.param('{"{f}": Infinity}', "application/json", "{f} must be finite", id="infinity"),
    pytest.param('{"{f}": -Infinity}', "application/json", "{f} must be finite", id="negative-infinity"),
    pytest.param('{"{f}": 1e30}', "application/json", "{f} must be within", id="huge"),
    pytest.param('{"{f}": -2000000}', "application/json", "{f} must be within", id="huge-negative"),
    pytest.param('{"{f}": 1' + "0" * 400 + "}", "application/json", "{f} must be within", id="huge-int-literal"),
    pytest.param('{"{f}": -1' + "0" * 400 + "}", "application/json", "{f} must be within", id="huge-negative-int-literal"),
    pytest.param('{"{f}": 0.5, "extra": 1}', "application/json", "unknown field: extra", id="extra-key"),
]

NUMBER_ENDPOINTS = [
    ("/api/load", "load_target"),
    ("/api/pump/speed", "speed_target"),
]


@pytest.mark.parametrize("path, field", NUMBER_ENDPOINTS)
@pytest.mark.parametrize("body, content_type, message", BAD_BODIES)
def test_number_endpoints_answer_a_bad_body_with_a_4xx_and_a_message(
    path, field, body, content_type, message,
):
    client = main.app.test_client()

    response = raw(client, path, body.replace("{f}", field), content_type)

    assert response.status_code == 400
    assert response.get_json()["error"].startswith(message.replace("{f}", field))


@pytest.mark.parametrize("path, field", NUMBER_ENDPOINTS)
def test_number_endpoints_refuse_an_oversized_body_with_413(path, field):
    client = main.app.test_client()
    padding = "x" * (config.API_MAX_BODY_BYTES + 1)

    response = raw(client, path, f'{{"{field}": 0.5, "pad": "{padding}"}}')

    assert response.status_code == 413
    assert "error" in response.get_json()


@pytest.mark.parametrize("path, field", NUMBER_ENDPOINTS)
@pytest.mark.parametrize("value, expected", [(0.5, 0.5), (1, 1.0), (1.5, 1.0), (-0.5, 0.0)])
def test_number_endpoints_still_accept_a_finite_value_and_clamp_it(path, field, value, expected):
    client = main.app.test_client()

    response = client.post(path, json={field: value})

    assert response.status_code == 200
    assert response.get_json()[field] == pytest.approx(expected)


@pytest.mark.parametrize("path", ["/api/start", "/api/stop", "/api/step", "/api/pump/start"])
def test_a_post_only_route_answers_get_with_a_json_405(path):
    client = main.app.test_client()

    response = client.get(path)

    assert response.status_code == 405
    assert "error" in response.get_json()
    assert "POST" in response.headers["Allow"]


def test_an_unknown_api_path_answers_with_a_json_404():
    client = main.app.test_client()

    response = client.get("/api/nope")

    assert response.status_code == 404
    assert "error" in response.get_json()


def test_an_unknown_page_keeps_the_default_html_404():
    client = main.app.test_client()

    response = client.get("/nope")

    assert response.status_code == 404
    assert response.get_json(silent=True) is None


# ---- /api/action ----


def action_client():
    engine = Engine(equipment=[GasCompressor(), CentrifugalPump(), ControlValve()])
    log = ActionLog()
    app = Flask(__name__)
    validate.install(app, unlimited)
    app.register_blueprint(create_action_blueprint(lambda: engine, lambda: log))

    return app.test_client(), engine, log


ACTION_BODIES = [
    pytest.param("{nope", "request body must be a JSON object", id="malformed"),
    pytest.param("[]", "request body must be a JSON object", id="array"),
    pytest.param("{}", "target and action must be strings", id="missing"),
    pytest.param('{"target": 1, "action": "start"}', "target and action must be strings", id="int-target"),
    pytest.param('{"target": "K-101", "action": ["start"]}', "target and action must be strings", id="list-action"),
    pytest.param('{"target": "K-101", "action": "start", "x": 1}', "unknown field: x", id="extra-key"),
    pytest.param('{"target": "X-999", "action": "start"}', "no device registered", id="unknown-target"),
    pytest.param('{"target": "K-101", "action": "explode"}', "allows only", id="unknown-action"),
    pytest.param('{"target": "K-101", "action": "set_load_target"}', "requires a value", id="missing-value"),
    pytest.param('{"target": "K-101", "action": "set_load_target", "value": "1"}', "must be a number", id="string-value"),
    pytest.param('{"target": "K-101", "action": "set_load_target", "value": true}', "must be a number", id="bool-value"),
    pytest.param('{"target": "K-101", "action": "set_load_target", "value": NaN}', "must be finite", id="nan-value"),
    pytest.param('{"target": "K-101", "action": "set_load_target", "value": 1e30}', "must be within", id="huge-value"),
    pytest.param('{"target": "K-101", "action": "set_load_target", "value": 1' + "0" * 400 + "}", "must be within", id="huge-int-literal"),
    pytest.param('{"target": "K-101", "action": "start", "value": 1}', "takes no value", id="value-on-valueless"),
]


@pytest.mark.parametrize("body, message", ACTION_BODIES)
def test_action_answers_a_bad_body_with_a_4xx_and_logs_nothing(body, message):
    client, _engine, log = action_client()

    response = raw(client, "/api/action", body)

    assert response.status_code == 400
    assert message in response.get_json()["error"]
    assert len(log) == 0


def test_action_refuses_an_oversized_body_with_413():
    client, _engine, _log = action_client()
    padding = "x" * (config.API_MAX_BODY_BYTES + 1)

    response = raw(client, "/api/action", f'{{"target": "{padding}", "action": "start"}}')

    assert response.status_code == 413


# ---- /api/alarms/acknowledge ----


def alarm_client():
    app = Flask(__name__)
    validate.install(app, unlimited)
    app.register_blueprint(create_alarm_blueprint(
            lambda: (), lambda alarm_id: Acknowledged.UNKNOWN
        ))

    return app.test_client()


ALARM_BODIES = [
    pytest.param("{nope", "request body must be a JSON object", id="malformed"),
    pytest.param("[]", "request body must be a JSON object", id="array"),
    pytest.param("{}", "alarm_id must be a string", id="missing"),
    pytest.param('{"alarm_id": 7}', "alarm_id must be a string", id="int"),
    pytest.param('{"alarm_id": null}', "alarm_id must be a string", id="null"),
    pytest.param('{"alarm_id": "a", "x": 1}', "unknown field: x", id="extra-key"),
]


@pytest.mark.parametrize("body, message", ALARM_BODIES)
def test_alarm_acknowledge_answers_a_bad_body_with_a_4xx(body, message):
    response = raw(alarm_client(), "/api/alarms/acknowledge", body)

    assert response.status_code == 400
    assert response.get_json()["error"] == message


# ---- /api/scenario/load ----


@pytest.fixture
def scenario_client(tmp_path):
    runner = ScenarioRunner(ScenarioLibrary(scenarios=tmp_path))
    app = Flask(__name__)
    validate.install(app, unlimited)
    app.register_blueprint(create_scenario_blueprint(lambda: runner))

    return app.test_client()


SCENARIO_BODIES = [
    pytest.param("{nope", id="malformed"),
    pytest.param("[]", id="array"),
    pytest.param("{}", id="missing"),
    pytest.param('{"scenario": 7}', id="int"),
    pytest.param('{"scenario": null}', id="null"),
]


@pytest.mark.parametrize("body", SCENARIO_BODIES)
def test_scenario_load_answers_a_bad_body_with_a_4xx(scenario_client, body):
    response = raw(scenario_client, "/api/scenario/load", body)

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_scenario_load_rejects_an_extra_key(scenario_client):
    response = scenario_client.post("/api/scenario/load", json={"scenario": "x", "extra": 1})

    assert response.status_code == 400
    assert response.get_json()["error"] == "unknown field: extra"


# ---- the field readers ----


def test_check_number_returns_a_float_for_an_int():
    value = validate.check_number(3, "x")

    assert value == pytest.approx(3.0)
    assert isinstance(value, float)


@pytest.mark.parametrize("value", [math.nan, math.inf, True, "1", None, config.API_MAX_MAGNITUDE * 2, 10**400, -(10**400)])
def test_check_number_refuses_what_is_not_a_usable_number(value):
    with Flask(__name__).app_context():
        result = validate.check_number(value, "x")

    assert isinstance(result, tuple)
    assert result[1] == 400


# ---- rate limiting ----


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_the_limiter_grants_a_burst_then_refuses_with_a_wait():
    clock = FakeClock()
    limiter = validate.RateLimiter(rate=2.0, burst=3.0, clock=clock)

    grants = [limiter.acquire("a") for _ in range(3)]
    wait = limiter.acquire("a")

    assert grants == [0.0, 0.0, 0.0]
    assert wait == pytest.approx(0.5)


def test_the_limiter_refills_at_its_rate_and_never_beyond_the_burst():
    clock = FakeClock()
    limiter = validate.RateLimiter(rate=2.0, burst=3.0, clock=clock)
    for _ in range(3):
        limiter.acquire("a")

    clock.now += 0.5
    after_half_a_second = [limiter.acquire("a"), limiter.acquire("a")]
    clock.now += 1000.0
    after_a_long_idle = [limiter.acquire("a") for _ in range(4)]

    assert after_half_a_second[0] == 0.0
    assert after_half_a_second[1] > 0.0
    assert after_a_long_idle[:3] == [0.0, 0.0, 0.0]
    assert after_a_long_idle[3] > 0.0


def test_the_limiter_keeps_clients_apart():
    limiter = validate.RateLimiter(rate=1.0, burst=1.0, clock=FakeClock())

    assert limiter.acquire("a") == 0.0
    assert limiter.acquire("a") > 0.0
    assert limiter.acquire("b") == 0.0


def test_the_limiter_bounds_how_many_clients_it_remembers():
    clock = FakeClock()
    limiter = validate.RateLimiter(rate=1.0, burst=2.0, clock=clock, max_clients=3)

    for index in range(10):
        limiter.acquire(f"client-{index}")

    assert len(limiter._buckets) <= 3


@pytest.mark.parametrize("rate, burst, max_clients", [(0, 1, 1), (-1, 1, 1), (1, 0.5, 1), (1, 1, 0)])
def test_the_limiter_refuses_nonsense_settings(rate, burst, max_clients):
    with pytest.raises(ValueError):
        validate.RateLimiter(rate, burst, max_clients=max_clients)


def limited_client(clock, burst=2.0):
    app = Flask(__name__)
    limiter = validate.RateLimiter(rate=1.0, burst=burst, clock=clock)
    validate.install(app, lambda: limiter)

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    return app.test_client()


def test_a_client_over_its_rate_gets_a_429_with_retry_after():
    client = limited_client(FakeClock())

    statuses = [client.get("/api/ping").status_code for _ in range(3)]
    refused = client.get("/api/ping")

    assert statuses == [200, 200, 429]
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "1"
    assert "rate limit exceeded" in refused.get_json()["error"]


def test_a_refused_client_is_served_again_once_the_bucket_refills():
    clock = FakeClock()
    client = limited_client(clock, burst=1.0)
    client.get("/api/ping")
    assert client.get("/api/ping").status_code == 429

    clock.now += 1.0

    assert client.get("/api/ping").status_code == 200


def test_static_files_are_not_rate_limited():
    app = Flask(__name__, static_folder=str(Path(__file__).parent), static_url_path="/static")
    validate.install(app, lambda: validate.RateLimiter(rate=1.0, burst=1.0, clock=FakeClock()))
    client = app.test_client()

    statuses = {client.get("/static/test_api_validation.py").status_code for _ in range(5)}

    assert statuses == {200}


def test_the_live_app_rate_limits_and_refuses_before_creating_a_session(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(main, "rate_limiter", validate.RateLimiter(rate=1.0, burst=1.0, clock=clock))
    created = []
    real_create = main.sessions.create
    monkeypatch.setattr(main.sessions, "create", lambda sid: created.append(sid) or real_create(sid))
    client = main.app.test_client()

    first = client.get("/api/state")
    second = client.get("/api/state")

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(created) == 1
