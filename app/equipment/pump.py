from app import config
from app.equipment.base import Equipment, INLET, OUTLET, signed_square
from app.equipment.ranges import checked
from app.statetypes import StateRow


class CentrifugalPump(Equipment):
    def __init__(self, tag: str = "P-101") -> None:
        super().__init__(
            tag,
            ports={
                "suction": INLET,
                "discharge": OUTLET,
            },
        )

        self.running = False

        self.speed = 0.0
        self.speed_target = 0.0
        self.speed_rate = config.PUMP_SPEED_RATE_PER_SECOND

        self.max_flow = 1200.0

        self.shutoff_pressure_rise = 75.0
        self.pump_resistance = 0.000015

    @property
    def shutoff_pressure_rise(self) -> float:
        return self._shutoff_pressure_rise

    @shutoff_pressure_rise.setter
    def shutoff_pressure_rise(self, value: float) -> None:
        self._shutoff_pressure_rise = checked(
            self.tag, "shutoff_pressure_rise", value, 0.0,
        )

    @property
    def pump_resistance(self) -> float:
        return self._pump_resistance

    @pump_resistance.setter
    def pump_resistance(self, value: float) -> None:
        self._pump_resistance = checked(
            self.tag, "pump_resistance", value, 0.0, above=True,
        )

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.speed_target = 0.0

    def set_speed_target(self, target: float) -> None:
        self.speed_target = max(
            0.0,
            min(target, 1.0),
        )

    def integrate(self, dt: float) -> None:
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

    def characteristic(self, flow: float) -> float:
        return (
            self.shutoff_pressure_rise * self.speed ** 2
            - self.pump_resistance * signed_square(flow)
        )

    def get_state(self) -> StateRow:
        return {
            "running": self.running,
            "speed": self.speed,
            "speed_target": self.speed_target,
            "max_flow": self.max_flow,
        }
