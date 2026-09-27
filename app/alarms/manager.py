"""
Alarm manager (T10-2, contract C7) - turns envelope band changes into
prioritised alarm events in the C6 event record shape.

A pure function of envelope events and time, same as `Evaluator.evaluate()`
(T9-1): no plant access. Binds one `Alarm` (T10-1) per monitored point,
keyed by `tag:pv`, so a compressor with two watched variables gets two
independent alarms rather than one that conflates them.

**Every severity change is a band change**, not just the first activation -
WARNING escalating to ALARM while already unacknowledged still produces a
new, re-prioritised event, because the operator needs to know it got worse.
A change that settles at NORMAL clears the alarm (T10-1's own `clear()`
semantics decide whether that makes it immediately available again) but is
not itself reported as an alarm event.

**Messages name symptoms, never causes (C6).** A message is built only from
the tag, the point's own description and an ISA-style HI/LO suffix repeated
once per severity tier (HI, HIHI, HIHIHI) - never from anything that would
imply a mechanism.

**Priorities come from configuration**, not a hardcoded table: the
constructor takes a `Severity -> Priority` mapping (the plant-config wiring
that will produce that mapping belongs to a later task) and falls back to a
sensible default (WARNING/low, ALARM/high, TRIP/critical) when none is given.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from app.alarms.state import Alarm
from app.envelope.evaluator import Severity

Side = Literal["lo", "hi"]

_SUFFIX: dict[Side, str] = {"lo": "LO", "hi": "HI"}

_NON_NORMAL: tuple[Severity, ...] = (Severity.WARNING, Severity.ALARM, Severity.TRIP)


class Priority(StrEnum):
    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"


_DEFAULT_PRIORITIES: dict[Severity, Priority] = {
    Severity.WARNING: Priority.LOW,
    Severity.ALARM: Priority.HIGH,
    Severity.TRIP: Priority.CRITICAL,
}


@dataclass(frozen=True)
class EnvelopeEvent:
    """One monitored point's current classification, as T9-1's `Evaluator`
    would produce it: `pv` names the symptom ("discharge pressure"), `side`
    is which limit it is against. `side` is required whenever `severity` is
    not NORMAL - a band always has a side, and NORMAL never needs one."""

    tag: str
    pv: str
    severity: Severity
    side: Side | None = None

    def __post_init__(self) -> None:
        if self.severity is not Severity.NORMAL and self.side is None:
            raise ValueError("side is required when severity is not NORMAL")


@dataclass(frozen=True)
class Event:
    """C6 event record shape. This manager only ever emits `type="alarm"`."""

    id: str
    sim_time: float
    tag: str
    priority: Priority
    message: str
    data: Mapping[str, object] = field(default_factory=dict)
    type: Literal["alarm"] = "alarm"


class AlarmManager:
    def __init__(self, priorities: Mapping[Severity, Priority] | None = None) -> None:
        resolved: dict[Severity, Priority] = dict(
            _DEFAULT_PRIORITIES if priorities is None else priorities
        )
        missing = [severity for severity in _NON_NORMAL if severity not in resolved]
        if missing:
            raise ValueError(f"missing priority for severities: {missing}")

        self._priorities = resolved
        self._alarms: dict[str, Alarm] = {}
        self._severity: dict[str, Severity] = {}

    def evaluate(self, envelope_events: Iterable[EnvelopeEvent], sim_time: float) -> list[Event]:
        emitted: list[Event] = []
        for envelope_event in envelope_events:
            alarm_id = f"{envelope_event.tag}:{envelope_event.pv}"
            alarm = self._alarms.setdefault(alarm_id, Alarm())
            previous = self._severity.get(alarm_id, Severity.NORMAL)
            severity = envelope_event.severity
            if severity == previous:
                continue
            self._severity[alarm_id] = severity

            if severity is Severity.NORMAL:
                alarm.clear()
                continue

            alarm.activate()
            emitted.append(self._event(alarm_id, envelope_event, sim_time))

        return emitted

    def acknowledge(self, alarm_id: str, sim_time: float) -> None:
        # sim_time is part of C7's frozen signature; T10-3 records it in history.
        self._alarms[alarm_id].acknowledge()

    def active(self) -> list[Alarm]:
        return [alarm for alarm in self._alarms.values() if alarm.active]

    def _event(self, alarm_id: str, envelope_event: EnvelopeEvent, sim_time: float) -> Event:
        assert envelope_event.side is not None  # enforced by EnvelopeEvent.__post_init__
        suffix = _SUFFIX[envelope_event.side] * envelope_event.severity.value
        return Event(
            id=alarm_id,
            sim_time=sim_time,
            tag=envelope_event.tag,
            priority=self._priorities[envelope_event.severity],
            message=f"{envelope_event.tag} {envelope_event.pv} {suffix}",
            data={"pv": envelope_event.pv, "severity": envelope_event.severity.name},
        )
