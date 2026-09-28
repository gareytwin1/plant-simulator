import pytest

from app.alarms.manager import AlarmManager, EnvelopeEvent, Priority, _alarm_id
from app.envelope.evaluator import Severity


def test_band_change_produces_the_right_alarm_and_priority():
    manager = AlarmManager()

    events = manager.evaluate(
        [EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")],
        sim_time=10.0,
    )

    assert len(events) == 1
    event = events[0]
    assert event.tag == "K-101"
    assert event.priority is Priority.HIGH
    assert event.sim_time == 10.0
    assert event.type == "alarm"


def test_priority_comes_from_configuration_not_a_hardcoded_table():
    manager = AlarmManager(
        priorities={
            Severity.WARNING: Priority.CRITICAL,
            Severity.ALARM: Priority.CRITICAL,
            Severity.TRIP: Priority.CRITICAL,
        }
    )

    events = manager.evaluate(
        [EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")],
        sim_time=0.0,
    )

    assert events[0].priority is Priority.CRITICAL


def test_missing_priority_for_a_severity_is_rejected():
    with pytest.raises(ValueError):
        AlarmManager(priorities={Severity.WARNING: Priority.LOW, Severity.ALARM: Priority.HIGH})


@pytest.mark.parametrize(
    "severity, side, expected_suffix",
    [
        (Severity.WARNING, "hi", "HI"),
        (Severity.WARNING, "lo", "LO"),
        (Severity.ALARM, "hi", "HIHI"),
        (Severity.ALARM, "lo", "LOLO"),
        (Severity.TRIP, "hi", "HIHIHI"),
        (Severity.TRIP, "lo", "LOLOLO"),
    ],
)
def test_message_names_the_symptom_with_an_isa_style_suffix(severity, side, expected_suffix):
    manager = AlarmManager()

    events = manager.evaluate(
        [EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side=side)],
        sim_time=0.0,
    )

    assert events[0].message == f"K-101 discharge pressure {expected_suffix}"


_CAUSAL_LANGUAGE = ("stuck", "failed", "fault", "reversed", "leaking", "valve", "because")


@pytest.mark.parametrize("severity, side", [(Severity.ALARM, "hi"), (Severity.TRIP, "lo")])
def test_message_content_contains_no_causal_language(severity, side):
    manager = AlarmManager()

    events = manager.evaluate(
        [EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=severity, side=side)],
        sim_time=0.0,
    )

    message = events[0].message.lower()
    for word in _CAUSAL_LANGUAGE:
        assert word not in message


def test_clearing_the_condition_clears_the_alarm():
    manager = AlarmManager()
    point = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    manager.evaluate([point], sim_time=0.0)
    assert len(manager.active()) == 1

    cleared = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.NORMAL)
    events = manager.evaluate([cleared], sim_time=5.0)

    assert manager.active() == []
    assert events == []


def test_repeated_identical_severity_does_not_reemit():
    manager = AlarmManager()
    point = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    manager.evaluate([point], sim_time=0.0)
    events = manager.evaluate([point], sim_time=1.0)

    assert events == []
    assert len(manager.active()) == 1


def test_escalation_while_already_active_emits_an_updated_event():
    manager = AlarmManager()
    warning = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")
    alarm = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    manager.evaluate([warning], sim_time=0.0)
    events = manager.evaluate([alarm], sim_time=1.0)

    assert len(events) == 1
    assert events[0].priority is Priority.HIGH
    assert events[0].message.endswith("HIHI")
    assert len(manager.active()) == 1


def test_same_severity_side_flip_still_reports_a_band_change():
    manager = AlarmManager()
    lo = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="lo")
    hi = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")

    manager.evaluate([lo], sim_time=0.0)
    events = manager.evaluate([hi], sim_time=1.0)

    assert len(events) == 1
    assert events[0].message.endswith(" HI")
    assert len(manager.active()) == 1


def test_de_escalation_between_two_active_severities_emits_an_updated_event():
    manager = AlarmManager()
    trip = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.TRIP, side="hi")
    alarm = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    manager.evaluate([trip], sim_time=0.0)
    events = manager.evaluate([alarm], sim_time=1.0)

    assert len(events) == 1
    assert events[0].priority is Priority.HIGH
    assert events[0].message.endswith("HIHI")
    [active_alarm] = manager.active()
    assert active_alarm.active


