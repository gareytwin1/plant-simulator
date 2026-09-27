"""
Trip actions on equipment (T11-2): what a TRIPPED interlock does to the plant.

T11-1's `Interlock` decides *whether* a trip has latched and carries its
`actions` as opaque strings. This module gives them meaning. An action is
`"<device tag>.<verb>"`, and `TRIP_ACTIONS` is an explicit per-class allowlist
of the verbs each device class accepts - same shape and same reason as
`app.controls.loader.OUTPUTS`, `app.disturbances.malfunction.WRITABLE` and
`app.api.action.ACTIONS`: a device's trippable surface is listed, never
inferred from a method that happens to share a name.

    stop     a machine (pump, compressor) stops running
    close    a valve is driven to its minimum travel
    open     a valve is driven fully open - a recycle or a vent
    fail     a valve is driven to its own configured fail position

**Every action reaches its device through `CommandArbiter` (T7-4), as an
interlock demand.** Nothing here calls a device method itself. A tripped
interlock holds its demands for as long as it stays TRIPPED, so it outranks
any operator or controller demand on the same output by precedence rather
than by running last, and releases them the moment it is no longer TRIPPED -
an `"auto"` interlock whose condition clears, or a `"manual"` one reset.
Releasing moves nothing of its own: the output falls back to whatever the
next source demands, or holds where the trip left it if nobody demands it. A
stopped machine therefore stays stopped after a reset until someone starts
it - unless a lower-precedence source is still holding a standing `RUN`
demand on it, which calls `start()` the moment the trip releases. That sets
only the run flag: the trip's `stop()` zeroed the speed or load target, so
the machine comes back running at zero until that target is demanded again.
Whoever posts a run demand owns that; gating a restart on what must be true
first is T11-3's.

**The arbiter carries numbers, so starting and stopping is a number.** A
machine's run command is its own arbiter output, `"<tag>.run"`, taking `RUN`
(1.0) or `STOP` (0.0); its actuator calls the machine's own `start()` or
`stop()`. Like a discrete output driven from an analog signal it switches at
the midpoint rather than refusing anything in between - the arbiter holds
demands standing, so an actuator that raised on one bad value would raise on
every apply after it, trips included. That keeps the run command separate from
the machine's load or speed target, which is a different output with a
different owner. A valve's output is its position target, named by the bare
valve tag and bound to `set_position_target` - exactly the output a loop
(T8-3) binds - so a trip on a loop-driven valve joins the loop's binding
rather than contending for a second one. The loop is not told its output is
overridden: in AUTO it keeps computing for the length of the trip and can
saturate, so handing back on release is not bumpless. Tracking a
loop to its arbitrated output belongs in `Engine._control`, a spine change
this task does not make. Sharing the binding needs the loop bound first:
build the `TripSystem` after the engine has every loop, as
`Engine.from_plant` does at construction, or `add_loop` will find the valve
already bound. The valve's own travel clamp turns
`close`'s 0.0 into `min_position`; a trip no more seals a resistance-only
valve than a signal loss does. What `fail` demands is read from the valve's
`fail_action` once, when the `TripSystem` is built - it is a design value,
not something a malfunction can move.

**A trip overrides every source, including what an operator wrote directly.**
`TripSystem.update` evaluates every interlock against the snapshot, posts or
releases each one's demands, and then applies the arbiter. It is called
between engine steps - like `MalfunctionRegistry.update` - and specifically
**immediately before `Engine.step`**, on the latest snapshot: the same point
in the cycle the engine's own control runs, reading the same pre-step view.
Applying there means any command written since the last step, through the
arbiter or around it, is overwritten by a standing trip before the next
integrate can act on it.

**A trip does nothing but move final elements.** What follows - a level
falling because the feed pump stopped, a second interlock latching on that
level - is the solver's answer to the new slow state, not anything this
module scripts. That is how a cascade trip propagates: through the network.

Like T9-4's `limits`, an interlock `condition` resolves only against a field a
device's own `get_state()` publishes, read from the snapshot's indicated
equipment section. One that names a solved node or branch quantity
(`K-101.discharge_pressure`) is warned about once, at construction, and then
never evaluated - the same tag-to-point resolver gap `app/engine/engine.py`'s
module docstring documents. Its actions are still validated and bound, so it
starts working the moment that resolver lands. A condition naming a device
the plant does not have is refused outright, like an unknown action: a
typo must not leave a trip silently inert. A condition that resolves
must read a number at construction; one that stops being a number mid-run,
or stops being published at all, is evaluated as NaN, which T11-1's
`Condition` fails safe on - a lost reading drives the trip timer rather than
reading as healthy.

Elapsed time is the snapshot's `sim_time` since the previous update - or,
for the first, since the snapshot the system was built on - so a paused
engine advances no delay.
"""

