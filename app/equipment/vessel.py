"""
Vessel: a coupling device whose liquid and gas inventory is slow state.

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

**Gas (T5-3).** The same physical vessel may also hold a gas phase, and it is
a second, separate piece of slow state: `pressure` (psia), integrated from
`gas_inlet_flow` and `gas_outlet_flow` in SCFM. GPM and SCFM never share an
attribute; which pair a port writes to is decided by the flow unit confirmed
at the node it attaches to (`app/engine/coupling.py`), never by the port's
name. The rate law is a lumped, isothermal ideal gas at the standard
temperature, which is the one temperature SCFM is defined at:

    dP/dt = P_std * (Q_in - Q_out) / V_gas

with P_std in psia, Q in SCFM and V_gas in ft^3, so the right-hand side is
psi per minute. It is dimensionally the ideal-gas law differentiated with
n proportional to P_std * (standard volume): the standard-volume inventory
`P * V_gas / P_std` (scf) changes by exactly the net SCFM, which is why mass
is conserved to rounding. No Z-factor, no temperature dynamics.

The gas phase is attachment-driven: a vessel with no confirmed SCFM
attachment never has it activated by the coupling, integrates no pressure
and publishes no gas fields, so a liquid-only vessel is exactly what it was.
The pressure is the vessel's own state and, unlike the liquid head, an
absolute one: the coupling writes it as the runtime boundary at every gas
attachment, and the as-built configured pressure is untouched. Nothing is
clamped. Pressure must stay strictly positive, and a step that would take it
to zero or below raises rather than being floored.

Range guards sit on the attributes rather than in `__init__` because design
values arrive by `setattr` from the C3 loader: a capacity of zero set from
config would otherwise surface much later as a division by zero, a long way
from the line that caused it.

**Configured ports (T5-7).** `Vessel` is the one device class that opts in to
receiving its structural port set from configuration rather than declaring a
fixed one: `accepts_configured_ports = True`. A C3 `ports` entry that gives
every port a `direction` replaces the default `inlet` / `outlet` pair below,
in config order — this is what lets a separator declare a liquid inlet, a
liquid outlet and a vapor outlet, or any other set a real plant needs. A
config that gives no port a `direction` leaves the two ports below untouched,
exactly as before T5-7. `accepts_configured_ports` is an internal capability
marker, not a design value: the loader refuses it under `design`, on any
device, because reading it from an instance rather than the class would let
it be set and silently ignored. See docs/ADR_0002_TYPED_PORTS.md,
Amendment 3.
"""

import math
from typing import ClassVar

from app import config
from app.equipment.base import Equipment, INLET, OUTLET
from app.statetypes import StateRow


class Vessel(Equipment):
    accepts_configured_ports: ClassVar[bool] = True

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

        self.gas_volume = 100.0                # ft^3, fixed gas space
        self.initial_pressure = config.STANDARD_PRESSURE   # psia, the design seed

        self.gas_inlet_flow = 0.0              # SCFM, written by the caller
        self.gas_outlet_flow = 0.0             # SCFM, written by the caller

        self._gas_active = False

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
    def gas_volume(self) -> float:
        return self._gas_volume

    @gas_volume.setter
    def gas_volume(self, value: float) -> None:
        self._gas_volume = _checked(self.tag, "gas_volume", value, 0.0, above=True)

    @property
    def initial_pressure(self) -> float:
        return self._initial_pressure

    @initial_pressure.setter
    def initial_pressure(self, value: float) -> None:
        # The design seed and the running state start equal, exactly as a
        # configured level is both. Set it once, from the loader.
        self._initial_pressure = _checked(
            self.tag,
            "initial_pressure",
            value,
            0.0,
            above=True,
        )
        self._pressure = self._initial_pressure

    @property
    def pressure(self) -> float:
        return self._pressure

    @pressure.setter
    def pressure(self, value: float) -> None:
        self._pressure = _checked(self.tag, "pressure", value, 0.0, above=True)

    @property
    def gas_active(self) -> bool:
        return self._gas_active

    def activate_gas(self) -> None:
        """Called by the coupling when it confirms an SCFM attachment."""
        self._gas_active = True

    @property
    def gas_inventory(self) -> float:
        return self.pressure * self.gas_volume / config.STANDARD_PRESSURE  # scf

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

        if self.gas_active:
            net_gas_flow = self.gas_inlet_flow - self.gas_outlet_flow   # SCFM

            self.pressure = (
                self.pressure
                + config.STANDARD_PRESSURE * net_gas_flow * dt / 60.0 / self.gas_volume
            )

    def characteristic(self, flow: float) -> float:
        return 0.0

    def get_state(self) -> StateRow:
        state: StateRow = {
            "level": self.level,                # [0, 1]
            "volume": self.volume,              # gal
            "head": self.head,                  # psi
            "carryover": self.carryover,
            "inlet_flow": self.inlet_flow,      # GPM
            "outlet_flow": self.outlet_flow,    # GPM
            "residence_time": self.residence_time,  # s, None with no outflow
        }

        if self.gas_active:
            state.update(
                {
                    "pressure": self.pressure,                  # psia
                    "gas_volume": self.gas_volume,              # ft^3
                    "gas_inventory": self.gas_inventory,        # scf
                    "gas_inlet_flow": self.gas_inlet_flow,      # SCFM
                    "gas_outlet_flow": self.gas_outlet_flow,    # SCFM
                },
            )

        return state


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
