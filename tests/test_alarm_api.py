import threading
import time

import pytest
from flask import Flask

from app.alarms.history import AcknowledgeRecord, AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent, _alarm_id
from app.alarms.state import AlarmState
from app.api.alarms import create_alarm_blueprint
from app.envelope.evaluator import Severity


def build_app():
    manager = AlarmManager()
    history = AlarmHistory(capacity=100)
    sim_time = {"value": 0.0}

    app = Flask(__name__)
    app.register_blueprint(
        create_alarm_blueprint(lambda: manager, lambda: history, lambda: sim_time["value"])
    )

    return app, manager, history, sim_time


def raise_alarm(manager, history, sim_time, tag="K-101", pv="discharge pressure"):
    point = EnvelopeEvent(tag=tag, pv=pv, severity=Severity.ALARM, side="hi")
    events = manager.evaluate([point], sim_time=sim_time)
    history.record_events(events)
    return events[0].id


def test_get_history_returns_recorded_events():
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    raise_alarm(manager, history, sim_time["value"])

    response = client.get("/api/alarms/history")

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 1
    assert body[0]["tag"] == "K-101"
    assert body[0]["type"] == "alarm"
    assert body[0]["priority"] == "high"


def test_get_history_serializes_an_acknowledge_entry_with_tag_and_a_consistent_id_key():
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    alarm_id = raise_alarm(manager, history, sim_time["value"])

    client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})
    response = client.get("/api/alarms/history")

    ack = response.get_json()[-1]
    assert ack == {
        "type": "acknowledge",
        "id": alarm_id,
        "tag": "K-101",
        "sim_time": pytest.approx(0.0),
    }


def test_get_history_is_empty_before_anything_is_recorded():
    app, _manager, _history, _sim_time = build_app()
    client = app.test_client()

    response = client.get("/api/alarms/history")

    assert response.status_code == 200
    assert response.get_json() == []


def test_post_acknowledge_transitions_state_and_is_recorded():
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    alarm_id = raise_alarm(manager, history, sim_time["value"])
    sim_time["value"] = 5.0

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "recorded": True}
    [alarm] = manager.active()
    assert alarm.acknowledged

    entries = history.entries()
    assert len(entries) == 2
    ack = entries[-1]
    assert ack.alarm_id == alarm_id
    assert ack.tag == "K-101"
    assert ack.sim_time == pytest.approx(5.0)


def test_post_acknowledge_twice_records_exactly_once():
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    alarm_id = raise_alarm(manager, history, sim_time["value"])

    first = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})
    second = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert first.get_json() == {"ok": True, "recorded": True}
    assert second.get_json() == {"ok": True, "recorded": False}
    assert len(history) == 2


def test_post_acknowledge_of_a_point_that_never_left_normal_does_not_record():
    # AlarmManager.evaluate() binds an Alarm to every monitored point on
    # first sight, via setdefault, even one whose severity never left
    # NORMAL - so this id is real to the manager despite never appearing in
    # history. Acknowledging it must be a pure no-op, not a phantom record.
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    point = EnvelopeEvent(tag="K-101", pv="suction pressure", severity=Severity.NORMAL)
    manager.evaluate([point], sim_time=sim_time["value"])
    alarm_id = _alarm_id("K-101", "suction pressure")

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "recorded": False}
    assert len(history) == 0


def test_post_acknowledge_of_an_already_cleared_alarm_does_not_record_again():
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    alarm_id = raise_alarm(manager, history, sim_time["value"])
    client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    cleared = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.NORMAL)
    manager.evaluate([cleared], sim_time=10.0)

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert response.get_json() == {"ok": True, "recorded": False}
    assert len(history) == 2


def test_post_acknowledge_of_an_alarm_that_cleared_before_being_acked_still_records():
    # RTN_UNACK: the condition cleared before the operator ever acknowledged
    # it. Alarm.acknowledge() still has a real transition to make here
    # (RTN_UNACK -> NORMAL), even though the point already reads NORMAL.
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    alarm_id = raise_alarm(manager, history, sim_time["value"])

    cleared = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.NORMAL)
    manager.evaluate([cleared], sim_time=5.0)

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert response.get_json() == {"ok": True, "recorded": True}
    assert len(history) == 2
    assert manager.get(alarm_id).state is AlarmState.NORMAL


def test_post_acknowledge_after_the_raising_event_has_been_evicted_still_works():
    # tag_of's entry for an id outlives that id's Event in the bounded
    # deque (the two are tracked separately) - an operator must still be
    # able to acknowledge a long-standing alarm whose original Event has
    # long since scrolled out of a capacity-bounded history.
    manager = AlarmManager()
    history = AlarmHistory(capacity=2)
    app = Flask(__name__)
    app.register_blueprint(create_alarm_blueprint(lambda: manager, lambda: history, lambda: 0.0))
    client = app.test_client()

    # Distinct tags on the evicted alarm vs. the two that push it out of the
    # buffer: identical tags would let ack.tag == "K-101" pass even if
    # tag_of returned whichever Event was recorded last, rather than the
    # evicted alarm's own.
    alarm_id = raise_alarm(manager, history, 0.0, tag="K-101", pv="discharge pressure")
    raise_alarm(manager, history, 0.0, tag="P-101", pv="speed")
    raise_alarm(manager, history, 0.0, tag="FV-101", pv="position")

    assert all(getattr(entry, "id", None) != alarm_id for entry in history.entries())

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert response.get_json() == {"ok": True, "recorded": True}
    assert manager.get(alarm_id).acknowledged is True
    ack = history.entries()[-1]
    assert isinstance(ack, AcknowledgeRecord)
    assert ack.alarm_id == alarm_id
    assert ack.tag == "K-101"
    assert all(getattr(entry, "id", None) != alarm_id for entry in history.entries())


