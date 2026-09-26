"""
Malfunctions: contract C8's fault half, and the rule that keeps it honest (T13-1).

A malfunction is a change an engineer could make to a device, made without
the operator's knowledge:

    Malfunction(target_tag, parameter, value, profile, start_condition)

It moves one design parameter of one device from wherever it stood to
`value`. That is the whole of its reach. A fouled exchanger raises `fouling`;
a worn impeller lowers `shutoff_pressure_rise`. The plant then responds
through the solver exactly as it would to the same change made on purpose,
so every symptom an operator sees is physics, not script.

**A malfunction never writes a solver output.** A stuck valve sets a position
limit; it does not set flow. `WRITABLE` is the structural form of that rule:
an explicit allowlist per device class, and a parameter absent from it cannot
be written, whatever it is called. Everything the solver owns - a node
pressure, a branch flow, a stream temperature - is absent by construction,
and so is every piece of slow state a device integrates: a valve's
`position`, a pump's `speed`, a vessel's `level` and gas `pressure`. Slow
state belongs to `integrate`; overwriting it would teleport the plant rather
than disturb it. T13-5 guards the same boundary from the outside.

Every allowlisted parameter is a validating property on its device, so the
device's own range check refuses an impossible value, and `add` runs that
check against a shallow copy before a malfunction is accepted. A value that
could only fail at onset, mid-scenario, is refused up front instead.

Lookup is by the device's exact class, never `isinstance`: a subclass does
not inherit its parent's writable surface silently, it has to be listed.

An instrument is a target too (T13-2). Its `bias` is the one thing a
malfunction may move, and moving it changes what the plant indicates while
the plant itself carries on exactly as before - which is what makes an
instrument fault a hidden cause. The registry resolves a tag against the
plant's devices and its instruments alike, and a tag may name only one.

`MalfunctionRegistry` holds a plant's malfunctions and owns their lifecycle.
Each `update(snapshot)` - run between engine steps, never inside one - checks
pending malfunctions' start conditions against the published snapshot, and
writes each active one's value as its profile shapes it. The original value is
captured at onset, and `revert` puts it back exactly. Nothing here reads the
plant except through the snapshot, and nothing reads a clock: elapsed time is
the snapshot's `sim_time` minus the onset's.

`Step` and `AtTime` are the minimal profile and start condition. Ramps and
condition-triggered onset are T13-3's, and fit the same two protocols.
"""

import copy
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.engine.instruments import Instrument
from app.engine.snapshot import Snapshot
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.exchanger import HeatExchanger
from app.equipment.pump import CentrifugalPump
from app.equipment.registry import EquipmentRegistry
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel


Target = Equipment | Instrument

WRITABLE: dict[type[Target], frozenset[str]] = {
    ControlValve: frozenset({
        "capacity",
        "min_position",
    }),
    HeatExchanger: frozenset({
        "fouling",
        "cold_temperature",
        "exchanger_resistance",
    }),
    CentrifugalPump: frozenset({
        "shutoff_pressure_rise",
        "pump_resistance",
    }),
    GasCompressor: frozenset({
        "shutoff_pressure_rise",
        "compressor_resistance",
        "polytropic_efficiency",
    }),
    # Everything a vessel carries is geometry or inventory. Listed empty so the
    # absence is a decision rather than an oversight.
    Vessel: frozenset(),
    Instrument: frozenset({
        "bias",
    }),
}


class NotWritable(ValueError):
    """A malfunction named a parameter its target's class does not allowlist."""


class Profile(Protocol):
    """How far toward its value a malfunction has moved, `elapsed` seconds after onset.

    Returns a fraction in [0, 1]: 0 is the original value, 1 is the
    malfunction's value. Must be pure and read nothing but `elapsed`.
    """

    def fraction(self, elapsed: float) -> float: ...


class StartCondition(Protocol):
    """Whether a pending malfunction starts now. Reads the snapshot only."""

    def is_met(self, snapshot: Snapshot) -> bool: ...


@dataclass(frozen=True)
class Step:
    """The whole change at once, on the update that sees the onset."""

    def fraction(self, elapsed: float) -> float:
        return 1.0


@dataclass(frozen=True)
class AtTime:
    """Starts on the first update whose simulated time has reached `sim_time`."""

    sim_time: float = 0.0

    def is_met(self, snapshot: Snapshot) -> bool:
        return snapshot.sim_time >= self.sim_time


