import math

from app import config
from app.equipment.base import Equipment, INLET, OUTLET, signed_square
from app.equipment.ranges import checked
from app.statetypes import StateRow

# °F to °R: the polytropic relation is defined on absolute temperature, and
# this is the one place that conversion has to happen.
RANKINE_OFFSET = 459.67


class GasCompressor(Equipment):
    def __init__(self, tag: str = "K-101") -> None:
        super().__init__(
            tag,
            ports={
                "suction": INLET,
                "discharge": OUTLET,
            },
        )

        self.running = False

        self.suction_pressure_target = 675.0
        self.discharge_pressure_target = 875.0

        self.load = 0.0
        self.load_target = 0.0
        self.load_rate = config.LOAD_RATE_PER_SECOND

        self.flow_target = 100.0
        self.max_flow = 120.0

        self.shutoff_pressure_rise = 220.0
        self.compressor_resistance = 0.002

        self.base_temperature = 75.0
        self.max_temperature = 120.0
        self.max_spread = 220.0

        self.isentropic_exponent = 1.3
        self.polytropic_efficiency = 0.75

    @property
    def shutoff_pressure_rise(self) -> float:
        return self._shutoff_pressure_rise

    @shutoff_pressure_rise.setter
    def shutoff_pressure_rise(self, value: float) -> None:
        self._shutoff_pressure_rise = checked(
            self.tag, "shutoff_pressure_rise", value, 0.0,
        )

    @property
    def compressor_resistance(self) -> float:
        return self._compressor_resistance

    @compressor_resistance.setter
    def compressor_resistance(self, value: float) -> None:
        self._compressor_resistance = checked(
            self.tag, "compressor_resistance", value, 0.0, above=True,
        )

    @property
    def base_temperature(self) -> float:
        return self._base_temperature

    @base_temperature.setter
    def base_temperature(self, value: float) -> None:
        self._base_temperature = checked(
            self.tag, "base_temperature", value, -RANKINE_OFFSET, above=True,
        )

    @property
    def isentropic_exponent(self) -> float:
        return self._isentropic_exponent

    @isentropic_exponent.setter
    def isentropic_exponent(self, value: float) -> None:
        self._isentropic_exponent = checked(
            self.tag, "isentropic_exponent", value, 1.0, 5.0 / 3.0, above=True,
        )

    @property
    def polytropic_efficiency(self) -> float:
        return self._polytropic_efficiency

    @polytropic_efficiency.setter
    def polytropic_efficiency(self, value: float) -> None:
        self._polytropic_efficiency = checked(
            self.tag, "polytropic_efficiency", value, 0.0, 1.0, above=True,
        )

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.load_target = 0.0

    def set_load_target(self, target: float) -> None:
        self.load_target = max(
            0.0,
            min(target, 1.0),
        )

    def temperature_at(
        self,
        suction_pressure: float,
        discharge_pressure: float,
    ) -> float:
        for label, pressure in (
            ("suction_pressure", suction_pressure),
            ("discharge_pressure", discharge_pressure),
        ):
            if not math.isfinite(pressure) or pressure <= 0.0:
                raise ValueError(
                    f"{label} must be finite and positive, got {pressure!r}",
                )

        ratio = discharge_pressure / suction_pressure
        exponent = (
            (self.isentropic_exponent - 1.0)
            / (self.isentropic_exponent * self.polytropic_efficiency)
        )
        suction_rankine = self.base_temperature + RANKINE_OFFSET

        return suction_rankine * float(ratio ** exponent) - RANKINE_OFFSET

    def integrate(self, dt: float) -> None:
        load_target = (
            self.load_target
            if self.running
            else 0.0
        )

        self.load = self._move_toward(
            self.load,
            load_target,
            self.load_rate,
            dt,
        )

    def characteristic(self, flow: float) -> float:
        return (
            self.shutoff_pressure_rise * self.load ** 2
            - self.compressor_resistance * signed_square(flow)
        )

    def get_state(self) -> StateRow:
        return {
            "running": self.running,
            "load": self.load,
            "load_target": self.load_target,
            "suction_pressure_target": self.suction_pressure_target,
            "discharge_pressure_target": self.discharge_pressure_target,
            "flow_target": self.flow_target,
            "max_spread": self.max_spread,
            "max_flow": self.max_flow,
            "max_temperature": self.max_temperature,
        }
