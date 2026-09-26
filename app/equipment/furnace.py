"""
Furnace - a fired heater whose firing rate ramps toward an operator- or
malfunction-set duty setpoint, and whose outlet temperature follows that
firing rate through a straight energy balance. Implements
`thermo.ThermalDevice`. The cracking section's heart (V1.1): a second
heat-addition device once `HeatExchanger` (T6-3) already proves the
transport hookup.

**`firing_rate` is the only slow state, and it is what carries the
inertia.** It chases `duty_setpoint` at `firing_ramp_rate` (BTU/hr per
second) via the same rate-limited `_move_toward` every other device's slow
state uses - a burner cannot jump from cold to full fire, or from firing to
dark, in one step. Because `leaving_temperature` reads `firing_rate`
directly rather than `duty_setpoint`, a step in the setpoint reaches the
outlet only as fast as firing_rate can ramp there: exactly the "first-order
outlet response" and "trip to zero firing cools at the expected rate" the
build plan asks for, with no second lagged state needed.

This is deliberately simpler than `HeatExchanger`'s metal-wall lag, and for
a reason worth stating: that lag exists there to chase the *arriving*
temperature, which `integrate(dt)` has no way to read (T6-3's documented
"nothing writes inlet_temperature" limitation - an engine/transport gap, not
this device's problem). `firing_rate` chases a target that is entirely the
furnace's own state, so it never needs a sensed value `integrate` cannot
see, and inherits none of that limitation.

**`leaving_temperature` is a straight energy balance**, unlike the
exchanger's bounded effectiveness formula, and that asymmetry is intentional
rather than a shortcut. A coolant physically bounds how cold an exchanger's
outlet can go; nothing bounds how hot a fired heater's outlet can go when
firing continues into a flow that is small or lost; a real fired heater
runs away and coke or ruptures its tubes in exactly that scenario. An
unbounded `T_in + firing_rate / |q * Cp|` is that hazard, not a bug -
matching the build plan's own description of this device as "the source of
the plant's most dramatic upsets." `abs(heat_capacity_rate(arriving))`
still makes firing heat a reversed stream exactly as it would a forward
one, the same reversal symmetry every thermal device in this simulator
keeps.

At exactly zero flow there is no honest rate to divide by, so - as
`heat_capacity_rate` documents and `HeatExchanger` also does - the arriving
temperature passes through unchanged; a duty into a stagnant stream is a
transient against surrounding metal this device does not model.

`duty(arriving)` is derived from `leaving_temperature`, not the other way
around, so a test comparing the two can never find daylight between them.
"""

from app import config
from app.equipment.base import Equipment, INLET, OUTLET, signed_square
from app.equipment.ranges import checked
from app.plant.thermo import StreamState, heat_capacity_rate
from app.statetypes import StateRow


class Furnace(Equipment):
    def __init__(self, tag: str = "H-101") -> None:
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.max_duty = 20_000_000.0   # BTU/hr, design firing limit
        self.duty_setpoint = 0.0       # BTU/hr, operator/malfunction target
        self.firing_rate = 0.0         # BTU/hr, slow state - ramps toward duty_setpoint
        self.firing_ramp_rate = config.FURNACE_FIRING_RAMP_RATE_PER_SECOND

        self.furnace_resistance = 0.0005

    @property
    def max_duty(self) -> float:
        return self._max_duty

    @max_duty.setter
    def max_duty(self, value: float) -> None:
        self._max_duty = checked(self.tag, "max_duty", value, 0.0, above=True)

    @property
    def firing_ramp_rate(self) -> float:
        return self._firing_ramp_rate

    @firing_ramp_rate.setter
    def firing_ramp_rate(self, value: float) -> None:
        self._firing_ramp_rate = checked(
            self.tag, "firing_ramp_rate", value, 0.0, above=True,
        )

    @property
    def furnace_resistance(self) -> float:
        return self._furnace_resistance

    @furnace_resistance.setter
    def furnace_resistance(self, value: float) -> None:
        self._furnace_resistance = checked(
            self.tag, "furnace_resistance", value, 0.0, above=True,
        )

    def set_duty_setpoint(self, target: float) -> None:
        self.duty_setpoint = max(0.0, min(target, self.max_duty))

    def trip(self) -> None:
        self.set_duty_setpoint(0.0)

    def leaving_temperature(
        self,
        arriving: StreamState,
        inlet_pressure: float,
        outlet_pressure: float,
    ) -> float:
        """`thermo.ThermalDevice`. Pressures are unused: firing duty is a
        design/operator input, never something read off a solved node."""
        return self._leaving(arriving)

    def duty(self, arriving: StreamState) -> float:
        return abs(heat_capacity_rate(arriving)) * (
            self._leaving(arriving) - arriving.temperature
        )

    def _leaving(self, arriving: StreamState) -> float:
        rate = abs(heat_capacity_rate(arriving))

        if rate <= 0.0:
            return arriving.temperature

        return arriving.temperature + self.firing_rate / rate

    def integrate(self, dt: float) -> None:
        self.firing_rate = self._move_toward(
            self.firing_rate,
            self.duty_setpoint,
            self.firing_ramp_rate,
            dt,
        )

    def characteristic(self, flow: float) -> float:
        return -self.furnace_resistance * signed_square(flow)

    def get_state(self) -> StateRow:
        return {
            "duty_setpoint": self.duty_setpoint,
            "firing_rate": self.firing_rate,
            "max_duty": self.max_duty,
        }
