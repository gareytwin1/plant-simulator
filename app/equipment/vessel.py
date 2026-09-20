"""
Vessel: a coupling device whose liquid inventory is slow state.

A vessel is not a branch. It declares ports and no hydraulic path (ADR 0001,
A4), so it holds no Branch, sits in no Topology and lives only in
`Plant.devices`. It terminates one flow domain and originates another through
its ports, and what couples them is inventory, never a shared flow variable.

Level is integrated by `integrate(dt)` from the net liquid flow, exactly like
any other slow state (D11). The flows arrive as plain attributes that a caller
writes, the same way the pump's `speed_target` does: the vessel never reads a
port, node or branch. `app/engine/coupling.py` (T5-2) is the caller that
writes them from the solved network.

`head` is the other half of that coupling and the reason `head_at_full`
exists: it is the pressure the inventory offers the domain the vessel
originates, and the engine adds it to that domain's as-built boundary. It is
a linear map of level and nothing more — no fluid density, no elevation, no
thermodynamics. The vessel publishes a number; where the boundary lands and
what the plant does about it belongs to the engine and the solver.

`head` is deliberately not an absolute pressure. An empty vessel offers zero
head, not zero psia, and the base it is added to is the node's own configured
pressure. A vessel that owned the absolute boundary would put a battery limit
at 0 psia the moment it drained.

Range guards sit on the attributes rather than in `__init__` because design
values arrive by `setattr` from the C3 loader: a capacity of zero set from
config would otherwise surface much later as a division by zero, a long way
from the line that caused it.
"""

import math

from app.equipment.base import Equipment, INLET, OUTLET
from app.statetypes import StateRow


class Vessel(Equipment):
    def __init__(self, tag: str = "V-101") -> None:
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.capacity = 1000.0                 # gal, liquid volume at level 1.0

        self.head_at_full = 5.0                # psi offered at level 1.0
        self.carryover_level = 0.90            # [0, 1], where carryover starts

        self.level = 0.5                       # [0, 1]

        self.inlet_flow = 0.0                  # GPM, written by the caller
        self.outlet_flow = 0.0                 # GPM, written by the caller

    @property
    def capacity(self) -> float:
        return self._capacity

    @capacity.setter
    def capacity(self, value: float) -> None:
        self._capacity = _checked(self.tag, "capacity", value, 0.0, above=True)

    @property
    def head_at_full(self) -> float:
        return self._head_at_full

    @head_at_full.setter
    def head_at_full(self, value: float) -> None:
        self._head_at_full = _checked(self.tag, "head_at_full", value, 0.0)

    @property
    def carryover_level(self) -> float:
        return self._carryover_level

    @carryover_level.setter
    def carryover_level(self, value: float) -> None:
        self._carryover_level = _checked(
            self.tag,
            "carryover_level",
            value,
            0.0,
            1.0,
        )

    @property
    def level(self) -> float:
        return self._level

    @level.setter
    def level(self, value: float) -> None:
        self._level = _checked(self.tag, "level", value, 0.0, 1.0)

    @property
    def volume(self) -> float:
        return self.level * self.capacity      # gal

    @property
    def head(self) -> float:
        return self.head_at_full * self.level  # psi

    @property
    def carryover(self) -> bool:
        return self.level >= self.carryover_level

    @property
    def residence_time(self) -> float | None:
        if self.outlet_flow <= 0.0:
            return None

        return self.volume / self.outlet_flow * 60.0    # s

    def integrate(self, dt: float) -> None:
        net_flow = self.inlet_flow - self.outlet_flow   # GPM

        self.level = max(
            0.0,
            min(
                self.level + net_flow * dt / 60.0 / self.capacity,
                1.0,
            ),
        )

    def characteristic(self, flow: float) -> float:
        return 0.0

    def get_state(self) -> StateRow:
        return {
            "level": self.level,                # [0, 1]
            "volume": self.volume,              # gal
            "head": self.head,                  # psi
            "carryover": self.carryover,
            "inlet_flow": self.inlet_flow,      # GPM
            "outlet_flow": self.outlet_flow,    # GPM
            "residence_time": self.residence_time,  # s, None with no outflow
        }


def _checked(
    tag: str,
    name: str,
    value: float,
    lowest: float,
    highest: float | None = None,
    above: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{tag}.{name} must be a number, got {type(value).__name__} "
            f"{value!r}",
        )

    number = float(value)

    if not math.isfinite(number):
        raise ValueError(
            f"{tag}.{name} must be finite, got {value!r}",
        )

    floor = f"above {lowest}" if above else f"at least {lowest}"
    ceiling = "" if highest is None else f" and at most {highest}"

    if number < lowest or (above and number == lowest):
        raise ValueError(f"{tag}.{name} must be {floor}{ceiling}, got {value!r}")

    if highest is not None and number > highest:
        raise ValueError(f"{tag}.{name} must be {floor}{ceiling}, got {value!r}")

    return number
