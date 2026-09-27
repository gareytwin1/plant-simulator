import pytest

from app.alarms.manager import Priority
from app.scoring.actionlog import ActionLog


def test_record_returns_a_c6_shaped_event():
    log = ActionLog()

    event = log.record(tag="K-101", action="start", value=None, sim_time=12.5)

    assert event.id == "action-0"
    assert event.sim_time == pytest.approx(12.5)
    assert event.tag == "K-101"
    assert event.type == "action"
    assert event.priority == Priority.LOW
    assert event.data == {"action": "start", "value": None}


def test_record_carries_a_value_when_given_one():
    log = ActionLog()

    event = log.record(tag="FV-101", action="set_position_target", value=0.42, sim_time=3.0)

    assert event.data["action"] == "set_position_target"
    assert event.data["value"] == pytest.approx(0.42)
    assert "0.42" in event.message


def test_ids_are_sequential_and_deterministic_not_random_or_wall_clock():
    log = ActionLog()

    first = log.record(tag="K-101", action="start", value=None, sim_time=0.0)
    second = log.record(tag="K-101", action="stop", value=None, sim_time=1.0)

    assert first.id == "action-0"
    assert second.id == "action-1"


def test_every_action_type_is_captured_in_arrival_order():
    log = ActionLog()

    log.record(tag="K-101", action="start", value=None, sim_time=0.0)
    log.record(tag="K-101", action="set_load_target", value=0.5, sim_time=1.0)
    log.record(tag="FV-101", action="set_position_target", value=0.8, sim_time=2.0)
    log.record(tag="P-101", action="stop", value=None, sim_time=3.0)

    actions = [event.data["action"] for event in log.events]
    assert actions == ["start", "set_load_target", "set_position_target", "stop"]


def test_timestamps_are_sim_time_never_wall_time():
    # The log never reads a clock of its own - sim_time is only ever what the
    # caller passes in, so two calls at the same instant of wall time can
    # still be recorded at whatever sim_time the caller supplies.
    log = ActionLog()

    log.record(tag="K-101", action="start", value=None, sim_time=100.0)
    log.record(tag="K-101", action="stop", value=None, sim_time=100.0)

    assert [event.sim_time for event in log.events] == pytest.approx([100.0, 100.0])


def test_log_is_append_only_events_is_a_tuple_and_len_and_iter_agree():
    log = ActionLog()

    log.record(tag="K-101", action="start", value=None, sim_time=0.0)

    assert isinstance(log.events, tuple)
    assert len(log) == 1
    assert list(log) == list(log.events)


def test_log_survives_a_full_scenario_length_run_with_no_drops():
    log = ActionLog()
    total = 10_000

    for i in range(total):
        log.record(tag="K-101", action="set_load_target", value=float(i), sim_time=float(i))

    assert len(log) == total
    assert log.events[0].sim_time == pytest.approx(0.0)
    assert log.events[-1].sim_time == pytest.approx(total - 1)
    # No entry lost or reordered along the way.
    assert [event.data["value"] for event in log.events] == pytest.approx(
        [float(i) for i in range(total)],
    )
