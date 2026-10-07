import pytest
from flask import Flask

from app.api.action import UnknownAction, apply_action, create_action_blueprint
from app.engine.engine import Engine
from app.engine.persistence import capture_state
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.plant.loader import load_plant
from app.scenarios.replay import Recording, replay
from app.scenarios.runner import Phase, ScenarioRunner
from app.scoring.actionlog import ActionLog
from app.training.session import TrainingSession


# ---- apply_action: the pure core, no Flask involved ----


def test_apply_action_calls_a_zero_arg_action_and_logs_it():
    compressor = GasCompressor()
    log = ActionLog()

    apply_action({"K-101": compressor}, log, sim_time=5.0, target="K-101", action="start", value=None, loops={})

    assert compressor.running is True
    assert len(log) == 1
    assert log.events[0].sim_time == pytest.approx(5.0)
    assert log.events[0].data == {"action": "start", "value": None}


def test_apply_action_calls_a_value_action_and_logs_it():
    compressor = GasCompressor()
    log = ActionLog()

    apply_action(
        {"K-101": compressor}, log, sim_time=1.0,
        target="K-101", action="set_load_target", value=0.6, loops={},
    )

    assert compressor.load_target == pytest.approx(0.6)
    assert log.events[0].data["action"] == "set_load_target"
    assert log.events[0].data["value"] == pytest.approx(0.6)


def test_apply_action_logs_an_int_value_as_a_float():
    # A JSON body's {"value": 1} decodes to a Python int; the log must not
    # let that leak through as a type the client happened to spell with no
    # decimal point - it should agree with what a float literal would log.
    compressor = GasCompressor()
    log = ActionLog()

    apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_load_target", 1, loops={})

    assert isinstance(log.events[0].data["value"], float)
    assert log.events[0].data["value"] == pytest.approx(1.0)


def test_apply_action_on_pump_and_valve():
    pump = CentrifugalPump()
    valve = ControlValve()
    log = ActionLog()
    equipment = {"P-101": pump, "FV-101": valve}

    apply_action(equipment, log, 0.0, "P-101", "start", None, loops={})
    apply_action(equipment, log, 0.0, "P-101", "set_speed_target", 0.75, loops={})
    apply_action(equipment, log, 0.0, "FV-101", "set_position_target", 0.3, loops={})

    assert pump.running is True
    assert pump.speed_target == pytest.approx(0.75)
    assert valve.position_target == pytest.approx(0.3)
    assert len(log) == 3


def test_apply_action_unknown_target_raises_key_error():
    log = ActionLog()

    with pytest.raises(KeyError):
        apply_action({}, log, 0.0, "K-101", "start", None, loops={})

    assert len(log) == 0


def test_apply_action_unknown_action_raises_unknown_action():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(UnknownAction):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_position_target", 0.5, loops={})

    assert len(log) == 0


def test_apply_action_missing_value_for_a_value_action_raises_value_error():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(ValueError):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_load_target", None, loops={})

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
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_load_target", value, loops={})

    assert len(log) == 0


def test_apply_action_a_value_given_to_a_zero_arg_action_raises_value_error():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(ValueError):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "start", 1.0, loops={})

    assert len(log) == 0
    assert compressor.running is False


def test_apply_action_a_failed_call_logs_nothing():
    compressor = GasCompressor()
    log = ActionLog()

    with pytest.raises(UnknownAction):
        apply_action({"K-101": compressor}, log, 0.0, "K-101", "set_speed_target", 0.5, loops={})

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


def test_post_action_with_a_json_integer_value_logs_a_float():
    app, engine, log = build_app()
    client = app.test_client()

    response = client.post(
        "/api/action",
        json={"target": "K-101", "action": "set_load_target", "value": 1},
    )

    assert response.status_code == 200
    assert isinstance(log.events[0].data["value"], float)


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


