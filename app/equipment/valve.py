"""
Control valve — a stroked, quadratic resistance (T7-1).

The valve is the first device that only resists. A machine publishes a rise and
finds its own flow against it; a valve publishes a drop that grows with the
square of the flow through it, and how *much* it grows is what an operator moves:

    characteristic(q) = -K(position) * signed_square(q),   K = 1 / Cv(position)^2

**Cv here is a coefficient, not a sizing standard.** Liquid Cv is gpm/sqrt(psi)
and compressible-gas sizing is a different equation entirely. This model takes
`capacity` as the simulator's simplified quadratic capacity coefficient —
flow per root psi at full travel — and does no gas-expansion or choked-flow
work. Its unit is whatever the flow unit of the domain it sits in is, which is
why the device carries no unit of its own: `app/engine/coupling.py` resolves
GPM or SCFM from the domain the valve is installed in. For liquid of specific
gravity 1 this is exactly the textbook `K = 1 / Cv^2`.

Two inherent characteristics map travel to capacity:

    linear             Cv(x) = capacity * x
    equal_percentage   Cv(x) = capacity * rangeability ** (x - 1)

Travel is bounded to [`min_position`, 1.0]. K rises as the valve closes, so a
zero position would divide by zero; the floor is what keeps `characteristic`
finite at every position and flow. It also means "closed" is the minimum
travel rather than tight shutoff — a fail-closed valve leaves the line
throttled to `min_position`, not sealed, and a resistance cannot stop reverse
flow against an adverse pressure gradient (only a check valve can).

`integrate(dt)` strokes the valve toward its target at no more than
`stroke_rate` (fraction of travel per second). While `signal_ok` is False the
target is the fail position — `min_position` for fail-closed, 1.0 for
fail-open — whatever was commanded.
"""

from app import config
from app.equipment.base import Equipment, INLET, OUTLET, signed_square
from app.statetypes import StateRow


LINEAR = "linear"
EQUAL_PERCENTAGE = "equal_percentage"

CHARACTERISTICS = (
    LINEAR,
    EQUAL_PERCENTAGE,
)

FAIL_CLOSED = "closed"
FAIL_OPEN = "open"

FAIL_ACTIONS = (
    FAIL_CLOSED,
    FAIL_OPEN,
)


class ControlValve(Equipment):
    def __init__(self, tag: str = "FV-101") -> None:
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.position = 1.0
        self.position_target = 1.0
        self.stroke_rate = config.VALVE_STROKE_RATE_PER_SECOND

        self.signal_ok = True

        self._capacity = 100.0
        self._rangeability = 50.0
        self._min_position = 0.10
        self._flow_characteristic = LINEAR
        self._fail_action = FAIL_CLOSED

    @property
    def capacity(self) -> float:
        return self._capacity

    @capacity.setter
    def capacity(self, value: float) -> None:
        if not value > 0.0:
            raise ValueError(f"capacity must be positive, got {value}")

        self._capacity = float(value)

    @property
    def rangeability(self) -> float:
        return self._rangeability

    @rangeability.setter
    def rangeability(self, value: float) -> None:
        if not value > 1.0:
            raise ValueError(f"rangeability must exceed 1, got {value}")

        self._rangeability = float(value)

    @property
    def min_position(self) -> float:
        return self._min_position

    @min_position.setter
    def min_position(self, value: float) -> None:
        if not 0.0 < value <= 1.0:
            raise ValueError(
                f"min_position must be in (0, 1] — zero travel is zero "
                f"capacity, got {value}",
            )

        self._min_position = float(value)

    @property
    def flow_characteristic(self) -> str:
        return self._flow_characteristic

    @flow_characteristic.setter
    def flow_characteristic(self, value: str) -> None:
        if value not in CHARACTERISTICS:
            raise ValueError(
                f"flow_characteristic must be one of {CHARACTERISTICS}, "
                f"got {value!r}",
            )

        self._flow_characteristic = value

    @property
    def fail_action(self) -> str:
        return self._fail_action

    @fail_action.setter
    def fail_action(self, value: str) -> None:
        if value not in FAIL_ACTIONS:
            raise ValueError(
                f"fail_action must be one of {FAIL_ACTIONS}, got {value!r}",
            )

        self._fail_action = value

    @property
    def fail_position(self) -> float:
        return self.min_position if self.fail_action == FAIL_CLOSED else 1.0

    @property
    def effective_capacity(self) -> float:
        travel = self._bounded(self.position)

        if self.flow_characteristic == EQUAL_PERCENTAGE:
            return self.capacity * float(self.rangeability ** (travel - 1.0))

        return self.capacity * travel

    def set_position_target(self, target: float) -> None:
        self.position_target = self._bounded(target)

    def lose_signal(self) -> None:
        self.signal_ok = False

    def restore_signal(self) -> None:
        self.signal_ok = True

    def integrate(self, dt: float) -> None:
        target = (
            self._bounded(self.position_target)
            if self.signal_ok
            else self.fail_position
        )

        self.position = self._move_toward(
            self.position,
            target,
            self.stroke_rate,
            dt,
        )

    def characteristic(self, flow: float) -> float:
        return -signed_square(flow) / self.effective_capacity ** 2

    def get_state(self) -> StateRow:
        return {
            "position": self.position,
            "position_target": self.position_target,
            "signal_ok": self.signal_ok,
            "effective_capacity": self.effective_capacity,
        }

    def _bounded(self, position: float) -> float:
        return max(
            self.min_position,
            min(position, 1.0),
        )
