"""
Alarm state machine (T10-1, contract C7) - pure ISA-style lifecycle, no plant
dependency.

Four states cover the standard lifecycle: `NORMAL`, `UNACK` (condition active,
not yet acknowledged), `ACKED` (condition active, acknowledged) and
`RTN_UNACK` (condition cleared before it was ever acknowledged). That last
state is where naive implementations go wrong: a return to normal is not an
acknowledgement, so the alarm must keep demanding operator attention until it
is actually acknowledged, even though the underlying condition is already
gone. A condition that re-activates while in `RTN_UNACK` returns to `UNACK`
rather than starting a second alarm - an `Alarm` tracks one condition for its
whole life, never a new instance per occurrence.
"""

from __future__ import annotations

from enum import StrEnum


class AlarmState(StrEnum):
    NORMAL = "normal"
    UNACK = "unack"
    ACKED = "acked"
    RTN_UNACK = "rtn_unack"


class Alarm:
    def __init__(self) -> None:
        self._state = AlarmState.NORMAL

    @property
    def state(self) -> AlarmState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state in (AlarmState.UNACK, AlarmState.ACKED)

    @property
    def acknowledged(self) -> bool:
        return self._state in (AlarmState.NORMAL, AlarmState.ACKED)

    def activate(self) -> None:
        if self._state in (AlarmState.NORMAL, AlarmState.RTN_UNACK):
            self._state = AlarmState.UNACK

    def clear(self) -> None:
        if self._state is AlarmState.UNACK:
            self._state = AlarmState.RTN_UNACK
        elif self._state is AlarmState.ACKED:
            self._state = AlarmState.NORMAL

    def acknowledge(self) -> None:
        if self._state is AlarmState.UNACK:
            self._state = AlarmState.ACKED
        elif self._state is AlarmState.RTN_UNACK:
            self._state = AlarmState.NORMAL
