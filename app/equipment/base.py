"""
Equipment: interface contract C1, and the sign convention its curves obey.

Two things live here that the rest of the plant is measured against. C1 itself
— the integrate/characteristic split every device implements — and the sign
convention `characteristic` reports against, including what a device is
required to do when flow runs backwards through it.

`signed_square` is the primitive that makes the second of those true. Read the
`Equipment.characteristic` docstring before writing a device: a curve with the
wrong sign is not a device bug, it is a solver that converges on a plant which
does not exist.
"""

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

LIQUID = "liquid"
VAPOR = "vapor"

PORT_PHASES = (
    LIQUID,
    VAPOR,
)

# Reserved rather than simply unknown, so the refusal can say why. A stream
# carrying both phases has to split, and splitting it is a flash calculation,
# which V1 rules out (ADR 0002, D6). Naming it here stops a later session
# inventing a different spelling for the same idea.
MIXED = "mixed"

RESERVED_PHASES = (MIXED,)

PROCESS = "process"
VENT = "vent"
DRAIN = "drain"
RELIEF = "relief"

PORT_PURPOSES = (
    PROCESS,
    VENT,
    DRAIN,
    RELIEF,
)

# Prefixed where the bare word is already a process quantity elsewhere in the
# plant: these name a control *function*, not a flow or a pressure.
CONTROL_FLOW = "flow"
CONTROL_PRESSURE = "pressure"
CONTROL_LEVEL = "level"
CONTROL_TEMPERATURE = "temperature"

