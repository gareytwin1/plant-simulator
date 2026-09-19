import copy
import functools
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from app.statetypes import StateRow


INLET = "inlet"
OUTLET = "outlet"

PORT_DIRECTIONS = (
    INLET,
    OUTLET,
)

PRESERVED_ON_RESET = (
    "ports",
    "_construction_state",
)


class PortNode(Protocol):
    """What a Port needs a node to be.

    C1 sits below C2 and must not import it, but a port does carry the
    node the topology attached it to. The only thing anything on the
    equipment side ever reads off that node is its id, so that is all the
    protocol states — `app.plant.topology.Node` satisfies it structurally.
    """

    @property
    def id(self) -> str: ...


class Port:
    """A named connection point between a device and the plant topology.

    A port carries wiring, not results. It knows the node the topology
    attached it to, and there is deliberately nowhere on a port to put a
    pressure or a flow — those are solver outputs, and a device that could
    read them would be solving its own operating point again.

    The slots are what make that structural rather than a convention: a
    device cannot stash a solver output on a port even by accident.
    """

    __slots__ = (
        "name",
        "direction",
        "node",
    )

    name: str
    direction: str
    node: PortNode | None

    def __init__(
        self,
        name: str,
        direction: str,
        node: PortNode | None = None,
    ) -> None:
        if direction not in PORT_DIRECTIONS:
            raise ValueError(
                f"port direction must be one of {PORT_DIRECTIONS}, got {direction!r}",
            )

        self.name = name
        self.direction = direction
        self.node = node

    @property
    def connected(self) -> bool:
        return self.node is not None

    def connect(self, node: PortNode) -> None:
        self.node = node

    def disconnect(self) -> None:
        self.node = None

    def __repr__(self) -> str:
        return f"Port({self.name!r}, {self.direction!r}, node={self.node!r})"


class Equipment:
    """Interface contract C1 — every device implements this and nothing more.

    The class exists to enforce one split, and the split is the whole point:

    `integrate(dt)` advances SLOW state only — a load ramp, a valve stroke,
    a vessel level, metal temperature. It is the only method allowed to
    mutate the device, it moves it forward by dt seconds of simulated time,
    and it never touches flow or pressure.

    `characteristic(flow)` is a pure query — the pressure change across the
    device at that flow, right now, with the slow state wherever integrate
    last left it. Positive is a rise (a machine), negative is a drop (a
    valve, a pipe). It mutates nothing, so the solver may call it as many
    times per timestep as its iteration needs.

    A device therefore never reads or writes a node pressure. It publishes a
    curve; the solver finds where the plant lands on it. A boundary pressure
    owned by a device is a solver output in disguise and belongs to the
    topology instead.

    Slow state is captured at the end of construction, so `reset()` restores
    every device exactly without each one reimplementing it. Port wiring is
    not process state and survives a reset — the topology owns it.
    """

    tag: str
    ports: dict[str, Port]

    _registry: dict[str, type["Equipment"]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        Equipment._registry[cls.__name__] = cls

        if "__init__" in cls.__dict__:
            cls.__init__ = _captures_construction_state(  # type: ignore[method-assign]
                cls.__init__,
            )

    def __init__(self, tag: str, ports: Mapping[str, str] | None = None) -> None:
        self.tag = tag
        self.ports = {}

        for name, direction in (ports or {}).items():
            self.add_port(name, direction)

        self._construction_state = _snapshot(self)

    @classmethod
    def registered(cls) -> dict[str, type["Equipment"]]:
        return dict(Equipment._registry)

    def add_port(self, name: str, direction: str) -> Port:
        port = Port(name, direction)
        self.ports[name] = port

        return port

    def port(self, name: str) -> Port:
        if name not in self.ports:
            raise KeyError(
                f"{self.tag} has no port {name!r}, only {sorted(self.ports)}",
            )

        return self.ports[name]

    def integrate(self, dt: float) -> None:
        """Advance SLOW state only: load ramp, valve stroke,
        level, metal temperature. Never touches flow/pressure.

        `integrate(0)` is a no-op — nothing may move without simulated time
        passing, or a solver iteration would change the plant.
        """
        raise NotImplementedError

    def characteristic(self, flow: float) -> float:
        """Pressure change across the device at this flow.
        Positive = rise (machine), negative = drop (valve, pipe).

        Pure: it reads slow state and returns a number, and mutates nothing.
        """
        raise NotImplementedError

    def get_state(self) -> StateRow:
        """Flat, JSON-safe primitives — the device's row in the snapshot."""
        raise NotImplementedError

    def reset(self) -> None:
        """Back to construction state, exactly. Port wiring is left alone."""
        restored = copy.deepcopy(self._construction_state)

        for name in list(self.__dict__):
            if name in PRESERVED_ON_RESET or name in restored:
                continue

            del self.__dict__[name]

        self.__dict__.update(restored)

    @staticmethod
    def _move_toward(
        current: float,
        target: float,
        rate: float,
        dt: float,
    ) -> float:
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

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {getattr(self, 'tag', '?')}>"


def _snapshot(device: "Equipment") -> dict[str, Any]:
    # A device's __dict__ holds whatever slow state that device declared,
    # so this is genuinely heterogeneous rather than under-specified.
    return copy.deepcopy(
        {
            name: value
            for name, value in device.__dict__.items()
            if name not in PRESERVED_ON_RESET
        },
    )


def _captures_construction_state(
    init: Callable[..., None],
) -> Callable[..., None]:
    @functools.wraps(init)
    def wrapper(self: "Equipment", *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)

        self._construction_state = _snapshot(self)

    return wrapper
