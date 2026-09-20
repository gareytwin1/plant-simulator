"""
Vessel: a coupling device whose liquid inventory is slow state.

A vessel is not a branch. It declares ports and no hydraulic path (ADR 0001,
A4), so it holds no Branch, sits in no Topology and lives only in
`Plant.devices`. It terminates one flow domain and originates another through
its ports, and what couples them is inventory, never a shared flow variable.

Level is integrated by `integrate(dt)` from the net liquid flow, exactly like
any other slow state (D11). The flows arrive as plain attributes that a caller
writes, the same way the pump's `speed_target` does: the vessel never reads a
port, node or branch. Writing them from the solved network is T5-2.
"""

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

        self.level = 0.5                       # [0, 1]

        self.inlet_flow = 0.0                  # GPM, written by the caller
        self.outlet_flow = 0.0                 # GPM, written by the caller

    @property
    def volume(self) -> float:
        return self.level * self.capacity      # gal

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
            "inlet_flow": self.inlet_flow,      # GPM
            "outlet_flow": self.outlet_flow,    # GPM
            "residence_time": self.residence_time,  # s, None with no outflow
        }
