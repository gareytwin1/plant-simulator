from app import config
from app.equipment.base import Equipment, INLET, OUTLET, signed_square
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

    def temperature_at(self, spread: float) -> float:
        ratio = (
            (self.suction_pressure_target + spread)
            / self.suction_pressure_target
        )
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
