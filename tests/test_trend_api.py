"""Trend API - T17-3, contract C5's trend routes."""

from pathlib import Path

import pytest
import yaml
from flask import Flask

from app import config, main
from app.api.trend import create_trend_blueprint
from app.historian.buffer import Historian
from app.scenarios.runner import ScenarioLibrary
from app.training.session import TrainingSession
from tests.test_training_session import SCENARIO, scheduler_workers


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit", "ignore:interlock")

POINT = "V-101.level"


def fake_app(histories, limits=None):
    """The blueprint over hand-fed histories, to pin the route's own rules."""

    def get_history(points):
        return {point: histories[point] for point in points}

    app = Flask(__name__)
    app.register_blueprint(create_trend_blueprint(lambda: sorted(histories), get_history, lambda: limits or {}))

    return app.test_client()


def feed(values, period=1.0):
    historian = Historian(10_000)

    for index, value in enumerate(values):
        historian.record("X.v", index * period, value)

    return historian.history("X.v")


def rows(response, point="X.v"):
    return response.get_json()[point]


def test_a_range_returns_exactly_the_samples_inside_it_inclusive():
    client = fake_app({"X.v": feed(range(100))})

    body = rows(client.get("/api/trend?tags=X.v&from=10&to=19.5"))

    assert body == [[float(t), float(t)] for t in range(10, 20)]


def test_no_range_returns_everything_retained():
    client = fake_app({"X.v": feed(range(50))})

    assert len(rows(client.get("/api/trend?tags=X.v"))) == 50


@pytest.mark.parametrize(
    "query, expected",
    [
        ("from=-100&to=4", [0.0, 1.0, 2.0, 3.0, 4.0]),
        ("from=45&to=9999", [45.0, 46.0, 47.0, 48.0, 49.0]),
        ("from=500&to=900", []),
        ("from=-9&to=-1", []),
        ("from=2.5&to=2.6", []),
    ],
)
def test_a_range_outside_the_buffer_returns_what_exists_not_an_error(query, expected):
    client = fake_app({"X.v": feed(range(50))})

    response = client.get(f"/api/trend?tags=X.v&{query}")

    assert response.status_code == 200
    assert [t for t, _ in rows(response)] == expected


def test_a_range_before_the_oldest_retained_sample_after_eviction_returns_what_is_left():
    historian = Historian(10)

    for index in range(25):
        historian.record("X.v", float(index), float(index))

    client = fake_app({"X.v": historian.history("X.v")})

    body = rows(client.get("/api/trend?tags=X.v&from=0&to=17"))

    assert [t for t, _ in body] == [15.0, 16.0, 17.0]


def test_a_large_request_is_bounded_by_max_points_and_keeps_the_spike():
    values = [0.0] * 5000
    values[3333] = 99.0
    client = fake_app({"X.v": feed(values)})

    body = rows(client.get("/api/trend?tags=X.v&max_points=100"))

    assert len(body) == 100
    assert [3333.0, 99.0] in body
    assert [t for t, _ in body] == sorted(t for t, _ in body)


def test_the_default_bound_applies_when_max_points_is_absent():
    client = fake_app({"X.v": feed(range(5000))})

    assert len(rows(client.get("/api/trend?tags=X.v"))) == config.TREND_DEFAULT_POINTS


def test_the_range_is_applied_before_decimation():
    client = fake_app({"X.v": feed(range(5000))})

    body = rows(client.get("/api/trend?tags=X.v&from=100&to=299&max_points=1000"))

    assert len(body) == 200


def test_several_tags_come_back_keyed_each_decimated_on_its_own():
    client = fake_app({"A.v": feed(range(100)), "B.v": feed(range(100, 300))})

    body = client.get("/api/trend?tags=A.v,B.v,A.v&max_points=20").get_json()

    assert list(body) == ["A.v", "B.v"]
    assert len(body["A.v"]) == len(body["B.v"]) == 20


def test_a_non_finite_reading_is_written_as_null():
    client = fake_app({"X.v": feed([1.0, float("nan"), float("inf"), 4.0])})

    response = client.get("/api/trend?tags=X.v")

    assert response.status_code == 200
    assert [v for _, v in rows(response)] == [1.0, None, None, 4.0]


