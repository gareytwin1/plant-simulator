from app import config
from app.equipment.base import Equipment, INLET, OUTLET
from app.statetypes import StateRow


class GasCompressor(Equipment):
    def __init__(self, tag: str = "K-101") -> None:
        super().__init__(
            tag,
            ports={
                "suction": INLET,
                "discharge": OUTLET,
            },
        )

        self.simulation_speed = config.SIMULATION_SPEED
        self.running = False

        self.upstream_boundary_pressure = 750.0
        self.downstream_boundary_pressure = 750.0

        self.suction_pressure = self.upstream_boundary_pressure
        self.suction_pressure_target = 675.0

        self.discharge_pressure = self.downstream_boundary_pressure
        self.discharge_pressure_target = 875.0

        self.load = 0.0
        self.load_target = 0.0
        self.load_rate = config.LOAD_RATE_PER_SECOND

        self.flow = 0.0
        self.flow_target = 100.0
        self.max_flow = 120.0

        self.shutoff_pressure_rise = 220.0
        self.compressor_resistance = 0.002
        self.suction_resistance = 0.0075
        self.discharge_resistance = 0.0125

        self.base_temperature = 75.0
        self.max_temperature = 120.0
        self.max_spread = 220.0

        self.downstream_restriction = 0.0

        self.discharge_valve_position = 1.0
        self.discharge_valve_target = 1.0
        self.discharge_valve_rate = (
            config.DISCHARGE_VALVE_RATE_PER_SECOND
        )
        self.valve_resistance_scale = 0.0025

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

    def set_discharge_valve_position(self, position: float) -> None:
        self.discharge_valve_target = max(
            0.10,
            min(position, 1.0),
        )

    @property
    def spread(self) -> float:
        return (
            self.discharge_pressure
            - self.suction_pressure
        )

    @property
    def temperature(self) -> float:
        if self.spread <= 150.0:
            return (
                self.base_temperature
                + self.spread / 150.0 * 3.0
            )

        if self.spread <= 200.0:
            return (
                78.0
                + (self.spread - 150.0) * 0.18
            )

        return min(
            87.0
            + (self.spread - 200.0) * 1.65,
            self.max_temperature,
        )

    @property
    def system_resistance(self) -> float:
        return (
            self.suction_resistance
            + self.discharge_resistance
            + self.downstream_restriction
            + self.valve_resistance
        )

    @property
    def valve_resistance(self) -> float:
        return self.valve_resistance_scale * (
            1.0 / self.discharge_valve_position ** 2
            - 1.0
        )

    @property
    def boundary_pressure_difference(self) -> float:
        return (
            self.downstream_boundary_pressure
            - self.upstream_boundary_pressure
        )

    @property
    def compressor_pressure_rise(self) -> float:
        return self.characteristic(self.flow)

    @property
    def valve_pressure_drop(self) -> float:
        return (
            self.valve_resistance
            * self.flow ** 2
        )

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

        self.discharge_valve_position = self._move_toward(
            self.discharge_valve_position,
            self.discharge_valve_target,
            self.discharge_valve_rate,
            dt,
        )

    def characteristic(self, flow: float) -> float:
        return max(
            self.shutoff_pressure_rise * self.load ** 2
            - self.compressor_resistance * flow ** 2,
            0.0,
        )

    def step(self, dt: float | None = None) -> None:
        if dt is None:
            dt = config.SIMULATION_STEP_SECONDS

        dt *= self.simulation_speed

        self.integrate(dt)

        (
            self.flow,
            self.suction_pressure,
            self.discharge_pressure,
        ) = self._calculate_operating_point()

    def _calculate_operating_point(self) -> tuple[float, float, float]:
        available_pressure_rise = (
            self.characteristic(0.0)
            - self.boundary_pressure_difference
        )

        if available_pressure_rise <= 0.0:
            return (
                0.0,
                self.upstream_boundary_pressure,
                self.downstream_boundary_pressure,
            )

        flow = (
            available_pressure_rise
            / (
                self.compressor_resistance
                + self.system_resistance
            )
        ) ** 0.5

        flow = min(
            flow,
            self.max_flow,
        )

        suction_pressure = (
            self.upstream_boundary_pressure
            - self.suction_resistance * flow ** 2
        )

        discharge_pressure = (
            self.downstream_boundary_pressure
            + (
                self.discharge_resistance
                + self.downstream_restriction
                + self.valve_resistance
            ) * flow ** 2
        )

        return (
            flow,
            suction_pressure,
            discharge_pressure,
        )

    def get_state(self) -> StateRow:
        return {
            "running": self.running,
            "pressure": self.discharge_pressure,
            "suction_pressure": self.suction_pressure,
            "discharge_pressure": self.discharge_pressure,
            "spread": self.spread,
            "temperature": self.temperature,
            "flow": self.flow,
            "load": self.load,
            "load_target": self.load_target,
            "suction_pressure_target": self.suction_pressure_target,
            "discharge_pressure_target": self.discharge_pressure_target,
            "flow_target": self.flow_target,
            "max_spread": self.max_spread,
            "max_flow": self.max_flow,
            "max_temperature": self.max_temperature,
            "compressor_pressure_rise": self.compressor_pressure_rise,
            "discharge_valve_position": self.discharge_valve_position,
            "discharge_valve_target": self.discharge_valve_target,
            "valve_pressure_drop": self.valve_pressure_drop,
        }
