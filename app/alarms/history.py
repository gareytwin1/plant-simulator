"""
Alarm history (T10-3, contract C7) - bounded, oldest-evicted-first record of
every alarm event and acknowledgement, for the debrief.

`AlarmManager` (T10-2) is a pure function of envelope events and time; it
holds no record of what already scrolled past. `AlarmHistory` is what
survives an alarm scrolling off `AlarmManager.active()` - the debrief needs
to see a TRIP that fired at t=200 and was acknowledged at t=210, even if the
plant is back at NORMAL by t=300 and `active()` no longer shows it. It is the
`AlarmManager` counterpart to `Historian` (T17-1): also a bounded ring
buffer, but one instance covers one scenario run in its entirety rather than
one series per tag, since a debrief wants "everything that happened this
scenario" in the order it happened. `AlarmHistory` carries no run
identifier of its own and cannot reset itself - starting a new run means the
owner replaces this instance and its `AlarmManager` together; reusing one
against a fresh instance of the other is exactly the drift `record_acknowledge`'s
caller is expected to check for (see `tag_of` below).

An acknowledgement is its own `AcknowledgeRecord`, not folded into the
`Event` that first raised the alarm - the operator's response is a distinct
occurrence with its own time, and a debrief that asks "how long between the
alarm and the ack" needs both timestamps preserved rather than one
overwriting the other. It carries a copy of the raising event's `tag`, since
once the bound evicts that event a bare `alarm_id` gives the debrief nothing
to show.

`AlarmHistory` does not decide whether an acknowledgement is redundant -
that answer lives in `AlarmManager`'s own `Alarm` state (via `AlarmManager.get`),
never in a flag reconstructed here from what happened to have been recorded.
A history built from a fresh instance, or one where a caller forgot a
`record_events` call, would disagree with the manager's real state and let a
phantom acknowledgement through; `AlarmHistory` only ever records what its
caller has already confirmed is real. Same reasoning as "a device does not
own solved plant state" (AGENTS.md), one level up: this module does not own
alarm state either.

`record_events`/`record_acknowledge`/`entries` share one lock, same as
`Historian` (T17-1) - for the same reason. Nothing calls `record_events`
from the engine's step loop yet (T10-3 leaves that wiring to a later task);
once something does, that producer thread can run concurrently with a
Flask request thread reading `entries()` or calling `record_acknowledge`,
and neither may observe a half-mutated buffer. That lock guards this
instance's own data structures only - it says nothing about the order an
`Event` and a later `AcknowledgeRecord` for the same id land in relative to
each other across threads. Ordering the two against a concurrent
`record_events()` call is the job of whichever caller wires them together
(a lock shared with that call site, held across both), not something
bounded here.
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

    `capacity` bounds the total number of retained entries; once full, the
    oldest is dropped first - same discipline as `Historian` (T17-1). Events
    and acknowledgements share that one bound, so an `AcknowledgeRecord` can
    outlive the `Event` that raised it.

    `Event.id` names a monitored point for its whole life (T10-2), not one
    occurrence, so `entries()` can hold several `Event`s and
    `AcknowledgeRecord`s with the same id - an escalation past an existing
    ACKED alarm, or a re-activation after RTN_UNACK, each add another
    `Event` with the id it already had. A consumer pairing an ack with the
    occurrence it closed wants the most recent preceding `Event` sharing its
    id, tolerating one that capacity has already evicted.
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
        if this history has never recorded one. A caller about to change
        other state on the strength of recording an acknowledgement
        (`AlarmManager.acknowledge`, notably) should check this first and
        pass the result to `record_acknowledge` - one lookup, one decision
        point, rather than a second lookup inside `record_acknowledge`
        whose failure would arrive only after that other state already
        changed, with nothing left to undo it."""
        with self._lock:
            return self._tag_of.get(alarm_id)

    def record_acknowledge(self, alarm_id: str, tag: str, sim_time: float) -> None:
        """Record that `alarm_id` (raised against `tag`) was acknowledged.
        Trusts `tag` rather than re-deriving it - the caller is expected to
        have obtained it from `tag_of` moments earlier, under the same
        decision that made recording an acknowledgement the right call."""
        with self._lock:
            self._entries.append(AcknowledgeRecord(alarm_id=alarm_id, tag=tag, sim_time=sim_time))

    def entries(self) -> tuple[HistoryEntry, ...]:
        """Retained entries, oldest first."""
        with self._lock:
            return tuple(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
