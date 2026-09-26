"""
Relief valve - a spring-loaded resistance with hysteresis (T7-5).

A pressure relief valve is a resistance, exactly like `ControlValve`, with one
difference: what it holds is not a commanded position but a pop-action
decision, made against a pressure rather than a signal. It pops fully open at
`set_pressure` and stays open until the pressure falls to `reseat_pressure`
(`set_pressure - blowdown`), which is lower - the hysteresis band that stops a
valve sitting exactly at set pressure from chattering open and shut every
step. Between the two thresholds it holds whichever state it was already in.

**Closed is a small leak, not a seal**, for the same reason `ControlValve`
floors travel at `min_position` rather than zero: `effective_capacity` cannot
be zero without `characteristic` dividing by it, and a curve that goes
vertical at closed is a flat region with no gradient for the solver to
descend. `CLOSED_LEAK_FRACTION` is that floor's relief-valve equivalent.

**`inlet_pressure` is a plain attribute, not a sensed value** - the same
bargain `Vessel.inlet_flow` makes: a device never reads a node (C1), so
whatever the valve sees at its inlet has to arrive as a value a caller
writes. `app/engine/coupling.py` is that caller: `VesselCoupling.
write_boundary_pressures()` writes the vessel's gas pressure onto every
relief valve whose inlet faces one of the vessel's gas exchange nodes, right
alongside writing that same pressure onto the node itself, so the two are
never a step apart. `FLOW_UNITS` lists this device as unit-neutral (alongside
`ControlValve`), which is what lets it share a vessel's boundary node through
`Engine.from_plant()` at all. Constructed on its own, with no vessel and no
engine, `inlet_pressure` stays whatever a test sets it to - the same bargain
`Vessel.gas_inlet_flow` makes standing alone in `tests/test_gas_inventory.py`.

The curve is the same shape as `ControlValve`'s, because a lifted relief
valve and a wide-open control valve are the same thing hydraulically:

    characteristic(q) = -signed_square(q) / effective_capacity ** 2

`capacity` is `effective_capacity` when lifted, `capacity * CLOSED_LEAK_
FRACTION` otherwise - a jump in the *coefficient*, not in the flow itself, so
the curve stays continuous and finite at every flow on both sides of the
lift/reseat transition, exactly as C1 requires.

Where this connects to the plant: the vessel-side port this valve discharges
from is what carries `purpose: relief` (ADR 0002 §3.4, Amendment 1 A.3) - a
property of the *vessel's* nozzle, declared in configuration, never inferred
from this device or its own ports. `purpose` is metadata; a relief withdrawal
sums into the same `gas_outlet_flow` aggregate as any process withdrawal,
with no separate accounting and no special case in the balance.
"""

from app.equipment.base import Equipment, INLET, OUTLET, signed_square
from app.equipment.ranges import checked as _checked
from app.statetypes import StateRow


# A lifted valve is wide open at `capacity`; closed leaks at this fraction of
# it. Not zero, for the reason `ControlValve.min_position` is not zero either:
# `effective_capacity` divides `characteristic`, so zero would make a closed
# valve's curve blow up rather than merely steep.
CLOSED_LEAK_FRACTION = 1e-3


class ReliefValve(Equipment):
    def __init__(self, tag: str = "PSV-101") -> None:
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self._set_pressure = 250.0
        self._blowdown = 10.0
        self._capacity = 50.0

        self.lifted = False
        self.inlet_pressure = 14.696  # psia, written by a caller - see above

    @property
    def set_pressure(self) -> float:
        return self._set_pressure

    @set_pressure.setter
    def set_pressure(self, value: float) -> None:
        checked = _checked(self.tag, "set_pressure", value, 0.0, above=True)
        self._check_hysteresis_band(checked, self._blowdown)
        self._set_pressure = checked

    @property
    def blowdown(self) -> float:
        return self._blowdown

    @blowdown.setter
    def blowdown(self, value: float) -> None:
        checked = _checked(self.tag, "blowdown", value, 0.0, above=True)
        self._check_hysteresis_band(self._set_pressure, checked)
        self._blowdown = checked

    @property
    def capacity(self) -> float:
        return self._capacity

    @capacity.setter
    def capacity(self, value: float) -> None:
        self._capacity = _checked(self.tag, "capacity", value, 0.0, above=True)

    @property
    def reseat_pressure(self) -> float:
        return self._set_pressure - self._blowdown

    @property
    def effective_capacity(self) -> float:
        return self.capacity if self.lifted else self.capacity * CLOSED_LEAK_FRACTION

    def integrate(self, dt: float) -> None:
        if self.inlet_pressure >= self.set_pressure:
            self.lifted = True
        elif self.inlet_pressure <= self.reseat_pressure:
            self.lifted = False

    def characteristic(self, flow: float) -> float:
        return -signed_square(flow) / self.effective_capacity ** 2

    def get_state(self) -> StateRow:
        return {
            "lifted": self.lifted,
            "inlet_pressure": self.inlet_pressure,
            "set_pressure": self.set_pressure,
            "reseat_pressure": self.reseat_pressure,
            "effective_capacity": self.effective_capacity,
        }

    def _check_hysteresis_band(self, set_pressure: float, blowdown: float) -> None:
        if blowdown >= set_pressure:
            raise ValueError(
                f"{self.tag}.blowdown ({blowdown}) must be less than "
                f"set_pressure ({set_pressure}) - the reseat pressure must "
                f"stay a positive absolute pressure",
            )