def test_escalating_an_acknowledged_alarm_returns_it_to_unacknowledged():
    manager = AlarmManager()
    warning = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")
    alarm = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")

    events = manager.evaluate([warning], sim_time=0.0)
    manager.acknowledge(events[0].id, sim_time=0.5)
    [acked] = manager.active()
    assert acked.acknowledged

    events = manager.evaluate([alarm], sim_time=1.0)

    assert len(events) == 1
    [reactivated] = manager.active()
    assert not reactivated.acknowledged


def test_side_flip_of_an_acknowledged_alarm_returns_it_to_unacknowledged():
    manager = AlarmManager()
    lo = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="lo")
    hi = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")

    events = manager.evaluate([lo], sim_time=0.0)
    manager.acknowledge(events[0].id, sim_time=0.5)
    assert manager.active()[0].acknowledged

    manager.evaluate([hi], sim_time=1.0)

    assert not manager.active()[0].acknowledged


def test_tag_and_pv_containing_a_colon_do_not_collide():
    manager = AlarmManager()

    events = manager.evaluate(
        [
            EnvelopeEvent(tag="K-101", pv="a:b", severity=Severity.ALARM, side="hi"),
            EnvelopeEvent(tag="K-101:a", pv="b", severity=Severity.WARNING, side="lo"),
        ],
        sim_time=0.0,
    )

    assert len(events) == 2
    assert events[0].id != events[1].id
    assert len(manager.active()) == 2


def test_de_escalating_an_acknowledged_alarm_returns_it_to_unacknowledged():
    manager = AlarmManager()
    alarm_severity = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")
    warning = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.WARNING, side="hi")

    events = manager.evaluate([alarm_severity], sim_time=0.0)
    manager.acknowledge(events[0].id, sim_time=0.5)
    assert manager.active()[0].acknowledged

    events = manager.evaluate([warning], sim_time=1.0)

    assert len(events) == 1
    assert events[0].priority is Priority.LOW
    assert not manager.active()[0].acknowledged


def test_acknowledge_with_an_unknown_alarm_id_raises_a_clear_error():
    manager = AlarmManager()

    with pytest.raises(KeyError, match="unknown alarm_id"):
        manager.acknowledge("no-such-alarm", sim_time=0.0)


def test_two_points_on_the_same_tag_alarm_independently():
    manager = AlarmManager()

    events = manager.evaluate(
        [
            EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi"),
            EnvelopeEvent(tag="K-101", pv="suction pressure", severity=Severity.WARNING, side="lo"),
        ],
        sim_time=0.0,
    )

    assert len(events) == 2
    assert len(manager.active()) == 2


def test_acknowledge_transitions_the_underlying_alarm():
    manager = AlarmManager()
    point = EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM, side="hi")
    events = manager.evaluate([point], sim_time=0.0)

    manager.acknowledge(events[0].id, sim_time=1.0)

    [alarm] = manager.active()
    assert alarm.acknowledged


def test_side_is_required_for_a_non_normal_severity():
    with pytest.raises(ValueError):
        EnvelopeEvent(tag="K-101", pv="discharge pressure", severity=Severity.ALARM)


def test_is_acknowledged_returns_none_for_an_id_never_seen():
    manager = AlarmManager()

    assert manager.is_acknowledged("no-such-alarm") is None


def test_is_acknowledged_is_true_for_a_point_that_never_left_normal():
    # evaluate() binds an Alarm to every monitored point on first sight, via
    # setdefault, even one whose severity never left NORMAL - so its id is
    # real and resolvable through is_acknowledged() despite never emitting
    # an Event.
    manager = AlarmManager()
    point = EnvelopeEvent(tag="K-101", pv="suction pressure", severity=Severity.NORMAL)

    events = manager.evaluate([point], sim_time=0.0)

    assert events == []
    assert manager.is_acknowledged(_alarm_id("K-101", "suction pressure")) is True
