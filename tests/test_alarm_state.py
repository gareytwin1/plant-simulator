import pytest

from app.alarms.state import Alarm, AlarmState

# How to reach each state through the machine's own transitions, so the table
# below exercises real paths rather than poking private state.
_REACH: dict[AlarmState, tuple[str, ...]] = {
    AlarmState.NORMAL: (),
    AlarmState.UNACK: ("activate",),
    AlarmState.ACKED: ("activate", "acknowledge"),
    AlarmState.RTN_UNACK: ("activate", "clear"),
}

_TRANSITIONS: dict[tuple[AlarmState, str], AlarmState] = {
    (AlarmState.NORMAL, "activate"): AlarmState.UNACK,
    (AlarmState.NORMAL, "clear"): AlarmState.NORMAL,
    (AlarmState.NORMAL, "acknowledge"): AlarmState.NORMAL,
    (AlarmState.UNACK, "activate"): AlarmState.UNACK,
    (AlarmState.UNACK, "clear"): AlarmState.RTN_UNACK,
    (AlarmState.UNACK, "acknowledge"): AlarmState.ACKED,
    (AlarmState.ACKED, "activate"): AlarmState.ACKED,
    (AlarmState.ACKED, "clear"): AlarmState.NORMAL,
    (AlarmState.ACKED, "acknowledge"): AlarmState.ACKED,
    (AlarmState.RTN_UNACK, "activate"): AlarmState.UNACK,
    (AlarmState.RTN_UNACK, "clear"): AlarmState.RTN_UNACK,
    (AlarmState.RTN_UNACK, "acknowledge"): AlarmState.NORMAL,
}


def _alarm_in(state: AlarmState) -> Alarm:
    alarm = Alarm()
    for event in _REACH[state]:
        getattr(alarm, event)()
    assert alarm.state is state
    return alarm


@pytest.mark.parametrize(("start", "event"), list(_TRANSITIONS))
def test_full_transition_table(start: AlarmState, event: str) -> None:
    alarm = _alarm_in(start)
    getattr(alarm, event)()
    assert alarm.state is _TRANSITIONS[(start, event)]


def test_new_alarm_starts_normal_inactive_and_acknowledged():
    alarm = Alarm()
    assert alarm.state is AlarmState.NORMAL
    assert not alarm.active
    assert alarm.acknowledged


def test_cleared_but_unacknowledged_persists_until_acknowledged():
    alarm = Alarm()
    alarm.activate()
    alarm.clear()

    assert alarm.state is AlarmState.RTN_UNACK
    assert not alarm.active
    assert not alarm.acknowledged

    # Clearing an already-cleared condition does not force an acknowledgement.
    alarm.clear()
    assert alarm.state is AlarmState.RTN_UNACK
    assert not alarm.acknowledged

    alarm.acknowledge()
    assert alarm.state is AlarmState.NORMAL
    assert alarm.acknowledged


def test_realarm_before_acknowledgement_does_not_duplicate():
    alarm = Alarm()
    alarm.activate()
    alarm.clear()
    assert alarm.state is AlarmState.RTN_UNACK

    alarm.activate()
    assert alarm.state is AlarmState.UNACK
    assert alarm.active
    assert not alarm.acknowledged

    # Still the same single alarm - acknowledging it now settles it in ACKED,
    # not NORMAL, because the condition is active again.
    alarm.acknowledge()
    assert alarm.state is AlarmState.ACKED
    assert alarm.active
    assert alarm.acknowledged


def test_acknowledging_an_active_alarm_keeps_it_active_until_cleared():
    alarm = Alarm()
    alarm.activate()
    alarm.acknowledge()

    assert alarm.state is AlarmState.ACKED
    assert alarm.active
    assert alarm.acknowledged

    alarm.clear()
    assert alarm.state is AlarmState.NORMAL
    assert not alarm.active