def test_post_acknowledge_of_an_id_missing_from_history_is_a_409_and_leaves_state_unchanged():
    # Simulates history and manager having drifted apart - a fresh history
    # for a reused manager, or a missed record_events call: the manager has
    # a real, unacknowledged alarm this history was never told about.
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    point = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")
    events = manager.evaluate([point], sim_time=sim_time["value"])
    alarm_id = events[0].id
    # Deliberately not calling history.record_events(events).

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert response.status_code == 409
    assert "error" in response.get_json()
    assert len(history) == 0
    assert manager.get(alarm_id).acknowledged is False


def test_post_acknowledge_concurrent_requests_record_exactly_once():
    # get_sim_time runs inside the blueprint's locked section, so blocking
    # the first request there and only releasing it once the second request
    # has been sent proves the lock actually serializes the two - a version
    # of this test that just lines threads up before each POST would pass
    # even with the lock removed, since the requests would still very likely
    # run one after another.
    manager = AlarmManager()
    history = AlarmHistory(capacity=100)
    alarm_id = raise_alarm(manager, history, 0.0)

    first_thread_entered = threading.Event()
    release_first_thread = threading.Event()
    call_count = {"value": 0}
    call_count_lock = threading.Lock()

    def get_sim_time() -> float:
        with call_count_lock:
            call_count["value"] += 1
            is_first_call = call_count["value"] == 1
        if is_first_call:
            first_thread_entered.set()
            assert release_first_thread.wait(timeout=5.0)
        return 0.0

    app = Flask(__name__)
    app.register_blueprint(create_alarm_blueprint(lambda: manager, lambda: history, get_sim_time))

    responses: list[object] = []
    responses_lock = threading.Lock()

    def post() -> None:
        client = app.test_client()
        response = client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})
        with responses_lock:
            responses.append(response.get_json())

    first = threading.Thread(target=post)
    first.start()
    assert first_thread_entered.wait(timeout=5.0)

    second = threading.Thread(target=post)
    second.start()

    # Give the second request every chance to reach get_sim_time if the lock
    # were not actually holding it back - poll rather than a single fixed
    # sleep, so this only passes because the second call never arrives, not
    # because we didn't wait long enough for it to. Reaching get_sim_time
    # takes microseconds when unblocked, so 0.5s is a large margin without
    # taxing every passing run by seconds.
    try:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with call_count_lock:
                reached = call_count["value"]
            if reached >= 2 or not second.is_alive():
                break
            time.sleep(0.01)

        assert call_count["value"] == 1  # second never reached get_sim_time
        assert manager.get(alarm_id).acknowledged is False  # ...nor manager.acknowledge()
        assert second.is_alive()  # ...so it is still blocked on the lock
        assert len(history) == 1  # only the original raise; neither ack recorded yet
    finally:
        # However the assertions above came out, the first thread is stuck
        # in get_sim_time until this fires - release it unconditionally so a
        # failed assertion can't strand a non-daemon thread past the test.
        release_first_thread.set()

    first.join(timeout=5.0)
    second.join(timeout=5.0)
    assert not first.is_alive()
    assert not second.is_alive()

    assert len(responses) == 2
    recorded = [body for body in responses if body["recorded"] is True]
    assert len(recorded) == 1
    not_recorded = [body for body in responses if body["recorded"] is False]
    assert not_recorded == [{"ok": True, "recorded": False}]
    assert len(history) == 2


def test_post_acknowledge_uses_sim_time_not_wall_time():
    app, manager, history, sim_time = build_app()
    client = app.test_client()
    alarm_id = raise_alarm(manager, history, sim_time["value"])
    sim_time["value"] = 123.0

    client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert history.entries()[-1].sim_time == pytest.approx(123.0)


def test_post_acknowledge_unknown_alarm_id_is_a_400_not_a_500():
    app, _manager, history, _sim_time = build_app()
    client = app.test_client()

    response = client.post("/api/alarms/acknowledge", json={"alarm_id": "no-such-alarm"})

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert len(history) == 0


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-alarm-id"),
        pytest.param({"alarm_id": 1}, id="non-string-alarm-id"),
    ],
)
def test_post_acknowledge_rejects_malformed_bodies(body):
    app, _manager, history, _sim_time = build_app()
    client = app.test_client()

    response = client.post("/api/alarms/acknowledge", json=body)

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert len(history) == 0


def test_post_acknowledge_non_json_body_is_rejected():
    app, _manager, _history, _sim_time = build_app()
    client = app.test_client()

    response = client.post("/api/alarms/acknowledge", data="not json", content_type="text/plain")

    assert response.status_code == 400
    assert "error" in response.get_json()
