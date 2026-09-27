import pytest

from app.alarms.history import AcknowledgeRecord, AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent
from app.envelope.evaluator import Severity


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        AlarmHistory(capacity=0)


def test_recorded_events_are_retained_oldest_first():
    history = AlarmHistory(capacity=10)
    manager = AlarmManager()
    point = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")
    alarm = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    history.record_events(manager.evaluate([point], sim_time=0.0))
    history.record_events(manager.evaluate([alarm], sim_time=1.0))

    entries = history.entries()
    assert len(entries) == 2
    assert entries[0].sim_time == pytest.approx(0.0)
    assert entries[1].sim_time == pytest.approx(1.0)


def test_history_survives_a_full_scenario_of_many_steps():
    history = AlarmHistory(capacity=1000)
    manager = AlarmManager()

    for step in range(500):
        severity = Severity.ALARM if step % 2 == 0 else Severity.WARNING
        events = manager.evaluate(
            [EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side="hi")],
            sim_time=float(step),
        )
        history.record_events(events)

    assert len(history) == 500
    assert history.entries()[0].sim_time == pytest.approx(0.0)
    assert history.entries()[-1].sim_time == pytest.approx(499.0)


def test_history_bounded_with_oldest_pruned_first():
    history = AlarmHistory(capacity=3)
    manager = AlarmManager()

    for step in range(5):
        severity = Severity.ALARM if step % 2 == 0 else Severity.WARNING
        events = manager.evaluate(
            [EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side="hi")],
            sim_time=float(step),
        )
        history.record_events(events)

    assert len(history) == 3
    sim_times = [entry.sim_time for entry in history.entries()]
    assert sim_times == [2.0, 3.0, 4.0]


def test_acknowledge_is_recorded_as_its_own_entry():
    history = AlarmHistory(capacity=10)
    manager = AlarmManager()
    point = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    events = manager.evaluate([point], sim_time=0.0)
    history.record_events(events)
    manager.acknowledge(events[0].id, sim_time=2.5)
    history.record_acknowledge(events[0].id, sim_time=2.5)

    entries = history.entries()
    assert len(entries) == 2
    ack = entries[-1]
    assert isinstance(ack, AcknowledgeRecord)
    assert ack.alarm_id == events[0].id
    assert ack.tag == "K-101"
    assert ack.sim_time == pytest.approx(2.5)


def test_acknowledge_entries_count_toward_the_bound():
    history = AlarmHistory(capacity=2)

    history.record_acknowledge("alarm-1", sim_time=0.0)
    history.record_acknowledge("alarm-2", sim_time=1.0)
    history.record_acknowledge("alarm-3", sim_time=2.0)

    entries = history.entries()
    assert len(entries) == 2
    assert [entry.alarm_id for entry in entries] == ["alarm-2", "alarm-3"]
