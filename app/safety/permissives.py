"""
Restart permissives and trip reset (T11-3): what must be true before a
stopped machine may run again.

T11-2's trips stop a machine; nothing there says when it may come back. A
`RestartGate` is that rule for one pump or compressor, and it is what makes
recovery a skill rather than a button. It blocks a restart on two counts:

    permissives   conditions that must hold before the machine starts, each
                  the same `"<tag>.<variable> <op> <threshold>"` comparison an
                  interlock uses, but read the other way round - a permissive
                  is what must be *true*, so it is satisfied when the
                  comparison is met.
    resets        interlocks whose trip must be acknowledged with `reset()`
                  before the machine may run, even one configured `"auto"`.
                  An auto interlock clears itself the moment its condition
                  does; the gate remembers that it tripped and keeps the
                  machine blocked until an operator resets it.

**A block is a standing STOP demand, not a refusal.** While the machine is
not running and anything blocks it, the gate holds a `Source.INTERLOCK` STOP
demand on the machine's `"<tag>.run"` output (T11-2), so an operator's or a
controller's RUN loses by precedence rather than by the gate patching a method
call - and the moment nothing blocks it the demand is released and a standing
RUN takes effect. The gate never stops a machine that is running: losing a
permissive after start is not a trip, and stopping on a process condition is
an interlock's job. `blocked` names every reason, so a caller can tell an
operator why the start did nothing.

**`reset()` cannot paper over a live condition.** It takes effect only for a
tripped interlock whose condition has cleared: a `"manual"` one is cleared by
`Interlock.reset()`, which refuses while its condition is still met, and an
`"auto"` one has already cleared itself. A reset attempted with the condition
still present changes nothing, the interlock stays TRIPPED and the machine
stays blocked, and if the condition returns after a successful reset the
interlock latches again on its own delay. A gate never resets an interlock it
was not given.

**Call order is a contract.** `RestartGate.update(snapshot)` runs between
engine steps like `TripSystem.update`, and **before it**: the gate must post
its hold before the trip system's apply, or a trip releasing that step would
let a standing RUN demand start the machine first. The gate learns of a trip
from the interlock's state after the previous update, so it must be updated
at least once while the interlock is TRIPPED, which any per-step caller does.

Like a trip condition, a permissive resolves only against a field a device's
own `get_state()` publishes, and one that names an existing device's
unpublished variable is warned about once, at construction, against the
snapshot the gate is built on. Unlike a trip condition, a reading that is missing, not a
number or not finite is **unsatisfied**: a lost transmitter must not permit a
start.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.controls.arbitration import CommandArbiter, Source
from app.engine.snapshot import Snapshot
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.safety.actions import TRIP_ACTIONS, resolve_action
from app.safety.interlocks import Condition, Interlock


@dataclass(frozen=True)
class Permissive:
    """One condition that must hold before the machine may start."""

    condition: Condition

    @classmethod
    def parse(cls, text: str) -> Permissive:
        return cls(Condition.parse(text))

    def reason(self, snapshot: Snapshot) -> str | None:
        """Why this permissive is not satisfied, or None when it is."""
        condition = self.condition
        value = snapshot.equipment.get(condition.tag, {}).get(condition.variable)
        point = f"{condition.tag}.{condition.variable}"
        wanted = f"{point} {condition.operator} {condition.threshold:g}"

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"permissive {wanted} not satisfied: {point} is not published"

        if not math.isfinite(value) or not condition.is_met(float(value)):
            return f"permissive {wanted} not satisfied: {point} reads {value:g}"

        return None


class RestartGate:
    def __init__(
        self,
        tag: str,
        equipment: Mapping[str, Equipment],
        arbiter: CommandArbiter,
        interlocks: Mapping[str, Interlock],
        snapshot: Snapshot,
        permissives: Sequence[Permissive] = (),
        resets: Sequence[str] = (),
    ) -> None:
        machine = equipment.get(tag)

        if not isinstance(machine, (CentrifugalPump, GasCompressor)):
            raise ValueError(f"restart gate {tag!r} must name a pump or compressor")

        unknown = sorted(set(resets) - set(interlocks))

        if unknown:
            raise ValueError(
                f"restart gate {tag} requires reset of unknown interlock(s) "
                f"{unknown}, only {sorted(interlocks)}",
            )

        for permissive in permissives:
            if permissive.condition.tag not in equipment:
                raise ValueError(
                    f"restart gate {tag}: permissive names unknown device "
                    f"{permissive.condition.tag!r}, only {sorted(equipment)}",
                )

        for permissive in permissives:
            condition = permissive.condition

            if condition.variable not in snapshot.equipment.get(condition.tag, {}):
                warnings.warn(
                    f"restart gate {tag} permissive {condition.tag}.{condition.variable} "
                    f"does not resolve against the equipment section the snapshot "
                    f"publishes and will always block a start",
                    stacklevel=2,
                )

        stop = resolve_action(f"{tag}.stop", equipment)
        self.tag = tag
        self.arbiter = arbiter
        self.permissives = tuple(permissives)
        self.interlocks = {name: interlocks[name] for name in resets}
        self._output = stop.output
        self._stop_value = stop.value
        self._requester = f"{tag}:restart-gate"
        self._awaiting: set[str] = set()
        self._reasons: tuple[str, ...] = ()

        if stop.output not in arbiter.outputs:
            arbiter.bind(stop.output, TRIP_ACTIONS[type(machine)]["stop"].actuator(machine))

    @property
    def blocked(self) -> tuple[str, ...]:
        """Every reason the machine may not start, as of the last update."""
        return self._reasons

    @property
    def awaiting_reset(self) -> tuple[str, ...]:
        return tuple(name for name in self.interlocks if name in self._awaiting)

    def update(self, snapshot: Snapshot) -> None:
        """Re-evaluate what blocks a restart on `snapshot`, then hold or
        release the STOP demand. Does not apply the arbiter: call it before
        `TripSystem.update`, which does - see the module docstring."""
        for name, interlock in self.interlocks.items():
            if interlock.tripped:
                self._awaiting.add(name)

        reasons = [
            reason
            for permissive in self.permissives
            if (reason := permissive.reason(snapshot)) is not None
        ]
        reasons.extend(
            f"interlock {name} tripped, reset required" for name in self.awaiting_reset
        )
        self._reasons = tuple(reasons)

        running = snapshot.equipment.get(self.tag, {}).get("running") is True

        if self._reasons and not running:
            self.arbiter.demand(
                self._output,
                Source.INTERLOCK,
                self._requester,
                self._stop_value,
            )
        else:
            self.arbiter.release(self._output, Source.INTERLOCK, self._requester)

    def reset(self) -> bool:
        """Acknowledge every awaiting trip whose condition has cleared.
        Returns whether nothing is left awaiting a reset; a trip whose
        condition is still met stays TRIPPED and stays awaited."""
        for name in self.awaiting_reset:
            interlock = self.interlocks[name]

            if interlock.reset() or not interlock.tripped:
                self._awaiting.discard(name)

        return not self._awaiting