@pytest.mark.parametrize(
    "query, fragment",
    [
        ("", "tags is required"),
        ("tags=", "tags is required"),
        ("tags=,,", "tags is required"),
        ("tags=Z.v", "unknown trend point: 'Z.v'"),
        ("tags=X.v,Z.v", "unknown trend point: 'Z.v'"),
        ("tags=X.v&from=abc", "from must be a number"),
        ("tags=X.v&to=nan", "to must be finite"),
        ("tags=X.v&from=inf", "from must be finite"),
        ("tags=X.v&from=5&to=4", "from must not be after to"),
        ("tags=X.v&max_points=1", "max_points must be from 2"),
        ("tags=X.v&max_points=0", "max_points must be from 2"),
        ("tags=X.v&max_points=-5", "max_points must be from 2"),
        (f"tags=X.v&max_points={config.TREND_MAX_POINTS + 1}", "max_points must be from 2"),
        ("tags=X.v&max_points=2.5", "max_points must be an integer"),
        ("tags=X.v&max_points=lots", "max_points must be an integer"),
    ],
)
def test_a_bad_request_is_a_400_naming_the_problem(query, fragment):
    client = fake_app({"X.v": feed(range(10))})

    response = client.get(f"/api/trend?{query}")

    assert response.status_code == 400
    assert fragment in response.get_json()["error"]


def test_the_largest_allowed_max_points_is_accepted():
    client = fake_app({"X.v": feed(range(10))})

    assert client.get(f"/api/trend?tags=X.v&max_points={config.TREND_MAX_POINTS}").status_code == 200
    assert client.get("/api/trend?tags=X.v&max_points=2").status_code == 200


def test_more_tags_than_the_limit_is_refused_not_trimmed():
    histories = {f"T{i}.v": feed(range(3)) for i in range(config.TREND_MAX_TAGS + 1)}
    client = fake_app(histories)

    allowed = ",".join(sorted(histories)[: config.TREND_MAX_TAGS])
    over = ",".join(sorted(histories))

    assert client.get(f"/api/trend?tags={allowed}").status_code == 200
    assert client.get(f"/api/trend?tags={over}").status_code == 400


def test_points_lists_what_the_plant_publishes():
    client = fake_app({"B.v": feed(range(3)), "A.v": feed(range(3))})

    body = client.get("/api/trend/points").get_json()

    assert body["points"] == ["A.v", "B.v"]
    assert body["max_tags"] == config.TREND_MAX_TAGS
    assert body["max_points"] == config.TREND_MAX_POINTS


def test_points_carry_the_limits_the_plant_reports():
    limits = {"A.v": {"warning_hi": 0.8, "trip_hi": 0.9}}
    client = fake_app({"A.v": feed(range(3))}, limits)

    assert client.get("/api/trend/points").get_json()["limits"] == limits


def test_a_point_that_vanishes_between_the_two_lookups_is_a_400():
    def get_history(points):
        raise KeyError("X.v")

    app = Flask(__name__)
    app.register_blueprint(create_trend_blueprint(lambda: ["X.v"], get_history, lambda: {}))

    response = app.test_client().get("/api/trend?tags=X.v")

    assert response.status_code == 400
    assert "unknown trend point: 'X.v'" in response.get_json()["error"]


# Through a real session: samples come from the plant's own steps.


@pytest.fixture
def session(tmp_path):
    (tmp_path / "pump-trip.yaml").write_text(SCENARIO)
    made = TrainingSession(ScenarioLibrary(scenarios=tmp_path))

    yield made

    made.end()


@pytest.fixture
def client(session):
    app = Flask(__name__)
    app.register_blueprint(create_trend_blueprint(session.trend_points, session.trend_history, session.trend_limits))

    return app.test_client()


def step(session, count):
    for _ in range(count):
        assert session.training_scheduler.step_once() is not None


def times(client, query=""):
    return [t for t, _ in client.get(f"/api/trend?tags={POINT}{query}").get_json()[POINT]]


def test_free_play_records_every_step_at_the_engine_clock(session, client):
    start = session.training_scheduler.snapshot().sim_time

    step(session, 10)

    assert times(client) == pytest.approx([start + i for i in range(11)])


def test_the_latest_sample_is_the_published_value(session, client):
    step(session, 4)

    body = client.get(f"/api/trend?tags={POINT}").get_json()[POINT]

    assert body[-1][1] == pytest.approx(session.training_scheduler.snapshot().equipment["V-101"]["level"])


