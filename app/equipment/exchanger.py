"""
Heat exchanger - a fixed-coolant-effectiveness cooler, mediated by a metal
wall with thermal mass (T6-3). Implements `thermo.ThermalDevice`.

V1 models one moving stream: the process fluid flowing inlet to outlet. The
cooling medium on the other side is not a plant stream: it is a fixed
design temperature, `cold_temperature`, standing in for a utility whose own
flow and temperature this simulator does not track. That is the same level
of simplification the training goal asks for everywhere else: a real duty
without a second modelled stream or an LMTD calculation.

**`leaving_temperature` is the fixed-coolant effectiveness formula.** For an
arriving stream at temperature `T_in` and rate `|q * Cp|`:

    T_out = T_cold + (T_in - T_cold) * exp(-UA_eff / |q * Cp|)

`exp(-x)` for `x >= 0` is always in `(0, 1]`, so `T_out` is always a convex
combination of `T_cold` and `T_in` - the outlet can never overshoot past the
coolant or undershoot past the arriving stream, at any flow, in either flow
direction (`heat_capacity_rate` is signed by `arriving.flow`; taking its
magnitude is what makes reversed flow cool exactly like forward flow at the
same `|q|`, rather than skip cooling because a signed rate reads negative).
At exactly zero flow there is no honest rate to divide by, so the arriving
temperature passes through unchanged - the device's own version of the
zero-flow case `heat_capacity_rate` leaves for its caller.

**The metal wall's lag lives in `UA_eff`, never in the `T_in`/`T_cold` pair
the exponential interpolates between.** `metal_temperature` is slow state
that chases `inlet_temperature` at a bounded rate (`metal_response_rate`,
°F/s), the same rate-limited `_move_toward` every other device's slow state
uses for a load ramp or a valve stroke (C1) - chosen over a self-referential
exponential lag specifically because `_move_toward` is stable for any `dt`:
`SimulationClock` places no ceiling on its speed multiplier, and a real
time-constant ODE integrated by explicit Euler oscillates once `dt` passes
twice the constant and diverges beyond that, a failure mode this device
cannot risk just to look more textbook. How much of the metal's climb from
`cold_temperature` toward the arriving temperature it has covered - clamped
to `[0, 1]` - scales `effective_ua` down to `UA_eff`. A metal wall still at
`cold_temperature` (a fresh device, or fouling's `effective_ua` already at
zero) gives `UA_eff = 0`, `exp(0) = 1`, and `T_out = T_in`: no capability,
no heat transfer, exactly - never a division that could send the outlet past
either bound the way a fixed BTU/hr duty divided by a shrinking flow once
did. Because `UA_eff` and not `T_in` carries the lag, a change that moves
the metal changes `T_out` only by moving `UA_eff`, gradually, through
`integrate(dt)` - it can never let a jump in `T_in` or `T_cold` pass through
unlagged, and it can never send `T_out` outside `[T_cold, T_in]` regardless
of where `metal_temperature` itself has drifted to.

At full equilibrium (metal fully caught up to the arriving temperature) this
reduces exactly to the textbook fixed-coolant effectiveness relation at the
device's full rated UA - the "Duty from UA and approach temperature" the
build plan asks for is this model's steady state, not a separate formula.

Fouling is a single [0, 1] fraction of UA lost: `effective_ua = ua * (1 -
fouling)`, which scales `UA_eff` and therefore `duty` directly, at any
`metal_temperature` - full fouling gives `UA_eff = 0` regardless of the
metal's own state, the same pass-through `T_out = T_in` as a cold metal wall.

`duty(arriving)` is derived from `leaving_temperature`, not the other way
around: `heat_capacity_rate(arriving) * (arriving.temperature -
leaving_temperature(...))`, using the same `|q * Cp|` magnitude. A test
comparing the two can never find daylight between them.

**Known interim limitation, not fixed here.** Nothing in a running plant
writes `inlet_temperature` yet - wiring the engine's arriving temperature
into a device's own slow state is an engine/transport design change, tracked
as separate follow-up work after T6-5. Until then `metal_temperature` chases
`inlet_temperature`'s constructor default rather than what actually arrives,
so the metal-capability fraction (and therefore `UA_eff`) is disconnected
from the live stream in a running plant. The bounds above hold regardless:
`UA_eff` is always clamped into `[0, effective_ua]`, so `T_out` is always in
`[T_cold, T_in]` no matter what state `metal_temperature` has drifted to.
"""

import math