from __future__ import annotations

import math
import sys
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FrameType

from app.controls.arbitration import AGREEMENT, Actuator, CommandArbiter, Source
from app.engine.snapshot import Snapshot
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import FAIL_CLOSED, FAIL_OPEN, ControlValve
from app.plant.loader import Plant
from app.safety.interlocks import Interlock, load_interlocks
from app.statetypes import JSONValue

RUN = 1.0
STOP = 0.0

CLOSED = 0.0
OPEN = 1.0


@dataclass(frozen=True)
class Verb:
    """How one trip verb reaches a device: the arbiter output it drives, the
    actuator that output is bound to, and the value the trip demands."""

    output: Callable[[Equipment], str]
    actuator: Callable[[Equipment], Actuator]
    value: Callable[[Equipment], float]


def _run_output(device: Equipment) -> str:
    return f"{device.tag}.run"


def _run_actuator(device: Equipment) -> Actuator:
    machine = device
    assert isinstance(machine, (CentrifugalPump, GasCompressor))

    def actuate(value: float) -> None:
        if value >= (RUN + STOP) / 2.0:
            machine.start()
        else:
            machine.stop()

    return actuate


def _position_output(device: Equipment) -> str:
    return device.tag


def _position_actuator(device: Equipment) -> Actuator:
    assert isinstance(device, ControlValve)

    return device.set_position_target


def _fail_value(device: Equipment) -> float:
    assert isinstance(device, ControlValve)

    return FAIL_TARGETS[device.fail_action]


FAIL_TARGETS = {
    FAIL_CLOSED: CLOSED,
    FAIL_OPEN: OPEN,
}

STOP_MACHINE = Verb(_run_output, _run_actuator, lambda device: STOP)

# Explicit allowlist, not hasattr(): a device's trippable surface is listed,
# never inferred. Same shape as OUTPUTS, WRITABLE and the C5 ACTIONS.
TRIP_ACTIONS: dict[type[Equipment], dict[str, Verb]] = {
    CentrifugalPump: {
        "stop": STOP_MACHINE,
    },
    GasCompressor: {
        "stop": STOP_MACHINE,
    },
    ControlValve: {
        "close": Verb(_position_output, _position_actuator, lambda device: CLOSED),
        "open": Verb(_position_output, _position_actuator, lambda device: OPEN),
        "fail": Verb(_position_output, _position_actuator, _fail_value),
    },
}


@dataclass(frozen=True)
class TripAction:
    """One `"<tag>.<verb>"` action, resolved against its device."""

    tag: str
    verb: str
    output: str
    value: float


def resolve_action(text: str, equipment: Mapping[str, Equipment]) -> TripAction:
    tag, dot, verb = text.rpartition(".")

    if not dot or not tag or not verb:
        raise ValueError(f"malformed trip action {text!r}, expected '<tag>.<verb>'")

    if tag not in equipment:
        raise ValueError(
            f"trip action {text!r} names unknown device {tag!r}, "
            f"only {sorted(equipment)}",
        )

    device = equipment[tag]
    verbs = TRIP_ACTIONS.get(type(device), {})

    if verb not in verbs:
        raise ValueError(
            f"trip action {text!r}: {tag} is a {type(device).__name__}, "
            f"which allows only {sorted(verbs)}",
        )

    spec = verbs[verb]

    return TripAction(
        tag=tag,
        verb=verb,
        output=spec.output(device),
        value=spec.value(device),
    )