PORT_CONTROLS = (
    CONTROL_FLOW,
    CONTROL_PRESSURE,
    CONTROL_LEVEL,
    CONTROL_TEMPERATURE,
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

    A port carries **connection metadata**, never **process state**. The
    distinction is the whole of what a port is allowed to hold:

    *Connection metadata* says what this connection is. The node the topology
    attached it to, and the four descriptors T3-7 added — `direction`,
    `phase`, `purpose` and `control`. All of it is declared, none of it is
    computed, and none of it moves while the plant runs.

    *Process state* is a pressure, a flow, a temperature, a level. Those are
    solver outputs, and there is deliberately nowhere on a port to put one: a
    device that could read them would be solving its own operating point
    again. The slots are what make that structural rather than a convention.

    `name` is an identifier for humans, configuration and diagnostics, and
    **never drives engineering behaviour** — no code may branch on a port
    being called `suction`, `drain` or anything else, and
    `tests/test_port_name_guard.py` fails the build if any does. Behaviour
    comes from the descriptors.

    Only `phase` and `direction` may participate in a conservation balance.
    `purpose` and `control` are descriptive: a vapor withdrawal is a vapor
    withdrawal whether it is labelled process, vent or relief, and whether or
    not a controller is associated with it. See ADR 0002, Amendment 1 A.3.

    The three descriptors are optional, and a legacy untyped wiring entry
    leaves them `None`. `declare()` sets all three together, because a typed
    declaration is one statement about a connection rather than three.
    """

    __slots__ = (
        "name",
        "direction",
        "phase",
        "purpose",
        "control",
        "node",
    )

    name: str
    direction: str
    phase: str | None
    purpose: str | None
    control: str | None
    node: PortNode | None

    def __init__(
        self,
        name: str,
        direction: str,
        node: PortNode | None = None,
        phase: str | None = None,
        purpose: str | None = None,
        control: str | None = None,
    ) -> None:
        if direction not in PORT_DIRECTIONS:
            raise ValueError(
                f"port direction must be one of {PORT_DIRECTIONS}, got {direction!r}",
            )

        self.name = name
        self.direction = direction
        self.node = node

        self.declare(phase, purpose, control)

    def declare(
        self,
        phase: str | None = None,
        purpose: str | None = None,
        control: str | None = None,
    ) -> None:
        """Set all three descriptors, validating each. Omitted means undeclared."""
        checked_phase = _checked(phase, PORT_PHASES, "phase", RESERVED_PHASES)
        checked_purpose = _checked(purpose, PORT_PURPOSES, "purpose")
        checked_control = _checked(control, PORT_CONTROLS, "control")

        self.phase = checked_phase
        self.purpose = checked_purpose
        self.control = checked_control

    @property
    def connected(self) -> bool:
        return self.node is not None

    def connect(self, node: PortNode) -> None:
        self.node = node

    def disconnect(self) -> None:
        self.node = None

    def __repr__(self) -> str:
        declared = "".join(
            f", {label}={value!r}"
            for label, value in (
                ("phase", self.phase),
                ("purpose", self.purpose),
                ("control", self.control),
            )
            if value is not None
        )

        return f"Port({self.name!r}, {self.direction!r}{declared}, node={self.node!r})"


class Equipment:
    """Interface contract C1 — every device implements this and nothing more.

    The class exists to enforce one split, and the split is the whole point:

    `integrate(dt)` advances SLOW state only — a load ramp, a valve stroke,
    a vessel level, metal temperature. It is the only method allowed to
    mutate the device, it moves it forward by dt seconds of simulated time,
    and it never touches a solved flow or a node pressure.

    `characteristic(flow)` is a pure query — the pressure change across the
    device at that flow, right now, with the slow state wherever integrate
    last left it. Positive is a rise (a machine), negative is a drop (a
    valve, a pipe). It mutates nothing, so the solver may call it as many
    times per timestep as its iteration needs.

    A device therefore never reads or writes a node pressure. It publishes a
    curve; the solver finds where the plant lands on it. A node pressure or
    branch flow is a solver output, and a device holding a copy of one is a
    solver output in disguise. Inventory — a vessel's level and gas pressure
    — is device slow state; it reaches the plant only as a boundary the
    coupling writes.

    A device reports the truth and nothing else. Its row is what the physics
    says; what an operator or a controller reads is that row as the plant's
    instruments indicate it, derived at the snapshot (T13-2,
    app/engine/instruments.py). A device never knows it is being measured, so
    an instrument fault can never change what it does.

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

    def add_port(
        self,
        name: str,
        direction: str,
        phase: str | None = None,
        purpose: str | None = None,
        control: str | None = None,
    ) -> Port:
        port = Port(name, direction, phase=phase, purpose=purpose, control=control)
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
        """Pressure change across the device at this flow: outlet minus inlet.

        Positive is a rise (a machine), negative is a drop (a valve, a pipe).

        Flow is signed, and positive flow runs from the inlet port to the
        outlet port. The direction the change is measured in is fixed by the
        ports and never by the flow: reversing the flow changes the number
        this returns, not which end it is measured from. A solver that had to
        know which way a branch happened to be flowing before it could read
        the sign would get it wrong on the iteration where the flow crossed
        zero, which is the iteration it spends most of its time near.

        Every real flow is in range — positive, zero and negative. A solver
        reaches flows the plant never will, and reaches them while it is
        still wrong, so a device may not raise, clamp, or return a
        non-finite number outside the range it expects to operate in.

        The curve is non-increasing in flow everywhere: more flow never buys
        more pressure. That is what leaves the branch equation exactly one
        root and keeps the Jacobian from going singular. A clamp that
        flattens the curve past runout is not a safety measure, it is a flat
        region with no gradient for the solver to descend.

        Written against `signed_square`, the two shapes V1 needs satisfy all
        of that without each device restating it:

            machine      shutoff_rise - resistance * signed_square(flow)
            resistance   -resistance * signed_square(flow)

        A machine holds its static head at zero flow and rises above shutoff
        when flow is driven backwards through it — a centrifugal machine
        resists a reversal rather than helping it along. A resistance is zero
        at zero flow and drops in whichever direction the flow runs.

        Pure: it reads slow state and returns a number, and mutates nothing.
        """
        raise NotImplementedError

    def get_state(self) -> StateRow:
        """Flat, JSON-safe primitives — the device's true row in the snapshot.

        True, never indicated: the snapshot publishes it as `truth` and runs it
        through the plant's instruments for the reading every consumer sees.
        """
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


def _checked(
    value: str | None,
    allowed: tuple[str, ...],
    label: str,
    reserved: tuple[str, ...] = (),
) -> str | None:
    if value is None:
        return None

    if value in reserved:
        raise ValueError(
            f"port {label} {value!r} is reserved for a future version, "
            f"use one of {allowed}",
        )

    if value not in allowed:
        raise ValueError(
            f"port {label} must be one of {allowed}, got {value!r}",
        )

    return value


def signed_square(flow: float) -> float:
    """`flow` squared, carried through zero with flow's own sign: |q|*q.

    Every quadratic term in the plant is written against this rather than
    `flow ** 2`, and that single substitution is the whole of reverse flow.

    `flow ** 2` is even, so a device built on it returns the same pressure
    change at -100 as at +100: a resistance that pushes back just as hard on
    flow which is already running backwards, and a curve with a matching root
    on either side of zero for the solver to fall into. |q|*q is odd,
    continuous and increasing everywhere, so a resistance term built on it
    always opposes the flow that caused it, and a machine curve built on it
    keeps falling as flow rises — including across zero.
    """
    return abs(flow) * flow


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
