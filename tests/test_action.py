import pytest
from flask import Flask

from app.api.action import UnknownAction, apply_action, create_action_blueprint
from app.engine.engine import Engine
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.scoring.actionlog import ActionLog


# ---- apply_action: the pure core, no Flask involved ----


def test_apply_action_calls_a_zero_arg_action_and_logs_it():
    compressor = GasCompressor()
    log = ActionLog()

    apply_action({"K-101": compressor}, log, sim_time=5.0, target="K-101", action="start", value=None)

    assert compressor.running is True
    assert len(log) == 1
    assert log.events[0].sim_time == pytest.approx(5.0)
    assert log.events[0].data == {"action": "start", "value": None}


def test_apply_action_calls_a_value_action_and_logs_it():
    compressor = GasCompressor()
    log = ActionLog()

    apply_action(
        {"K-101": compressor}, log, sim_time=1.0,
        target="K-101", action="set_load_target", value=0.6,
    )

    assert compressor.load_target == pytest.approx(0.6)
    assert log.events[0].data["action"] == "set_load_target"
    assert log.events[0].data["value"] == pytest.approx(0.6)


def test_apply_action_on_pump_and_valve():
    pump = CentrifugalPump()
    valve = ControlValve()
    log = ActionLog()
    equipment = {"P-101": pump, "FV-101": valve}

    apply_action(equipment, log, 0.0, "P-101", "start", None)
    apply_action(equipment, log, 0.0, "P-101", "set_speed_target", 0.75)
    apply_action(equipment, log, 0.0, "FV-101", "set_position_target", 0.3)

    assert pump.running is True
    assert pump.speed_target == pytest.approx(0.75)
    assert valve.position_target == pytest.approx(0.3)
    assert len(log) == 3


def test_apply_action_unknown_target_raises_key_error():
    log = ActionLog()

    with pytest.raises(KeyError):
        apply_action({}, log, 0.0, "K-101", "start", None)

    assert len(log) == 0


def test_apply_action_unknown_action_raises_unknown_action():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(UnknownAction):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_position_target", 0.5)

    assert len(log) == 0


def test_apply_action_missing_value_for_a_value_action_raises_value_error():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(ValueError):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_load_target", None)

    assert len(log) == 0


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), True],
    ids=["nan", "inf", "bool"],
)
def test_apply_action_rejects_a_bad_value(value):
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(ValueError):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_load_target", value)

    assert len(log) == 0


def test_apply_action_a_value_given_to_a_zero_arg_action_raises_value_error():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(ValueError):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "start", 1.0)

    assert len(log) == 0
    assert compressor.running is False


def test_apply_action_a_failed_call_logs_nothing():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(UnknownAction):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_speed_target", 0.5)

    assert len(log) == 0


# ---- the Flask blueprint: POST /api/action end to end ----


def build_app():
    engine = Engine(equipment=[GasCompressor(), CentrifugalPump(), ControlValve()])
    log = ActionLog()

    app = Flask(__name__)
    app.register_blueprint(create_action_blueprint(lambda: engine, lambda: log))

    return app, engine, log


def test_post_action_start_succeeds_and_advances_the_log():
    app, engine, log = build_app()
    client = app.test_client()

    response = client.post("/api/action", json={"target": "K-101", "action": "start", "value": None})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert engine.equipment["K-101"].running is True
    assert len(log) == 1


def test_post_action_records_sim_time_from_the_engine_clock_not_wall_time():
    app, engine, log = build_app()
    client = app.test_client()
    engine.clock.sim_time = 42.0

    client.post("/api/action", json={"target": "K-101", "action": "start", "value": None})

    assert log.events[0].sim_time == pytest.approx(42.0)


def test_post_action_with_a_value_applies_it():
    app, engine, _log = build_app()
    client = app.test_client()

    response = client.post(
        "/api/action",
        json={"target": "FV-101", "action": "set_position_target", "value": 0.25},
    )

    assert response.status_code == 200
    assert engine.equipment["FV-101"].position_target == pytest.approx(0.25)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-fields"),
        pytest.param({"target": "K-101"}, id="missing-action"),
        pytest.param({"target": 1, "action": "start"}, id="non-string-target"),
        pytest.param({"target": "K-101", "action": "start", "value": "abc"}, id="string-value"),
        pytest.param({"target": "K-101", "action": "start", "value": True}, id="bool-value"),
    ],
)
def test_post_action_rejects_malformed_bodies_without_touching_the_plant(body):
    app, engine, log = build_app()
    client = app.test_client()

    response = client.post("/api/action", json=body)

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert engine.equipment["K-101"].running is False
    assert len(log) == 0


def test_post_action_rejects_a_raw_nan_literal_value():
    app, engine, log = build_app()
    client = app.test_client()

    response = client.post(
        "/api/action",
        data='{"target": "K-101", "action": "start", "value": NaN}',
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert engine.equipment["K-101"].running is False
    assert len(log) == 0


def test_post_action_rejects_a_string_value_for_a_value_taking_action():
    # The handler does only shape validation (target/action are strings);
    # value correctness is apply_action's job alone, exercised here through
    # the full HTTP path rather than just the pure function.
    app, engine, log = build_app()
    client = app.test_client()

    response = client.post(
        "/api/action",
        json={"target": "K-101", "action": "set_load_target", "value": "abc"},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert engine.equipment["K-101"].load_target == pytest.approx(0.0)
    assert len(log) == 0


def test_post_action_non_json_body_is_rejected():
    app, _engine, _log = build_app()
    client = app.test_client()

    response = client.post("/api/action", data="not json", content_type="text/plain")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_post_action_unknown_target_is_a_400_not_a_500():
    app, _engine, log = build_app()
    client = app.test_client()

    response = client.post("/api/action", json={"target": "Z-999", "action": "start", "value": None})

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert len(log) == 0


def test_post_action_disallowed_action_is_a_400_not_a_500():
    app, engine, log = build_app()
    client = app.test_client()

    response = client.post(
        "/api/action",
        json={"target": "K-101", "action": "set_position_target", "value": 0.5},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert len(log) == 0


def test_post_action_every_action_type_reaches_the_log():
    app, engine, log = build_app()
    client = app.test_client()

    client.post("/api/action", json={"target": "K-101", "action": "start", "value": None})
    client.post("/api/action", json={"target": "K-101", "action": "set_load_target", "value": 0.4})
    client.post("/api/action", json={"target": "P-101", "action": "start", "value": None})
    client.post("/api/action", json={"target": "P-101", "action": "set_speed_target", "value": 0.9})
    client.post("/api/action", json={"target": "FV-101", "action": "set_position_target", "value": 0.6})
    client.post("/api/action", json={"target": "K-101", "action": "stop", "value": None})

    actions = [event.data["action"] for event in log.events]
    assert actions == [
        "start",
        "set_load_target",
        "start",
        "set_speed_target",
        "set_position_target",
        "stop",
    ]