class TripSystem:
    def __init__(
        self,
        interlocks: Mapping[str, Interlock],
        equipment: Mapping[str, Equipment],
        arbiter: CommandArbiter,
        snapshot: Snapshot,
    ) -> None:
        self.interlocks = dict(interlocks)
        self.arbiter = arbiter
        self.actions: dict[str, tuple[TripAction, ...]] = {}

        errors: list[str] = []

        for tag, interlock in self.interlocks.items():
            try:
                self.actions[tag] = tuple(
                    resolve_action(text, equipment)
                    for text in interlock.definition.actions
                )
            except ValueError as error:
                errors.append(f"interlock {tag}: {error}")

        errors.extend(self._conflicts())
        unresolved: list[str] = []
        self.evaluated = self._resolve_conditions(snapshot, equipment, errors, unresolved)
        self._last_time = snapshot.sim_time

        if errors:
            raise ValueError(
                f"interlocks rejected, {len(errors)} problem(s):\n"
                + "\n".join(f"  {error}" for error in errors),
            )

        for message in unresolved:
            warnings.warn(message, stacklevel=_outside_this_module())

        for actions in self.actions.values():
            for action in actions:
                if action.output not in arbiter.outputs:
                    device = equipment[action.tag]
                    verb = TRIP_ACTIONS[type(device)][action.verb]
                    arbiter.bind(action.output, verb.actuator(device))

    @classmethod
    def from_plant(
        cls,
        plant: Plant,
        equipment: Mapping[str, Equipment],
        arbiter: CommandArbiter,
        snapshot: Snapshot,
    ) -> TripSystem:
        return cls(
            load_interlocks({"interlocks": plant.passthrough("interlocks")}),
            equipment,
            arbiter,
            snapshot,
        )

    @property
    def tripped(self) -> tuple[str, ...]:
        return tuple(tag for tag, interlock in self.interlocks.items() if interlock.tripped)

    def update(self, snapshot: Snapshot) -> None:
        """Evaluate every resolvable interlock on `snapshot`, hold or release
        each one's demands, and apply the arbiter. Call it immediately before
        `Engine.step`, on the latest snapshot - see the module docstring."""
        dt = snapshot.sim_time - self._last_time

        if dt < 0.0:
            raise ValueError(
                f"snapshot at sim_time {snapshot.sim_time} is older than the "
                f"last one this trip system saw, at {self._last_time}",
            )

        self._last_time = snapshot.sim_time

        for tag in self.evaluated:
            interlock = self.interlocks[tag]
            condition = interlock.definition.condition
            row = snapshot.equipment.get(condition.tag, {})
            value = _number(row.get(condition.variable))
            interlock.evaluate(math.nan if value is None else value, dt)

        for tag, interlock in self.interlocks.items():
            for action in self.actions[tag]:
                if interlock.tripped:
                    self.arbiter.demand(action.output, Source.INTERLOCK, tag, action.value)
                else:
                    self.arbiter.release(action.output, Source.INTERLOCK, tag)

        self.arbiter.apply()

    def _conflicts(self) -> list[str]:
        """Two interlocks demanding different values of one output would
        make the arbiter raise mid-run, on the step the second one latched.
        Refused here instead, where both can be named."""
        claims: dict[str, tuple[str, TripAction]] = {}
        errors: list[str] = []

        for tag, actions in self.actions.items():
            for action in actions:
                claim = claims.setdefault(action.output, (tag, action))
                other_tag, other = claim

                if not math.isclose(other.value, action.value, abs_tol=AGREEMENT):
                    errors.append(
                        f"interlock {tag}: {action.tag}.{action.verb} contradicts "
                        f"interlock {other_tag}'s {other.tag}.{other.verb}",
                    )

        return errors

    def _resolve_conditions(
        self,
        snapshot: Snapshot,
        equipment: Mapping[str, Equipment],
        errors: list[str],
        unresolved: list[str],
    ) -> tuple[str, ...]:
        """The interlocks whose condition reads a published number. A
        condition naming no device of this plant, or a published field that
        is not a number, is a configuration fault and joins `errors`; only an
        existing device's unpublished variable - the resolver gap - joins
        `unresolved`, to be warned about once construction succeeds."""
        evaluated: list[str] = []

        for tag, interlock in self.interlocks.items():
            condition = interlock.definition.condition
            point = f"{condition.tag}.{condition.variable}"

            if condition.tag not in equipment:
                errors.append(
                    f"interlock {tag}: condition names unknown device "
                    f"{condition.tag!r}, only {sorted(equipment)}",
                )
                continue

            row = snapshot.equipment.get(condition.tag, {})

            if condition.variable not in row:
                unresolved.append(
                    f"interlock {tag} condition {point} does not resolve "
                    f"against the equipment section the snapshot publishes "
                    f"and will not be evaluated",
                )
                continue

            if _number(row[condition.variable]) is None:
                errors.append(
                    f"interlock {tag}: condition {point} is "
                    f"{row[condition.variable]!r}, not a number",
                )
                continue

            evaluated.append(tag)

        return tuple(evaluated)


def _number(value: JSONValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    return float(value)


def _outside_this_module() -> int:
    """The `warnings.warn` stacklevel of the first frame outside this file,
    counted from the caller, so a warning names the code that built the trip
    system whichever constructor it went through. (`skip_file_prefixes` does
    not skip frames on the Python 3.12 this project pins.)"""
    frame: FrameType | None = sys._getframe(1)
    level = 1

    while frame is not None and frame.f_code.co_filename == __file__:
        frame = frame.f_back
        level += 1

    return level
