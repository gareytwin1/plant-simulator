"""
Stream enthalpy: where a temperature comes from, and what happens when two
streams meet at a node.

C2 gives every stream a temperature, but nothing yet says what that number
means or how it survives a junction. This module is that rule. It is pure
arithmetic over values handed to it — no device, no node, no solver state —
which is what lets the engine call it wherever energy has to move.

**Heat capacity is per unit of native flow.** A liquid capacity is
BTU/(gal·°F) and a vapour capacity is BTU/(scf·°F), with density folded into
the constant rather than carried separately. That is deliberate:
docs/UNITS_CONVENTION.md forbids unit conversion inside an equipment model, so
the conversion between a flow in its native unit and an energy rate has to
live somewhere neutral, and this is it. An energy rate is BTU/hr — the one
energy unit the convention names — so a caller with a duty in BTU/hr divides
it by `heat_capacity_rate()` and gets °F with nothing to convert.

**The datum is 60 °F**, the standard condition SCFM is already defined at, so
a stream at standard conditions carries zero enthalpy. Only differences
matter, and any datum would do; this one makes the gas domain's two
conventions agree.

**Mixing weights temperature by q·Cp and composition by q**, and the pairing
is not an inconsistency — it is the whole reason energy closes. Weighting
composition by flow makes the mixture's own capacity satisfy
`Q·Cp_out = Σ q·Cp`, which is exactly the factor the temperature average
divided by, so the enthalpy leaving equals the enthalpy arriving identically
rather than to within a tolerance.

**Phases never mix.** Two streams of different phase meeting at a node is a
flash calculation, which V1 rules out (ADR 0002, D6), so the mix refuses
instead of averaging its way past it.

This module imports nothing from `app.plant` on purpose. The phase vocabulary
comes from C1, where T3-7 froze it, and nothing else does — so an equipment
model may import this module without inverting a layer or closing a cycle,
which is the route T6-2, T6-3 and T6-4 need.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from app.equipment.base import LIQUID, PORT_PHASES, VAPOR


# The enthalpy datum. A second spelling of topology.STANDARD_TEMPERATURE on
# purpose, for the reason app/config.py duplicates ATMOSPHERIC_PRESSURE: this
# module does not import from app.plant, so the two cannot be one constant.
# They are the same 60 °F, and moving the datum means moving both.
REFERENCE_TEMPERATURE = 60.0  # °F

MINUTES_PER_HOUR = 60.0

# An ideal gas at 60 °F and 14.696 psia: R·T/P with R = 10.7316
# psia·ft³/(lbmol·°R). It converts a looked-up molar heat capacity into the
# per-standard-cubic-foot form the vapour domain's SCFM needs.
STANDARD_MOLAR_VOLUME = 379.5  # scf/lbmol

# Guards the division in a mix, and nothing else. It is float slack, not a
# process deadband: a stopped machine's residual flow is orders of magnitude
# above this and is weighted normally, which is the intent — see the idle-flow
# note in .workspace/memory/project_state.md.
WEIGHT_TOLERANCE = 1e-12

# Matches topology.COMPOSITION_TOLERANCE. A composition that sums to 0.9 is a
# bug that would otherwise surface as a quietly low heat capacity, which is
# the same reason C2 refuses one.
COMPOSITION_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Component:
    """One component's heat capacity, per unit of native flow, per phase.

    A capacity is `None` where this simulator does not model the component in
    that phase — methane is a vapour here, not an LNG, and asking for its
    liquid capacity is a modelling error worth a message rather than a number
    extrapolated 300 °F out of range.

    Capacities are constant with temperature. Over the 50–300 °F band
    docs/UNITS_CONVENTION.md covers, that is the level of rigour the training
    goal asks for; a temperature-dependent correlation is the compositional
    property package AGENTS.md rules out.
    """

    name: str
    vapor_heat_capacity: float | None = None  # BTU/(scf·°F)
    liquid_heat_capacity: float | None = None  # BTU/(gal·°F)

    def heat_capacity(self, phase: str) -> float:
        # Checked here and not only at the module's entry points: without it
        # anything that is not VAPOR reads as liquid, so MIXED — the phase
        # ADR 0002 D6 refuses precisely so it never reaches physics — would
        # quietly return a number.
        _check_phase(phase)

        capacity = (
            self.vapor_heat_capacity
            if phase == VAPOR
            else self.liquid_heat_capacity
        )

        if capacity is None:
            raise ValueError(
                f"component {self.name!r} has no {phase} heat capacity — it "
                f"is not modelled as a {phase} anywhere in this simulator's "
                f"50-300 °F range",
            )

        return capacity


# Vapour capacities are ideal-gas molar heat capacities near 60 °F, in
# BTU/(lbmol·°F), divided into standard cubic feet. Liquid capacities are a
# mass heat capacity in BTU/(lb·°F) times a density in lb/gal at the same
# condition. The arithmetic is left in place so the looked-up number stays
# visible and a later correction lands on the source value, not on a derived
# decimal nobody can trace.
COMPONENTS = {
    "methane": Component(
        "methane",
        vapor_heat_capacity=8.55 / STANDARD_MOLAR_VOLUME,
    ),
    "ethane": Component(
        "ethane",
        vapor_heat_capacity=12.6 / STANDARD_MOLAR_VOLUME,
    ),
    "ethylene": Component(
        "ethylene",
        vapor_heat_capacity=10.3 / STANDARD_MOLAR_VOLUME,
    ),
    "propane": Component(
        "propane",
        vapor_heat_capacity=17.6 / STANDARD_MOLAR_VOLUME,
        liquid_heat_capacity=0.58 * 4.22,
    ),
    "propylene": Component(
        "propylene",
        vapor_heat_capacity=15.3 / STANDARD_MOLAR_VOLUME,
        liquid_heat_capacity=0.60 * 4.33,
    ),
    "water": Component(
        "water",
        vapor_heat_capacity=8.0 / STANDARD_MOLAR_VOLUME,
        liquid_heat_capacity=1.00 * 8.34,
    ),
}

# C2 says an empty composition is a single unspecified fluid, which is what
# every plant config declares today — none of them name a component at all.
# The unspecified fluid needs a capacity anyway, and these are the stand-ins:
# a light-ends train's overhead is mostly methane and its bottoms are a
# propane-weight condensate. Naming components rather than repeating their
# numbers keeps one spelling of each.
DEFAULT_COMPONENT = {
    VAPOR: "methane",
    LIQUID: "propane",
}


@dataclass(frozen=True)
class StreamState:
    """The thermal view of a stream: how much, how hot, of what.

    Deliberately not C2's `Stream`. That one is solver-owned mutable state
    tied to a branch; this is an immutable value the energy arithmetic can be
    handed, including for a stream that does not exist yet — the one a mix is
    about to produce. Keeping them separate is also what keeps this module
    free of `app.plant` imports.

    Flow is signed. Positive is the direction its branch is oriented, so a
    reversed branch carries a negative enthalpy flow, and a plant-wide energy
    balance is a signed sum with no special case. `mix_streams` is the one
    place that refuses a negative flow, because there the sign means the
    caller has not worked out which streams are arriving.

    It validates and copies its composition rather than leaning on C2 to have
    done it. Nothing requires one of these to have come from a `Stream` — a
    mix builds them from nothing — so trusting C2's check would leave the
    common case unguarded, and a composition summing to 0.5 would read as a
    fluid with half the heat capacity instead of as the error it is.

    `composition` is a `MappingProxyType` over that copy, so item assignment
    on it raises `TypeError` instead of quietly mutating a value the rest of
    the plant is still holding. `__copy__` and `__deepcopy__` return `self`
    for the same reason a frozen dataclass needs no copy in the first place —
    there is nothing a copy could protect that the proxy does not already.
    Pickle is not supported (`MappingProxyType` refuses it); hashing already
    was not, since `composition` is a mapping.
    """

    flow: float  # GPM if liquid, SCFM if vapor
    temperature: float  # °F
    phase: str  # LIQUID or VAPOR
    composition: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_phase(self.phase)
        _check_composition(self.composition)

        # Frozen stops the field being rebound; it does nothing about a
        # mapping the caller still holds, or one this object hands out. The
        # proxy closes both: it wraps a private copy, and it is itself
        # read-only.
        object.__setattr__(
            self, "composition", MappingProxyType(dict(self.composition)),
        )

    def __copy__(self) -> "StreamState":
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> "StreamState":
        return self


@runtime_checkable
class ThermalDevice(Protocol):
    """A device that changes the temperature of what flows through it.

    The energy counterpart of `Equipment.characteristic`, and bound by the
    same rules: a pure query that reads slow state and mutates nothing, so
    the engine may call it as often as transport needs. A device without it
    changes the temperature of what passes through it by too little to
    matter at this level of rigour - a valve, a pipe, a pump - and
    temperature passes through it unchanged. The method's name is the whole
    marker: the engine classifies a device as thermal by it, once, when the
    plant is built.

    `arriving` is the stream entering the device. Its flow is signed in the
    device's own orientation, positive inlet to outlet, exactly as
    `characteristic(flow)` reads it, so a reversed flow arrives at the outlet
    port and the device knows it. Its temperature is the one at whichever end
    the flow enters, and at exactly zero flow that is the inlet end. The
    return value is the temperature at the end it leaves.

    The two pressures are the solved pressures at the nodes the inlet and
    outlet ports attach to, handed in as arguments. The device never reads a
    node; this is the same bargain `GasCompressor.temperature_at` makes.

    Every finite flow is in range, including zero, where a fixed duty has no
    stream to heat - the device owns that case (see `heat_capacity_rate`),
    and must return a finite temperature.
    """

    def leaving_temperature(
        self,
        arriving: StreamState,
        inlet_pressure: float,
        outlet_pressure: float,
    ) -> float: ...


def heat_capacity(composition: Mapping[str, float], phase: str) -> float:
    """The mixture's heat capacity, in BTU per unit of native flow per °F.

    Mole-fraction weighted. For a vapour that is exact, because a standard
    cubic foot is a fixed number of moles. For a liquid it is an
    approximation — volume-based capacities weighted by mole fraction — and a
    deliberate one: the alternative is carrying molecular weights and
    densities to convert between the two, which is the property package this
    project does not want.
    """
    _check_phase(phase)
    _check_composition(composition)

    fractions = composition or {DEFAULT_COMPONENT[phase]: 1.0}

    return sum(
        fraction * _component(name).heat_capacity(phase)
        for name, fraction in fractions.items()
    )


def heat_capacity_rate(stream: StreamState) -> float:
    """How much energy this stream carries per °F, in BTU/(hr·°F).

    The bridge between a duty and a temperature: a device with a duty in
    BTU/hr divides by this and has its temperature change in °F, with no unit
    conversion anywhere inside the device.

    **It is zero at zero flow, and the caller owns that case.** No guard is
    offered here because there is no honest algebraic answer: a duty into a
    stagnant stream does not produce a temperature rise, it produces a
    transient against the metal it is sitting in. That is a dynamic the
    device has to model — T6-3's metal thermal inertia — not a division this
    module can rescue.
    """
    return (
        stream.flow
        * MINUTES_PER_HOUR
        * heat_capacity(stream.composition, stream.phase)
    )


def enthalpy_flow(stream: StreamState) -> float:
    """The energy this stream carries past a point, in BTU/hr.

    Relative to the 60 °F datum, so it is negative for a stream colder than
    standard. Differences are what mean anything.
    """
    return heat_capacity_rate(stream) * (
        stream.temperature - REFERENCE_TEMPERATURE
    )


def mix_streams(streams: Iterable[StreamState]) -> StreamState:
    """Combine the streams arriving at a node into the one leaving it.

    Flows are the arriving flows, and must be non-negative: a negative one
    means the caller is holding a branch's signed flow rather than a resolved
    arrival, and mixing a departure into the result would be silent nonsense.

    The result is a `StreamState` like its inputs, so it can be fed straight
    back into another mix or into `enthalpy_flow`.

    With no flow there is no information to weight by, and the result is the
    plain average of the inputs. That keeps the temperature field finite
    through a blocked-in node; it is not a claim about what a dead junction
    is actually at.

    **The composition it emits is flow-weighted.** For a vapour that is a
    mole fraction, exactly as C2 means it, because a standard cubic foot is a
    fixed number of moles. For a liquid it is a volume fraction wearing a
    mole fraction's name: 50 GPM of propane meeting 50 GPM of water comes out
    50/50, where the true mole split is nearer 17/83. Correcting it needs
    molecular weights, which V1 does not carry, and no plant config names a
    component at all yet — so this is recorded rather than fixed, and the
    first config to declare a liquid composition is what makes it matter.
    The mixed *temperature* is unaffected either way.
    """
    arrivals = tuple(streams)

    if not arrivals:
        raise ValueError(
            "mix_streams needs at least one stream — a node with nothing "
            "arriving has no mixed state to compute",
        )

    phases = {stream.phase for stream in arrivals}

    if len(phases) > 1:
        raise ValueError(
            f"cannot mix {sorted(phases)} at one node — separating the "
            f"result is a flash calculation, which V1 rules out (ADR 0002, "
            f"D6)",
        )

    reversed_flows = [stream for stream in arrivals if stream.flow < 0.0]

    if reversed_flows:
        raise ValueError(
            f"mix_streams takes arriving flows, and "
            f"{[stream.flow for stream in reversed_flows]} are negative — "
            f"resolve which streams arrive at the node before mixing them",
        )

    named = [bool(stream.composition) for stream in arrivals]

    if any(named) and not all(named):
        raise ValueError(
            "cannot mix a named composition with an unspecified fluid — one "
            "of them has to say what it is",
        )

    phase = arrivals[0].phase
    total_flow = sum(stream.flow for stream in arrivals)

    weights = [
        stream.flow * heat_capacity(stream.composition, phase)
        for stream in arrivals
    ]
    total_weight = sum(weights)

    if total_weight <= WEIGHT_TOLERANCE:
        weights = [1.0] * len(arrivals)
        total_weight = float(len(arrivals))
        composition_weights = weights
        composition_total = total_weight
    else:
        composition_weights = [stream.flow for stream in arrivals]
        composition_total = total_flow

    temperature = (
        sum(
            weight * stream.temperature
            for weight, stream in zip(weights, arrivals)
        )
        / total_weight
    )

    return StreamState(
        flow=total_flow,
        temperature=temperature,
        phase=phase,
        composition=_mixed_composition(
            arrivals,
            composition_weights,
            composition_total,
        ),
    )


def _mixed_composition(
    arrivals: tuple[StreamState, ...],
    weights: list[float],
    total: float,
) -> dict[str, float]:
    if not arrivals[0].composition:
        return {}

    mixed: dict[str, float] = {}

    for weight, stream in zip(weights, arrivals):
        for name, fraction in stream.composition.items():
            mixed[name] = mixed.get(name, 0.0) + weight * fraction / total

    return mixed


def _component(name: str) -> Component:
    component = COMPONENTS.get(name)

    if component is None:
        raise ValueError(
            f"unknown component {name!r} — this model carries "
            f"{sorted(COMPONENTS)}",
        )

    return component


def _check_composition(composition: Mapping[str, float]) -> None:
    if not composition:
        return

    for component, fraction in composition.items():
        if fraction < 0.0:
            raise ValueError(
                f"composition fraction for {component!r} is negative: "
                f"{fraction}",
            )

    total = sum(composition.values())

    if abs(total - 1.0) > COMPOSITION_TOLERANCE:
        raise ValueError(
            f"composition fractions must sum to 1.0, got {total} for "
            f"{sorted(composition)}",
        )


def _check_phase(phase: str) -> None:
    if phase not in PORT_PHASES:
        raise ValueError(
            f"phase {phase!r} is not one of {list(PORT_PHASES)}",
        )