# ---- loops as targets (T16-14) ----
#
# One pressure loop: PIC-101 holds N-02 at 60 psia by throttling PV-101 from
# a 100 psia supply; HV-101 drains N-02 to a 20 psia header. At 0.5 travel
# each valve takes half the 80 psi, so the loop starts settled at setpoint.

SETPOINT = 60.0
KP = 0.005
KI = 0.005


def loop_plant(mode="AUTO", tunable=True):
    def valve(tag, node_in, node_out):
        return {
            "tag": tag, "type": "control_valve", "node_in": node_in, "node_out": node_out,
            "design": {
                "capacity": 20.0, "flow_characteristic": "linear", "stroke_rate": 0.05,
                "position": 0.5, "position_target": 0.5,
            },
        }

    entry = {
        "tag": "PIC-101", "pv": "N-02", "sp": SETPOINT, "out": "PV-101", "mode": mode,
        "kp": KP, "ki": KI, "kd": 0.0,
    }
    if tunable:
        entry["tunable"] = True

    engine = Engine.from_plant(
        load_plant(
            {
                "nodes": [
                    {"id": "N-01", "boundary": True, "pressure": 100.0},
                    {"id": "N-02", "boundary": False, "pressure": SETPOINT},
                    {"id": "N-03", "boundary": True, "pressure": 20.0},
                ],
                "equipment": [valve("PV-101", "N-01", "N-02"), valve("HV-101", "N-02", "N-03")],
                "controllers": [entry],
            },
        ),
    )
    engine.start()

    return engine


def act(engine, log, target, action, value=None):
    apply_action(engine.equipment, log, engine.clock.sim_time, target, action, value, loops=engine.loops)


def test_manual_and_auto_switch_the_loop_mode_and_are_logged():
    engine = loop_plant(mode="AUTO")
    log = ActionLog()

    act(engine, log, "PIC-101", "manual")
    assert engine.snapshot().controllers["PIC-101"]["mode"] == "MANUAL"

    act(engine, log, "PIC-101", "auto")
    assert engine.snapshot().controllers["PIC-101"]["mode"] == "AUTO"

    assert [(event.tag, event.data) for event in log.events] == [
        ("PIC-101", {"action": "manual", "value": None}),
        ("PIC-101", {"action": "auto", "value": None}),
    ]


def test_set_setpoint_moves_the_setpoint():
    engine = loop_plant()
    log = ActionLog()

    act(engine, log, "PIC-101", "set_setpoint", 55)

    assert engine.snapshot().controllers["PIC-101"]["sp"] == 55.0
    assert log.events[0].data == {"action": "set_setpoint", "value": 55.0}


def test_set_output_in_manual_drives_the_loops_valve():
    engine = loop_plant(mode="MANUAL")
    log = ActionLog()

    act(engine, log, "PIC-101", "set_output", 0.8)
    engine.step(1.0)

    assert engine.snapshot().controllers["PIC-101"]["out"] == pytest.approx(0.8)
    assert engine.equipment["PV-101"].position_target == pytest.approx(0.8)


@pytest.mark.parametrize("value", [1.0001, -0.0001, 2.0], ids=["above-max", "below-min", "far-above"])
def test_set_output_outside_the_output_range_is_refused(value):
    engine = loop_plant(mode="MANUAL")
    log = ActionLog()
    before = engine.loops["PIC-101"].loop.checkpoint()

    with pytest.raises(ValueError, match="output range"):
        act(engine, log, "PIC-101", "set_output", value)

    assert engine.loops["PIC-101"].loop.checkpoint() == before
    assert len(log) == 0


@pytest.mark.parametrize("value", [0.0, 1.0], ids=["min", "max"])
def test_set_output_takes_either_end_of_the_range(value):
    engine = loop_plant(mode="MANUAL")

    act(engine, ActionLog(), "PIC-101", "set_output", value)

    assert engine.loops["PIC-101"].loop.manual_output == value