@dataclass(frozen=True)
class Malfunction:
    target_tag: str
    parameter: str
    value: float
    profile: Profile = field(default_factory=Step)
    start_condition: StartCondition = field(default_factory=AtTime)

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError(
                f"malfunction {self.target_tag}.{self.parameter} value must be "
                f"a number, got {type(self.value).__name__} {self.value!r}",
            )

        if not math.isfinite(self.value):
            raise ValueError(
                f"malfunction {self.target_tag}.{self.parameter} value must be "
                f"finite, got {self.value!r}",
            )

    @property
    def key(self) -> tuple[str, str]:
        return (self.target_tag, self.parameter)


def writable(device: Target) -> frozenset[str]:
    device_type = type(device)

    if device_type not in WRITABLE:
        raise NotWritable(
            f"{device.tag} is a {device_type.__name__}, which has no "
            f"malfunction allowlist",
        )

    return WRITABLE[device_type]


def check_writable(device: Target, parameter: str) -> None:
    allowed = writable(device)

    if parameter not in allowed:
        raise NotWritable(
            f"{device.tag}.{parameter} is not writable by a malfunction; "
            f"{type(device).__name__} allows only {sorted(allowed)}",
        )


@dataclass
class _Onset:
    time: float
    original: float


class MalfunctionRegistry:
    def __init__(
        self,
        equipment: EquipmentRegistry,
        instruments: Iterable[Instrument] = (),
    ) -> None:
        self._equipment = equipment
        self._instruments: dict[str, Instrument] = {}

        for instrument in instruments:
            if instrument.tag in self._instruments or _registered(equipment, instrument.tag):
                raise ValueError(f"tag {instrument.tag!r} is already in use")

            self._instruments[instrument.tag] = instrument

        self._malfunctions: dict[tuple[str, str], Malfunction] = {}
        self._onsets: dict[tuple[str, str], _Onset] = {}

    @property
    def pending(self) -> tuple[Malfunction, ...]:
        return tuple(
            malfunction
            for key, malfunction in self._malfunctions.items()
            if key not in self._onsets
        )

    @property
    def active(self) -> tuple[Malfunction, ...]:
        return tuple(
            malfunction
            for key, malfunction in self._malfunctions.items()
            if key in self._onsets
        )

    def add(self, malfunction: Malfunction) -> None:
        device = self._resolve(malfunction.target_tag)
        check_writable(device, malfunction.parameter)

        if malfunction.key in self._malfunctions:
            raise ValueError(
                f"{malfunction.target_tag}.{malfunction.parameter} already has "
                f"a malfunction: {self._malfunctions[malfunction.key]!r}",
            )

        setattr(copy.copy(device), malfunction.parameter, malfunction.value)

        self._malfunctions[malfunction.key] = malfunction

    def update(self, snapshot: Snapshot) -> None:
        for key, malfunction in self._malfunctions.items():
            device = self._resolve(malfunction.target_tag)

            if key not in self._onsets:
                if not malfunction.start_condition.is_met(snapshot):
                    continue

                self._onsets[key] = _Onset(
                    time=snapshot.sim_time,
                    original=getattr(device, malfunction.parameter),
                )

            onset = self._onsets[key]
            fraction = malfunction.profile.fraction(snapshot.sim_time - onset.time)

            if not 0.0 <= fraction <= 1.0:
                raise ValueError(
                    f"{malfunction.profile!r} returned fraction {fraction!r} for "
                    f"{malfunction.target_tag}.{malfunction.parameter}; it must "
                    f"lie in [0, 1]",
                )

            _write(
                device,
                malfunction.parameter,
                _between(onset.original, malfunction.value, fraction),
            )

    def revert(self, malfunction: Malfunction) -> None:
        if self._malfunctions.get(malfunction.key) != malfunction:
            raise KeyError(f"{malfunction!r} is not registered")

        del self._malfunctions[malfunction.key]
        onset = self._onsets.pop(malfunction.key, None)

        if onset is not None:
            device = self._resolve(malfunction.target_tag)
            _write(device, malfunction.parameter, onset.original)

    def revert_all(self) -> None:
        for malfunction in list(self._malfunctions.values()):
            self.revert(malfunction)

    def _resolve(self, tag: str) -> Target:
        if tag in self._instruments:
            return self._instruments[tag]

        return self._equipment.resolve(tag)


def _registered(equipment: EquipmentRegistry, tag: str) -> bool:
    try:
        equipment.resolve(tag)
    except KeyError:
        return False

    return True


def _between(original: float, value: float, fraction: float) -> float:
    # The endpoints are written exactly rather than interpolated to, so a
    # finished step lands on `value` and a zero fraction leaves the device alone
    # bit for bit.
    if fraction == 1.0:
        return value

    if fraction == 0.0:
        return original

    return original + (value - original) * fraction


def _write(device: Target, parameter: str, value: float) -> None:
    check_writable(device, parameter)
    setattr(device, parameter, value)
