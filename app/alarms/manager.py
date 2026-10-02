"""
Alarm manager (T10-2, contract C7) - turns envelope band changes into
prioritised alarm events in the C6 event record shape.

A pure function of envelope events and time, same as `Evaluator.evaluate()`
(T9-1): no plant access. Binds one `Alarm` (T10-1) per monitored point,
keyed by tag and pv together (length-prefixed so a ":" inside either string
can't collide two distinct points onto one id), so a compressor with two
watched variables gets two independent alarms rather than one that conflates
them.

**Every band change is reportable**, keyed on `(severity, side)` together -
not just the first activation, and not severity alone. WARNING escalating to
ALARM while already unacknowledged still produces a new, re-prioritised
event, because the operator needs to know it got worse; so does a same-
severity side flip (WARNING/lo straight to WARNING/hi), which `Evaluator`
(T9-1) treats as immediate and exempt from its own deadband - tracking
severity alone would silently swallow that transition. A change that settles
at NORMAL clears the alarm (T10-1's own `clear()` semantics decide whether
that makes it immediately available again) but is not itself reported as an
alarm event. It is reported separately: `last_cleared()` names the points the
most recent `evaluate()` returned to NORMAL, for `AlarmHistory.record_clears`
(T15-2 reads it to time stabilisation).

**A reportable change on an ACKED alarm returns it to UNACK.** The operator
acknowledged the band that was active *then*; a change to a different one is
a new condition, not the one they signed off on, so it goes back to
demanding attention. `Alarm.activate()` alone is a no-op on ACKED (T10-1),
so this manager clears it first, using only `Alarm`'s existing public
transitions rather than reaching into its frozen state machine.

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

from app.alarms.state import Alarm, AlarmState
from app.envelope.evaluator import Severity

Side = Literal["lo", "hi"]

_SUFFIX: dict[Side, str] = {"lo": "LO", "hi": "HI"}

_NON_NORMAL: tuple[Severity, ...] = (Severity.WARNING, Severity.ALARM, Severity.TRIP)


def _alarm_id(tag: str, pv: str) -> str:
    # Length-prefixing tag makes this collision-free regardless of ":" inside
    # tag or pv - "K-101" + "a:b" and "K-101:a" + "b" produce different ids.
    return f"{len(tag)}:{tag}:{pv}"


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


@dataclass(frozen=True)
class Cleared:
    """A monitored point that returned to NORMAL from a non-NORMAL band."""

    alarm_id: str
    tag: str


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
        self._band: dict[str, tuple[Severity, Side | None]] = {}
        self._cleared: tuple[Cleared, ...] = ()

    def evaluate(self, envelope_events: Iterable[EnvelopeEvent], sim_time: float) -> list[Event]:
        emitted: list[Event] = []
        cleared: list[Cleared] = []
        for envelope_event in envelope_events:
            alarm_id = _alarm_id(envelope_event.tag, envelope_event.pv)
            alarm = self._alarms.setdefault(alarm_id, Alarm())
            band = (envelope_event.severity, envelope_event.side)
            previous = self._band.get(alarm_id, (Severity.NORMAL, None))
            if band == previous:
                continue
            self._band[alarm_id] = band

            if envelope_event.severity is Severity.NORMAL:
                alarm.clear()
                if previous[0] is not Severity.NORMAL:
                    cleared.append(Cleared(alarm_id=alarm_id, tag=envelope_event.tag))
                continue

            if alarm.state is AlarmState.ACKED:
                # The operator acknowledged the *previous* band. A reportable
                # change to a new one is a new condition, so it goes back to
                # demanding attention rather than staying silently ACKED.
                alarm.clear()
            alarm.activate()
            emitted.append(self._event(alarm_id, envelope_event, sim_time))

        self._cleared = tuple(cleared)

        return emitted

    def last_cleared(self) -> tuple[Cleared, ...]:
        """Points the most recent `evaluate()` returned to NORMAL, in the
        order they were seen. Additive read accessor (T15-2): `evaluate()`'s
        frozen signature returns only alarm events, and a return to NORMAL
        is not one."""
        return self._cleared

    def acknowledge(self, alarm_id: str, sim_time: float) -> None:
        # sim_time is part of C7's frozen signature; T10-3 records it in history.
        try:
            alarm = self._alarms[alarm_id]
        except KeyError:
            raise KeyError(f"unknown alarm_id: {alarm_id!r}") from None
        alarm.acknowledge()

    def active(self) -> list[Alarm]:
        return [alarm for alarm in self._alarms.values() if alarm.active]

    def is_acknowledged(self, alarm_id: str) -> bool | None:
        """Whether the `Alarm` bound to `alarm_id` currently reads as
        acknowledged, or `None` if this manager has never seen the id.
        Additive read accessor (T10-3, narrowed to the one query a caller
        actually needs rather than handing out the manager's live `Alarm`):
        a caller deciding whether `acknowledge()` would actually change
        anything needs the real state, not a guess reconstructed from
        emitted events - `evaluate()` binds an `Alarm` to every monitored
        point on first sight (via `setdefault`), including one that has
        never left NORMAL and so has never emitted an `Event` a caller
        could have observed."""
        alarm = self._alarms.get(alarm_id)
        return None if alarm is None else alarm.acknowledged

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