def test_set_output_in_auto_is_refused():
    engine = loop_plant(mode="AUTO")
    log = ActionLog()
    before = engine.loops["PIC-101"].loop.checkpoint()

    with pytest.raises(ValueError, match="MANUAL"):
        act(engine, log, "PIC-101", "set_output", 0.6)

    assert engine.loops["PIC-101"].loop.checkpoint() == before
    assert len(log) == 0


@pytest.mark.parametrize("action, gain", [("set_kp", "kp"), ("set_ki", "ki"), ("set_kd", "kd")])
def test_tuning_sets_its_gain_on_a_tunable_loop(action, gain):
    engine = loop_plant(tunable=True)

    act(engine, ActionLog(), "PIC-101", action, 0.02)

    assert engine.snapshot().controllers["PIC-101"][gain] == 0.02


@pytest.mark.parametrize("action", ["set_kp", "set_ki", "set_kd"])
def test_tuning_a_loop_that_is_not_tunable_is_refused(action):
    engine = loop_plant(tunable=False)
    log = ActionLog()
    before = engine.loops["PIC-101"].loop.checkpoint()

    with pytest.raises(UnknownAction, match="not open to operator tuning"):
        act(engine, log, "PIC-101", action, 0.02)

    assert engine.loops["PIC-101"].loop.checkpoint() == before
    assert len(log) == 0


def test_a_negative_gain_is_refused():
    engine = loop_plant(tunable=True)
    log = ActionLog()

    with pytest.raises(ValueError, match="non-negative"):
        act(engine, log, "PIC-101", "set_kp", -0.01)

    assert engine.snapshot().controllers["PIC-101"]["kp"] == KP
    assert len(log) == 0


def test_a_retune_in_auto_does_not_bump_the_output():
    # Disturb the supply so the loop is working against a real error, then
    # change Kp and Ki on one of three identical plants through the action and
    # on another by bare assignment. Against the untouched plant, the retuned
    # output may move only by what the new gains do with the error from here
    # on - the change in error times the change in Kp, plus one step of the
    # extra integral action - and never by a jump at the moment of the change.
    # The bare assignment, which skips the integral re-solve, jumps.
    plants = [loop_plant(mode="AUTO") for _ in range(3)]
    for engine in plants:
        engine.topology.nodes["N-01"].set_boundary_pressure(80.0)
        published = [engine.step(1.0) for _ in range(20)]  # identical on all three
    retuned, untouched, bare = plants

    act(retuned, ActionLog(), "PIC-101", "set_kp", 3 * KP)
    act(retuned, ActionLog(), "PIC-101", "set_ki", 5 * KI)
    bare.loops["PIC-101"].loop.pid.kp = 3 * KP
    bare.loops["PIC-101"].loop.pid.ki = 5 * KI

    # Control reads the pv the previous step published, so the next step acts
    # on the last snapshot and the retune held the error of the one before.
    # PIC-101 is reverse-acting: error = sp - pv.
    error_then = SETPOINT - published[-2].controllers["PIC-101"]["pv"]
    error_now = SETPOINT - published[-1].controllers["PIC-101"]["pv"]
    shift = (3 * KP - KP) * (error_now - error_then) + (5 * KI - KI) * error_now * 1.0

    retuned_out, untouched_out, bare_out = (engine.step(1.0).controllers["PIC-101"]["out"] for engine in plants)

    assert retuned_out - untouched_out == pytest.approx(shift, rel=1e-9, abs=1e-12)
    assert abs(bare_out - untouched_out) > 10 * abs(shift)


def test_an_unknown_loop_action_is_refused():
    engine = loop_plant()
    log = ActionLog()

    with pytest.raises(UnknownAction, match="is a loop"):
        act(engine, log, "PIC-101", "cascade")

    assert len(log) == 0


