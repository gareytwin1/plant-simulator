"""
Acknowledging an alarm (T10-3, moved out of the HTTP layer by T16-6).

Three checks run in sequence, and the caller holds one lock across all of them
so two concurrent acknowledgements of the same id cannot both act:

- `manager.is_acknowledged()` is `None` for an unknown id.
- Otherwise it is the redundancy check - already ACKED, cleared to NORMAL, or
  never raised (`AlarmManager.evaluate`'s `setdefault` binds an `Alarm` even
  to a point that has never left NORMAL) all read `True` here, per `Alarm`'s
  own state machine, and nothing is written.
- `history.tag_of()` is checked before `manager.acknowledge()` runs: a history
  that has drifted from the manager (fresh history, reused manager; a missed
  `record_events` call) has no tag for the id, and the alarm's state is left
  untouched rather than acknowledged with nothing to record.

The fetched tag passes straight to `record_acknowledge()`, which trusts it
rather than looking it up again.
"""

from __future__ import annotations

from enum import Enum

from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager


class Acknowledged(Enum):
    RECORDED = "recorded"
    ALREADY = "already"
    UNKNOWN = "unknown"
    NO_EVENT = "no_event"


def acknowledge_alarm(
    manager: AlarmManager,
    history: AlarmHistory,
    alarm_id: str,
    sim_time: float,
) -> Acknowledged:
    acknowledged = manager.is_acknowledged(alarm_id)

    if acknowledged is None:
        return Acknowledged.UNKNOWN

    if acknowledged:
        return Acknowledged.ALREADY

    tag = history.tag_of(alarm_id)

    if tag is None:
        return Acknowledged.NO_EVENT

    manager.acknowledge(alarm_id, sim_time)
    history.record_acknowledge(alarm_id, tag, sim_time)

    return Acknowledged.RECORDED
