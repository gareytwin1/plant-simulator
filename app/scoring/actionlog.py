"""
Operator action log (T15-1, contract C6's action-typed event).

Every operator input that reaches a device through the C5 action endpoint
(`app/api/action.py`) is recorded here, timestamped in sim time - never wall
time, so the log is exactly what deterministic replay (T14-5) needs: the
same sequence of actions played back against the same sim-time clock
reproduces the same run bit for bit. The log is append-only - nothing here
removes or rewrites an entry - so it stays "the single record of intent" for
the whole life of a scenario, which is also what scoring (T15-2) reads to
count unnecessary actions.

`ActionEvent` is the C6 event record shape, this module's own type="action"
corner of it - same pattern as `app.alarms.manager.Event` (type="alarm"):
each contract owner fills in its own fixed `type`, since no shared
`app/events.py` exists yet to hang a single class off of. `priority` reuses
`app.alarms.manager.Priority` rather than inventing a second scale, and every
action carries `Priority.LOW`: an operator's own considered input is never
something demanding attention the way an alarm is, but the field still has to
be present and comparable so a mixed event stream (console, historian) can
sort or filter by priority without a type-specific special case for actions.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.alarms.manager import Priority


@dataclass(frozen=True)
class ActionEvent:
    """C6 event record shape. This log only ever emits type="action"."""

    id: str
    sim_time: float
    tag: str
    priority: Priority
    message: str
    data: Mapping[str, object] = field(default_factory=dict)
    type: Literal["action"] = "action"


class ActionLog:
    """Append-only record of every operator action, in the order taken.

    `id` is the entry's own index rather than a random or wall-clock value,
    so two logs built from the same sequence of `record()` calls are
    identical - no source of nondeterminism belongs here any more than it
    would inside a device.
    """

    def __init__(self) -> None:
        self._events: list[ActionEvent] = []

    def record(self, tag: str, action: str, value: float | None, sim_time: float) -> ActionEvent:
        detail = "" if value is None else f" {value!r}"
        event = ActionEvent(
            id=f"action-{len(self._events)}",
            sim_time=sim_time,
            tag=tag,
            priority=Priority.LOW,
            message=f"{tag} {action}{detail}",
            data={"action": action, "value": value},
        )
        self._events.append(event)

        return event

    @property
    def events(self) -> tuple[ActionEvent, ...]:
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[ActionEvent]:
        return iter(self._events)
