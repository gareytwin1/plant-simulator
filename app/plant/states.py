"""
Plant state machine (T12-3): where the whole plant stands in its start-up and
shut-down, as a layer above the equipment.

Six states, and the only moves between them:

    COLD -> PURGED -> PRESSURISED -> CIRCULATING -> ON_SPEC
                                          ^             |
                                          +-------------+   (fell off spec)

    PURGED, PRESSURISED, CIRCULATING, ON_SPEC -> SHUTTING_DOWN -> COLD

Any other move is **illegal** and raises `IllegalTransition`: cold cannot jump
to on-spec, and a plant mid shut-down cannot start back up without first
reaching cold.

**A transition is gated by what the plant is doing, never by a button.** Each
edge carries conditions, the same `"<tag>.<variable> <op> <threshold>"`
comparisons a `Permissive` holds, and `advance` moves only when every one is
met on the `Snapshot` it is given. The plant numbers (what pressure counts as
pressurised, what flow as circulating) are not in this module: they are the
caller's, passed to the constructor, because they belong to a plant and a
sequence (T12-4's `config/sequences/*.yaml`), not to the machine. Every edge
needs at least one condition except those into SHUTTING_DOWN: stopping the
plant is a decision an operator or a trip must always be able to make, so no
process condition may refuse it. A reading that is missing, not a number or
not finite blocks, as it does in `Permissive`: a lost transmitter must not
advance the plant.

**The machine owns no process state.** It reads a `Snapshot` and holds only its
current state; it writes nothing to a device, the arbiter or the engine, and it
has no clock, so it is deterministic by construction. State changes only
through `advance`, which a caller (a startup sequence, T12-4) calls between
engine steps.

Trips are not live in a session yet (nothing calls `TripSystem.update`), so
nothing enters SHUTTING_DOWN on its own: a caller does, through `advance`.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from enum import StrEnum

from app.engine.snapshot import Snapshot
from app.safety.actions import number
from app.safety.permissives import Permissive


class PlantState(StrEnum):
    COLD = "cold"
    PURGED = "purged"
    PRESSURISED = "pressurised"
    CIRCULATING = "circulating"
    ON_SPEC = "on_spec"
    SHUTTING_DOWN = "shutting_down"


Edge = tuple[PlantState, PlantState]

_RUNNING = (
    PlantState.PURGED,
    PlantState.PRESSURISED,
    PlantState.CIRCULATING,
    PlantState.ON_SPEC,
)

TRANSITIONS: frozenset[Edge] = frozenset(
    {
        (PlantState.COLD, PlantState.PURGED),
        (PlantState.PURGED, PlantState.PRESSURISED),
        (PlantState.PRESSURISED, PlantState.CIRCULATING),
        (PlantState.CIRCULATING, PlantState.ON_SPEC),
        (PlantState.ON_SPEC, PlantState.CIRCULATING),
        (PlantState.SHUTTING_DOWN, PlantState.COLD),
    }
    | {(state, PlantState.SHUTTING_DOWN) for state in _RUNNING},
)


class IllegalTransition(ValueError):
    """The requested move is not an edge of the state graph."""


class PlantStateMachine:
    def __init__(
        self,
        snapshot: Snapshot,
        gates: Mapping[Edge, Sequence[str | Permissive]],
        state: PlantState = PlantState.COLD,
    ) -> None:
        errors: list[str] = []
        parsed: dict[Edge, tuple[Permissive, ...]] = {edge: () for edge in TRANSITIONS}
        rejected: set[Edge] = set()

        for edge, conditions in gates.items():
            source, target = edge

            if edge not in TRANSITIONS:
                errors.append(f"gate on {source} -> {target}, which is not a transition")
                continue

            if target is PlantState.SHUTTING_DOWN and conditions:
                errors.append(f"{source} -> {target} may not be gated, a shutdown is never refused")
                continue

            try:
                parsed[edge] = tuple(
                    condition
                    if isinstance(condition, Permissive)
                    else Permissive.parse(condition)
                    for condition in conditions
                )
            except ValueError as error:
                rejected.add(edge)
                errors.append(f"{source} -> {target}: {error}")

        for edge in sorted(TRANSITIONS):
            source, target = edge

            if target is not PlantState.SHUTTING_DOWN and not parsed[edge] and edge not in rejected:
                errors.append(f"{source} -> {target} has no condition to gate it")

        for edge, permissives in parsed.items():
            errors.extend(self._unresolved(edge, permissives, snapshot))

        if errors:
            raise ValueError(
                f"plant state machine rejected, {len(errors)} problem(s):\n"
                + "\n".join(f"  {error}" for error in errors),
            )

        self._gates = parsed
        self._state = PlantState(state)

    @property
    def state(self) -> PlantState:
        return self._state

    def blocked(self, target: PlantState, snapshot: Snapshot) -> tuple[str, ...]:
        """Every reason the plant may not move to `target` now, empty when it
        may. Raises `IllegalTransition` when `target` is not reachable from
        the current state at all."""
        edge = (self._state, PlantState(target))

        if edge not in TRANSITIONS:
            raise IllegalTransition(f"cannot go from {edge[0]} to {edge[1]}")

        return tuple(
            reason
            for permissive in self._gates[edge]
            if (reason := permissive.reason(snapshot)) is not None
        )

    def advance(self, target: PlantState, snapshot: Snapshot) -> tuple[str, ...]:
        """Move to `target` when every condition on the edge holds on
        `snapshot`. Returns the reasons it did not, and then the state is
        unchanged."""
        reasons = self.blocked(target, snapshot)

        if not reasons:
            self._state = PlantState(target)

        return reasons

    @staticmethod
    def _unresolved(
        edge: Edge,
        permissives: Sequence[Permissive],
        snapshot: Snapshot,
    ) -> list[str]:
        errors: list[str] = []

        for permissive in permissives:
            condition = permissive.condition
            point = f"{condition.tag}.{condition.variable}"

            if condition.tag not in snapshot.equipment:
                errors.append(
                    f"{edge[0]} -> {edge[1]} names unknown device {condition.tag!r}, "
                    f"only {sorted(snapshot.equipment)}",
                )
                continue

            row = snapshot.equipment[condition.tag]

            if condition.variable not in row:
                warnings.warn(
                    f"plant state gate {edge[0]} -> {edge[1]} condition {point} does "
                    f"not resolve against the equipment section the snapshot "
                    f"publishes and will always block",
                    stacklevel=3,
                )
            elif number(row[condition.variable]) is None:
                errors.append(f"{edge[0]} -> {edge[1]}: {point} is not a number")

        return errors