def test_points_include_nodes_streams_and_controllers_and_exclude_hidden_fields(client):
    points = client.get("/api/trend/points").get_json()["points"]

    assert {POINT, "N-101.pressure", "B-P-101.flow", "PIC-101.pv"} <= set(points)
    assert "P-101.running" not in points
    assert not [point for point in points if point.endswith((".kp", ".malfunction", ".fouling"))]


def test_history_follows_the_plant_the_snapshot_shows(session, client):
    step(session, 5)
    free_times = times(client)

    session.load("pump-trip")
    loaded = times(client)
    session.start()
    step(session, 3)
    running = times(client)

    assert len(free_times) == 6
    assert loaded == pytest.approx([loaded[0]])
    assert running == pytest.approx([loaded[0] + i for i in range(4)])

    session.abort()
    aborted = times(client)

    # Abort rebuilds the run at its armed time: history starts over there.
    assert aborted == pytest.approx([loaded[0]])

    session.unload()

    assert times(client) == free_times


def test_loading_a_scenario_again_starts_a_fresh_history(session, client):
    session.load("pump-trip")
    session.start()
    step(session, 3)
    session.abort()
    session.unload()

    session.load("pump-trip")

    assert len(times(client)) == 1


def test_a_point_of_one_plant_unknown_to_another_is_refused(session):
    with pytest.raises(KeyError, match="Z-9.level"):
        session.trend_history(["Z-9.level"])


@pytest.mark.parametrize("speed", [0.25, 0.1])
def test_a_slower_clock_records_one_sample_per_period_of_simulated_time(session, client, speed):
    session.free.engine.clock.set_speed(speed)

    step(session, 100)

    stamps = times(client)
    spacing = [b - a for a, b in zip(stamps, stamps[1:])]

    assert len(stamps) == 1 + round(100 * speed / config.TREND_SAMPLE_PERIOD_SECONDS)
    assert min(spacing) >= config.TREND_SAMPLE_PERIOD_SECONDS - 1e-9
    assert max(spacing) <= config.TREND_SAMPLE_PERIOD_SECONDS + 1e-9


def test_trend_capacity_covers_the_longest_scenario_time_limit():
    scenarios = Path(__file__).resolve().parent.parent / "config" / "scenarios"
    limits = [yaml.safe_load(path.read_text())["time_limit_s"] for path in scenarios.glob("*.yaml")]

    assert limits
    assert config.TREND_CAPACITY * config.TREND_SAMPLE_PERIOD_SECONDS >= max(limits)


def test_trend_routes_never_start_a_scheduler():
    client = main.app.test_client()

    assert client.get("/api/trend/points").status_code == 200
    points = client.get("/api/trend/points").get_json()["points"]
    assert client.get(f"/api/trend?tags={points[0]}").status_code == 200

    session = main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)

    assert session.training_scheduler.running is False
    assert not scheduler_workers()


def test_the_app_serves_the_trend_over_the_cookie_session():
    client = main.app.test_client()

    client.post("/api/action", json={"target": "P-101", "action": "stop"})
    response = client.get(f"/api/trend?tags={POINT}")

    assert response.status_code == 200
    assert len(response.get_json()[POINT]) == 1


def test_limits_list_exactly_the_evaluated_trend_points_with_only_set_bounds(client):
    limits = client.get("/api/trend/points").get_json()["limits"]

    assert limits == {
        POINT: {
            "trip_lo": pytest.approx(0.1),
            "warning_lo": pytest.approx(0.2),
            "warning_hi": pytest.approx(0.8),
            "trip_hi": pytest.approx(0.9),
        },
    }


def test_a_limit_the_engine_cannot_resolve_draws_no_band(client):
    limits = client.get("/api/trend/points").get_json()["limits"]

    assert "K-101.discharge_pressure" not in limits
    assert "P-101.flow" not in limits


def test_limits_follow_the_plant_the_snapshot_shows(session, client):
    free_limits = client.get("/api/trend/points").get_json()["limits"]

    session.load("pump-trip")
    body = client.get("/api/trend/points").get_json()

    assert free_limits
    assert set(body["limits"]) == {POINT}
    assert body["limits"][POINT] == free_limits[POINT]
