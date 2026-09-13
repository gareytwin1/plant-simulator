from app import config


class CentrifugalPump:
    def __init__(self):
        self.simulation_speed = config.SIMULATION_SPEED
        self.running = False

        self.supply_pressure = 50.0
        self.discharge_header_pressure = 50.0

        self.suction_pressure = self.supply_pressure
        self.discharge_pressure = self.discharge_header_pressure

        self.speed = 0.0
        self.speed_target = 0.0
        self.speed_rate = config.PUMP_SPEED_RATE_PER_SECOND

        self.flow = 0.0
        self.max_flow = 1200.0

        self.shutoff_pressure_rise = 75.0
        self.pump_resistance = 0.000015

        self.suction_resistance = 0.000010
        self.discharge_resistance = 0.000050

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.speed_target = 0.0

    def set_speed_target(self, target):
        self.speed_target = max(
            0.0,
            min(target, 1.0),
        )

    @property
    def spread(self):
        return (
            self.discharge_pressure
            - self.suction_pressure
        )

    @property
    def system_resistance(self):
        return (
            self.suction_resistance
            + self.discharge_resistance
        )

    @property
    def boundary_pressure_difference(self):
        return (
            self.discharge_header_pressure
            - self.supply_pressure
        )

    @property
    def pump_pressure_rise(self):
        return max(
            self.shutoff_pressure_rise * self.speed ** 2
            - self.pump_resistance * self.flow ** 2,
            0.0,
        )

    @staticmethod
    def _move_toward(current, target, rate, dt):
        change = rate * dt

        if current < target:
            return min(
                current + change,
                target,
            )

        if current > target:
            return max(
                current - change,
                target,
            )

        return current

    def step(self, dt=None):
        if dt is None:
            dt = config.SIMULATION_STEP_SECONDS

        dt *= self.simulation_speed

        speed_target = (
            self.speed_target
            if self.running
            else 0.0
        )

        self.speed = self._move_toward(
            self.speed,
            speed_target,
            self.speed_rate,
            dt,
        )

        (
            self.flow,
            self.suction_pressure,
            self.discharge_pressure,
        ) = self._calculate_operating_point()

    def _calculate_operating_point(self):
        available_pressure_rise = (
            self.shutoff_pressure_rise * self.speed ** 2
            - self.boundary_pressure_difference
        )

        if available_pressure_rise <= 0.0:
            return (
                0.0,
                self.supply_pressure,
                self.discharge_header_pressure,
            )

        flow = (
            available_pressure_rise
            / (
                self.pump_resistance
                + self.system_resistance
            )
        ) ** 0.5

        flow = min(
            flow,
            self.max_flow,
        )

        suction_pressure = (
            self.supply_pressure
            - self.suction_resistance * flow ** 2
        )

        discharge_pressure = (
            self.discharge_header_pressure
            + self.discharge_resistance * flow ** 2
        )

        return (
            flow,
            suction_pressure,
            discharge_pressure,
        )

    def get_state(self):
        return {
            "running": self.running,
            "speed": self.speed,
            "speed_target": self.speed_target,
            "flow": self.flow,
            "suction_pressure": self.suction_pressure,
            "discharge_pressure": self.discharge_pressure,
            "spread": self.spread,
            "pump_pressure_rise": self.pump_pressure_rise,
            "max_flow": self.max_flow,
        }
