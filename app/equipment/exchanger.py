"""
Heat exchanger - UA-based duty against a fixed cold utility, mediated by a
metal wall with thermal mass (T6-3).

V1 models one moving stream: the process fluid flowing inlet to outlet. The
cooling medium on the other side is not a plant stream: it is a fixed
design temperature, `cold_temperature`, standing in for a utility whose own
flow and temperature this simulator does not track. That is the same level
of simplification the training goal asks for everywhere else: a real duty
without a second modelled stream or an LMTD calculation.

**The metal wall is what has thermal inertia, and duty is gated through it
alone.** `metal_temperature` is slow state that chases the process inlet
temperature at a bounded rate (`metal_response_rate`, °F/s), the same
rate-limited `_move_toward` every other device's slow state uses for a load
ramp or a valve stroke (C1). That helper is chosen over a self-referential
exponential lag specifically because `_move_toward` is stable for any `dt`:
`SimulationClock` places no ceiling on its speed multiplier, and a real
time-constant ODE integrated by explicit Euler oscillates once `dt` passes
twice the constant and diverges beyond that, a failure mode this device
cannot risk just to look more textbook. `duty`, the "cooling loss", is
`effective_ua * (metal_temperature - cold_temperature)`: UA times an
approach temperature, using only `metal_temperature` and `cold_temperature`,
never the live process inlet directly. That is deliberate: since duty never
reads the process inlet temperature directly, it cannot jump the instant
that temperature does. A step at the inlet only ever reaches duty by first
moving the metal, which `integrate(dt)` does at a bounded rate. Cooling loss
ramps because there is no path for it to do anything else.

At full equilibrium (metal fully settled on the inlet temperature) this
reduces exactly to the textbook `Q = UA * approach`, with approach measured
between the process inlet and the cold utility: the "Duty from UA and
approach temperature" the build plan asks for is this model's steady state,
not a separate formula.

Fouling is a single [0, 1] fraction of UA lost: `effective_ua = ua * (1 -
fouling)`. It scales `duty` directly at any `metal_temperature`, not only at
steady state.

`outlet_temperature` converts `duty` into a process-side temperature drop
using `app.plant.thermo.heat_capacity_rate`, so the process stream's own
energy balance closes by construction: the heat the process stream loses
(`heat_capacity_rate(inlet) * (inlet.temperature - outlet_temperature)`) is
`duty`, exactly, not to a tolerance.
"""

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

    @property
    def duty(self) -> float:
        """Heat rejected to the cold utility right now, in BTU/hr.

        A pure query over slow state only: `effective_ua`, `metal_temperature`
        and `cold_temperature`. It moves only across an `integrate(dt)` call,
        never within one.
        """
        return self.effective_ua * (self.metal_temperature - self.cold_temperature)

    def outlet_temperature(self, inlet: StreamState) -> float:
        """The process stream's temperature leaving the exchanger.

        Divides `duty` by the arriving stream's heat capacity rate. With no
        flow there is no honest delta-T (`heat_capacity_rate` is zero and the
        caller owns that case, see its docstring), so the inlet temperature
        passes through unchanged rather than dividing by zero.
        """
        rate = heat_capacity_rate(inlet)

        if rate <= 0.0:
            return inlet.temperature

        return inlet.temperature - self.duty / rate

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
            "duty": self.duty,
        }
