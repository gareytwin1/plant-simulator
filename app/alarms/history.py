"""
Alarm history (T10-3, contract C7) - bounded, oldest-evicted-first record of
every alarm event and acknowledgement, for the debrief.

`AlarmManager` (T10-2) is a pure function of envelope events and time; it
holds no record of what already scrolled past. `AlarmHistory` is what
survives an alarm scrolling off `AlarmManager.active()` - the debrief needs
to see a TRIP that fired at t=200 and was acknowledged at t=210, even if the
plant is back at NORMAL by t=300 and `active()` no longer shows it. It is the
`AlarmManager` counterpart to `Historian` (T17-1): also a bounded ring
buffer, but keyed by run rather than by tag, since a debrief wants
"everything that happened this scenario" in the order it happened, not one
series per point.

An acknowledgement is its own `AcknowledgeRecord`, not folded into the
`Event` that first raised the alarm - the operator's response is a distinct
occurrence with its own time, and a debrief that asks "how long between the
alarm and the ack" needs both timestamps preserved rather than one
overwriting the other. `AlarmHistory` does not call `AlarmManager` itself
(C7 stays a pure function with no history dependency); a caller records each
side after it happens - `record_events` after `evaluate()`, `record_acknowledge`
after `acknowledge()` succeeds.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from app.alarms.manager import Event


@dataclass(frozen=True)
class AcknowledgeRecord:
    """Records that `alarm_id` was acknowledged at `sim_time`."""

    alarm_id: str
    sim_time: float


HistoryEntry = Event | AcknowledgeRecord


class AlarmHistory:
    """Bounded, oldest-evicted-first record of alarm events and
    acknowledgements, in the order they were recorded.

    `capacity` bounds the total number of retained entries; once full, the
    oldest is dropped first - same discipline as `Historian` (T17-1).
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")

        self._entries: deque[HistoryEntry] = deque(maxlen=capacity)

    def record_events(self, events: Iterable[Event]) -> None:
        self._entries.extend(events)

    def record_acknowledge(self, alarm_id: str, sim_time: float) -> None:
        self._entries.append(AcknowledgeRecord(alarm_id=alarm_id, sim_time=sim_time))

    def entries(self) -> tuple[HistoryEntry, ...]:
        """Retained entries, oldest first."""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