from app import config
from app.equipment.base import Equipment, INLET, OUTLET, signed_square
from app.equipment.ranges import checked
from app.plant.thermo import REFERENCE_TEMPERATURE, StreamState, heat_capacity_rate
from app.statetypes import StateRow


# °F to °R: matches compressor.py's RANKINE_OFFSET. Bounds a temperature
# design value at absolute zero rather than trusting a caller not to pass
# one below it.
RANKINE_OFFSET = 459.67


class HeatExchanger(Equipment):
    def __init__(self, tag: str = "E-101") -> None:
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.ua = 5000.0                     # BTU/(hr·°F), clean (unfouled)
        self.fouling = 0.0                   # [0, 1], fraction of ua lost

        self.cold_temperature = 90.0         # °F, fixed cooling-medium temperature
        self.metal_response_rate = config.EXCHANGER_METAL_RESPONSE_RATE_PER_SECOND

        self.metal_temperature = self.cold_temperature  # °F, slow state
        self.inlet_temperature = REFERENCE_TEMPERATURE  # °F, written by the caller

        self.exchanger_resistance = 0.0005

    @property
    def ua(self) -> float:
        return self._ua

    @ua.setter
    def ua(self, value: float) -> None:
        self._ua = checked(self.tag, "ua", value, 0.0, above=True)

    @property
    def fouling(self) -> float:
        return self._fouling

    @fouling.setter
    def fouling(self, value: float) -> None:
        self._fouling = checked(self.tag, "fouling", value, 0.0, 1.0)

    @property
    def cold_temperature(self) -> float:
        return self._cold_temperature

    @cold_temperature.setter
    def cold_temperature(self, value: float) -> None:
        self._cold_temperature = checked(
            self.tag, "cold_temperature", value, -RANKINE_OFFSET, above=True,
        )

    @property
    def metal_response_rate(self) -> float:
        return self._metal_response_rate

    @metal_response_rate.setter
    def metal_response_rate(self, value: float) -> None:
        self._metal_response_rate = checked(
            self.tag, "metal_response_rate", value, 0.0, above=True,
        )

    @property
    def exchanger_resistance(self) -> float:
        return self._exchanger_resistance

    @exchanger_resistance.setter
    def exchanger_resistance(self, value: float) -> None:
        self._exchanger_resistance = checked(
            self.tag, "exchanger_resistance", value, 0.0, above=True,
        )

    @property
    def effective_ua(self) -> float:
        return self.ua * (1.0 - self.fouling)

    def leaving_temperature(
        self,
        arriving: StreamState,
        inlet_pressure: float,
        outlet_pressure: float,
    ) -> float:
        """`thermo.ThermalDevice`. Pressures are unused: the cold utility is
        a fixed design temperature, not a solved node, so nothing here reads
        them."""
        return self._leaving(arriving)

    def duty(self, arriving: StreamState) -> float:
        """Heat `arriving` actually loses to the cold utility, in BTU/hr -
        positive whenever the leaving temperature is below the arriving one,
        forward or reversed. Derived from `leaving_temperature`, so this and
        an independent `heat_capacity_rate(arriving) * (arriving.temperature
        - leaving_temperature(...))` calculation can never disagree.
        """
        return abs(heat_capacity_rate(arriving)) * (
            arriving.temperature - self._leaving(arriving)
        )

    def _leaving(self, arriving: StreamState) -> float:
        rate = abs(heat_capacity_rate(arriving))

        if rate <= 0.0:
            return arriving.temperature

        approach = arriving.temperature - self.cold_temperature

        if approach <= 0.0:
            # Already at or below the coolant: nothing to cool, and the
            # metal-capability fraction below has no sign to be a fraction
            # of.
            return arriving.temperature

        fraction = min(
            max(
                (self.metal_temperature - self.cold_temperature) / approach,
                0.0,
            ),
            1.0,
        )
        effective = self.effective_ua * fraction

        return self.cold_temperature + approach * math.exp(-effective / rate)

    def integrate(self, dt: float) -> None:
        self.metal_temperature = self._move_toward(
            self.metal_temperature,
            self.inlet_temperature,
            self.metal_response_rate,
            dt,
        )

    def characteristic(self, flow: float) -> float:
        return -self.exchanger_resistance * signed_square(flow)

    def get_state(self) -> StateRow:
        return {
            "ua": self.ua,
            "fouling": self.fouling,
            "effective_ua": self.effective_ua,
            "cold_temperature": self.cold_temperature,
            "metal_temperature": self.metal_temperature,
            "inlet_temperature": self.inlet_temperature,
        }
