"""
Alarm history (T10-3, contract C7) - bounded, oldest-evicted-first record of
every alarm event and acknowledgement, for the debrief.

`AlarmManager` (T10-2) is a pure function of envelope events and time; it
holds no record of what already scrolled past. `AlarmHistory` is what
survives an alarm scrolling off `AlarmManager.active()`. One instance covers
one scenario run; it carries no run identifier and cannot reset itself, so
the owner replaces it and its `AlarmManager` together at each run boundary.
`tag_of` catches only one direction of getting that wrong - a fresh
`AlarmHistory` paired with a reused `AlarmManager` has no tag for any
existing alarm - not a stale `AlarmHistory` kept past a manager restart.

An acknowledgement is its own `AcknowledgeRecord`, not folded into the
`Event` that first raised the alarm, so a debrief can read both timestamps
rather than one overwriting the other. `AlarmHistory` does not decide
whether an acknowledgement is redundant - that answer lives in
`AlarmManager`'s own `Alarm` state (`AlarmManager.get`), never in a flag
reconstructed here from what happened to have been recorded; same reasoning
as "a device does not own solved plant state" (AGENTS.md), one level up.

`record_events`/`record_acknowledge`/`entries` share one lock, same as
`Historian` (T17-1): nothing calls `record_events` from the engine's step
loop yet (a later task wires that), but once something does, that producer
thread can run concurrently with a Flask request thread. The lock guards
only this instance's own data structures; ordering an `Event` against a
later `AcknowledgeRecord` for the same id across threads is left to
whichever caller wires `record_events` and `record_acknowledge` together,
not something bounded here.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from app.alarms.manager import Event


@dataclass(frozen=True)
class AcknowledgeRecord:
    """Records that `alarm_id` (raised against `tag`) was acknowledged at
    `sim_time`."""

    alarm_id: str
    tag: str
    sim_time: float


HistoryEntry = Event | AcknowledgeRecord


class AlarmHistory:
    """Bounded, oldest-evicted-first record of alarm events and
    acknowledgements, in the order they were recorded.

    `capacity` bounds the total number of retained entries, events and
    acknowledgements together - same discipline as `Historian` (T17-1), so
    an `AcknowledgeRecord` can outlive the `Event` that raised it.

    `Event.id` names a monitored point for its whole life (T10-2), not one
    occurrence: every reportable band change adds another `Event` with the
    id it already had (escalation, a side flip, re-activation after
    RTN_UNACK - see `AlarmManager`'s and `Alarm`'s own docstrings). Pairing
    an ack with the occurrence it closed means the most recent preceding
    `Event` sharing its id, tolerating one capacity has evicted, assuming
    the caller orders its own `record_events`/`record_acknowledge` calls
    (see the module docstring's locking note).
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")

        self._lock = threading.Lock()
        self._entries: deque[HistoryEntry] = deque(maxlen=capacity)
        # Keyed by alarm id, like AlarmManager's own _alarms/_band maps: this
        # grows with the number of distinct ids seen over a run, not with
        # capacity - the same tradeoff AlarmManager already makes.
        self._tag_of: dict[str, str] = {}

    def record_events(self, events: Iterable[Event]) -> None:
        with self._lock:
            for event in events:
                self._entries.append(event)
                self._tag_of[event.id] = event.tag

    def tag_of(self, alarm_id: str) -> str | None:
        """The tag last recorded for `alarm_id`'s raising event, or `None`
        if this history has never recorded one. Check this before changing
        other state on the strength of recording an acknowledgement, and
        pass the result to `record_acknowledge` rather than looking it up
        twice."""
        with self._lock:
            return self._tag_of.get(alarm_id)

    def record_acknowledge(self, alarm_id: str, tag: str, sim_time: float) -> None:
        """Record that `alarm_id` (raised against `tag`) was acknowledged.
        Trusts `tag` rather than re-deriving it; the caller is expected to
        have it from `tag_of` already."""
        with self._lock:
            self._entries.append(AcknowledgeRecord(alarm_id=alarm_id, tag=tag, sim_time=sim_time))

    def entries(self) -> tuple[HistoryEntry, ...]:
        """Retained entries, oldest first."""
        with self._lock:
            return tuple(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