def test_a_mode_action_takes_no_value():
    engine = loop_plant(mode="AUTO")

    with pytest.raises(ValueError, match="takes no value"):
        act(engine, ActionLog(), "PIC-101", "manual", 1.0)

    assert engine.snapshot().controllers["PIC-101"]["mode"] == "AUTO"


def test_a_device_a_loop_drives_refuses_a_direct_action_naming_the_loop():
    engine = loop_plant(mode="MANUAL")
    log = ActionLog()

    with pytest.raises(UnknownAction, match="PV-101 is driven by loop PIC-101"):
        act(engine, log, "PV-101", "set_position_target", 0.9)

    assert engine.equipment["PV-101"].position_target == pytest.approx(0.5)
    assert len(log) == 0


def test_a_device_no_loop_drives_still_takes_its_action():
    engine = loop_plant()

    act(engine, ActionLog(), "HV-101", "set_position_target", 0.3)

    assert engine.equipment["HV-101"].position_target == pytest.approx(0.3)


def test_an_unknown_target_names_the_loops_too():
    engine = loop_plant()

    with pytest.raises(KeyError, match="PIC-101"):
        act(engine, ActionLog(), "X-999", "start")


# ---- end to end: POST /api/action on a training session ----


@pytest.fixture
def session():
    session = TrainingSession()

    yield session

    session.end()


def post(client, target, action, value=None):
    return client.post("/api/action", json={"target": target, "action": action, "value": value})


def test_each_loop_action_reaches_pic_101_through_the_endpoint_and_shows_in_the_next_snapshot(session):
    app = Flask(__name__)
    app.register_blueprint(create_action_blueprint(apply=session.act))
    client = app.test_client()
    scheduler = session.training_scheduler

    def row():
        return scheduler.step_once().controllers["PIC-101"]

    assert post(client, "PIC-101", "manual").status_code == 200
    assert row()["mode"] == "MANUAL"

    assert post(client, "PIC-101", "set_output", 0.7).status_code == 200
    assert row()["out"] == pytest.approx(0.7)

    assert post(client, "PIC-101", "set_setpoint", 190.0).status_code == 200
    assert row()["sp"] == 190.0

    assert post(client, "PIC-101", "set_kp", 0.02).status_code == 200
    assert row()["kp"] == 0.02

    assert post(client, "PIC-101", "auto").status_code == 200
    assert row()["mode"] == "AUTO"

    refused = post(client, "PIC-101", "set_output", 0.4)
    assert refused.status_code == 400
    assert "MANUAL" in refused.get_json()["error"]

    refused = post(client, "PV-101", "set_position_target", 0.4)
    assert refused.status_code == 400
    assert "PIC-101" in refused.get_json()["error"]

    assert [event.tag for event in session.free.actions] == ["PIC-101"] * 5


def test_a_scenario_run_with_loop_actions_replays_bit_for_bit():
    runner = ScenarioRunner()
    runner.load_config(
        {
            "id": "loop-actions",
            "plant": "olefins_lite",
            "initial_condition": {"condition": "normal_operation"},
            "objectives": [{"id": "hold", "success": {"condition": "V-101.level >= 0.0", "hold_duration_s": 60}}],
            "time_limit_s": 120,
            "difficulty": "easy",
            "seed": 3,
        },
    )
    runner.start()

    def steps(count):
        for _ in range(count):
            runner.step(1.0)

    steps(5)
    runner.act("PIC-101", "manual", None)
    runner.act("PIC-101", "set_output", 0.6)
    steps(5)
    runner.act("PIC-101", "set_kp", 0.02)
    runner.act("PIC-101", "set_setpoint", 195.0)
    runner.act("PIC-101", "auto", None)
    while runner.phase is Phase.RUNNING:
        runner.step(1.0)

    replayed = replay(Recording.of(runner))

    assert capture_state(replayed.engine) == capture_state(runner.engine)
    assert replayed.snapshot().as_dict() == runner.snapshot().as_dict()
    assert replayed.result().as_dict() == runner.result().as_dict()
