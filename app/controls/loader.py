"""
Loop configuration and tag wiring (T8-3): plant config `controllers` -> real `Loop`s.

The C3 schema (already frozen by T3-1) shapes a `controllers` entry as
`{tag, pv, sp, out, mode, kp, ki, kd}`, plus an optional `action` (T8-6) and
an optional `tunable` (T16-14) — one string tag for the measurement,
one for the output, no separate "variable" field on either. That shape is
this module's whole design constraint, and it resolves cleanly against what
C3 already has, with no new key:

  - **`pv` names a published point (T20-4).** A string with no dot is a
    node id and means that node's pressure, which is all a `pv` could name
    before T20-4, so every earlier config loads unchanged. `N-201` in
    `config/plants/olefins_lite.yaml` carries a real, physical pressure this
    way - `VesselCoupling` writes it from V-101's inventory every step. A
    string with a dot is a point, `<id>.<field>`, resolved by the rule a
    `limits` entry has used since T9-5: a field of the row the plant
    publishes for a device (its solved points included), a node or a stream.
    So a level loop names `V-301.level` and a flow loop `B-FV-301.flow`. The
    point must be published, numeric, and its id in exactly one of those
    sections, all checked here against the plant as the engine first
    publishes it (`published`), so the error can name the entry. **Instruments
    are not in C3** (an `Engine` only gains one via `Engine(instruments=...)`,
    never from a config key), so a `pv` cannot name a transmitter tag; it
    names the point a transmitter would read, and the engine reads it from
    the indicated view - a biased transmitter on that point fools the loop as
    it fools the operator.

  - **A loop's measurement carries its point's unit** (`pv_unit`, from
    `app.plant.units`, T20-1), and its setpoint is checked against it, at
    load and on every `set_setpoint`: within [0, 1] for a fraction, above
    absolute zero for a temperature, and never negative for anything else -
    see `setpoint_problem`.

  - **`out` resolves to a device with a settable normalized command.** Only
    `ControlValve.set_position_target` qualifies today (`OUTPUTS`,
    deliberately an explicit per-class allowlist and not a `hasattr` check —
    the same shape as `app.disturbances.malfunction.WRITABLE`, and for the
    same reason: a device's writable surface is listed, never inferred from
    whatever method happens to share a name).

  - **A loop starts at the command its output already holds.** A `Loop`
    otherwise starts at an output of 0.0, and the engine writes a loop's
    output every step in every mode (T8-4) — so a loop bound to a valve
    standing at 0.5 would slam it to its floor on the first step. Each
    `OUTPUTS` entry therefore also names the attribute holding the device's
    current command, read once here to seed the loop's output and manual
    output, so binding a loop moves nothing.

  - **An operator may retune a loop only when its entry says `tunable:
    true`** (T16-14). The default is false, so every earlier config loads
    unchanged and no loop is opened to retuning by accident.

  - **The PID's output range is a fixed [0.0, 1.0]**, not introspected per
    device. A loop's output is a normalized 0-100% demand; `ControlValve`
    already clamps its own travel to `[min_position, 1.0]` inside
    `set_position_target`, so the loop does not need to know that floor to
    stay within it.

This loader only loads and binds — it resolves every `pv` and `out` tag once,
at load time, into the point and the real setter they name, and
constructs the `Loop` (T8-1/T8-2) each entry describes. It does not read a
measurement, call `Loop.compute()`, or write an output: execution order
against the engine step belongs to `app/engine/engine.py` (T8-4). A `Plant` already
schema-validates its `controllers` entries (T3-1) before this module ever
sees them, so the only failures here are semantic — a tag that does not
resolve, resolves to the wrong kind of thing, or a setpoint its unit
refuses — named clearly enough to
fix from the message alone, exactly as `app.plant.loader.PlantConfigError`
does for the plant it wraps.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from app.controls.modes import Loop, Mode
from app.controls.pid import PID, Action
from app.engine.coupling import VesselCoupling
from app.engine.transport import ABSOLUTE_ZERO
from app.equipment.base import Equipment
from app.equipment.valve import ControlValve
from app.plant.loader import Plant
from app.plant.units import DEG_F, FRACTION, unit_lookup
from app.statetypes import JSONValue


MODES: dict[str, Mode] = {
    "AUTO": Mode.AUTO,
    "MANUAL": Mode.MANUAL,
}

# ISA action: DIRECT means output rises as the measurement rises. An entry
# with no `action` is REVERSE, which is what every loop computed before the
# key existed (T8-6), so every earlier config loads unchanged.
ACTIONS: dict[str, Action] = {
    "DIRECT": Action.DIRECT,
    "REVERSE": Action.REVERSE,
}

# A loop's output is always this normalized range — see the module docstring.
OUTPUT_MIN = 0.0
OUTPUT_MAX = 1.0

# What a bare node id measures (see the module docstring).
NODE_PV_FIELD = "pressure"

# The sections a point's id may name, as for a limit.
PV_SECTIONS = ("equipment", "nodes", "streams")

# Units a setpoint may go below zero in, each with the value it must stay
# above. Every other unit a loop measures is an absolute pressure, a flow, a
# fraction or a rate, where a negative setpoint asks for nothing a plant can
# hold.
SETPOINT_FLOORS: dict[str, float] = {DEG_F: ABSOLUTE_ZERO}

# A plant as the engine publishes it: section -> row id -> field -> value.
Published = Mapping[str, Mapping[str, Mapping[str, JSONValue]]]


@dataclass(frozen=True)
class Output:
    """How a loop drives one class of device: the method that takes its
    normalized command, and the attribute holding the command last taken."""

    setter: str
    command: str


# Explicit allowlist, not hasattr(): a device's writable surface is listed,
# never inferred from a method that merely happens to share a name.
OUTPUTS: dict[type[Equipment], Output] = {
    ControlValve: Output(setter="set_position_target", command="position_target"),
}


class LoopConfigError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors

        super().__init__(
            f"loop config rejected, {len(errors)} problem(s):\n"
            + "\n".join(f"  {error}" for error in errors),
        )


@dataclass(frozen=True)
class LoopBinding:
    """One `controllers` entry, resolved: `pv` against the point it names,
    `out` against the device's own setter for its normalized command input.
    """

    tag: str
    loop: Loop
    # Where the loop reads its measurement in a snapshot: section, row,
    # field - the same shape as an `Instrument.point`.
    pv_point: tuple[str, str, str]
    pv_unit: str
    out_tag: str
    output_setter: Callable[[float], None]
    tunable: bool = False


def setpoint_problem(value: float, unit: str) -> str | None:
    """Why a loop measuring in `unit` cannot take setpoint `value`, or None."""
    if unit == FRACTION and not 0.0 <= value <= 1.0:
        return "must be a fraction within [0, 1]"

    floor = SETPOINT_FLOORS.get(unit)

    if floor is not None:
        if value <= floor:
            return f"must be above {floor} {unit}"
    elif value < 0.0:
        return f"must be non-negative {unit}".rstrip()

    return None


def resolve_pv(text: str, published: Published) -> tuple[str, str, str]:
    """The point a `pv` names in `published`, or ValueError saying why it
    names none."""
    row_id, dot, field = text.rpartition(".")

    if not dot:
        if text not in published["nodes"]:
            raise ValueError(f"unknown node {text!r}, only {sorted(published['nodes'])}")

        return ("nodes", text, NODE_PV_FIELD)

    sections = [section for section in PV_SECTIONS if row_id in published[section]]

    if not sections:
        raise ValueError(f"unknown point {text!r}: {row_id!r} is no device, node or stream")

    if len(sections) > 1:
        raise ValueError(f"point {text!r} is ambiguous: {row_id!r} names a row in each of {sections}")

    section = sections[0]
    row = published[section][row_id]

    if field not in row:
        raise ValueError(f"{row_id!r} publishes no {field!r}, only {sorted(row)}")

    value = row[field]

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{text!r} is {value!r}, not a number")

    return (section, row_id, field)


def load_loops(
    plant: Plant,
    published: Published,
    couplings: Iterable[VesselCoupling],
) -> dict[str, LoopBinding]:
    """Every `controllers` entry of `plant`, bound. `published` is the plant
    as its engine publishes it before any loop is added - the indicated
    view - which is what a `pv` is resolved against; `couplings` are that
    engine's, which classify the flow unit of each domain."""
    entries = plant.passthrough("controllers")
    unit_of = unit_lookup(plant.devices, plant.topologies, couplings)

    errors: list[str] = []
    tag_paths: dict[str, str] = {}
    out_paths: dict[str, str] = {}
    bindings: dict[str, LoopBinding] = {}

    for i, entry in enumerate(entries):
        path = f"$.controllers[{i}]"
        tag = entry["tag"]

        if tag in tag_paths:
            errors.append(
                f"{path}.tag: duplicate loop tag {tag!r}, "
                f"first used at {tag_paths[tag]}",
            )
            continue

        tag_paths[tag] = path

        point: tuple[str, str, str] | None = None
        unit = ""

        try:
            point = resolve_pv(entry["pv"], published)
        except ValueError as error:
            errors.append(f"{path}.pv: {error}")
        else:
            unit = unit_of(*point)
            problem = setpoint_problem(entry["sp"], unit)

            if problem is not None:
                errors.append(f"{path}.sp: {problem}, got {entry['sp']!r}")
                point = None

        device = plant.devices.get(entry["out"])
        resolvable = point is not None and device is not None

        if device is None:
            errors.append(
                f"{path}.out: unknown device {entry['out']!r}, "
                f"only {sorted(plant.devices)}",
            )
        elif type(device) not in OUTPUTS:
            errors.append(
                f"{path}.out: {entry['out']!r} is a {type(device).__name__}, "
                f"which a loop cannot drive; only "
                f"{sorted(t.__name__ for t in OUTPUTS)}",
            )
            resolvable = False
        elif entry["out"] in out_paths:
            # Two loops on one output is not a tag-resolution failure — both
            # tags are real — but silently letting it through would hand
            # T8-4 a last-write-wins race with no signal that anything is
            # wrong. Caught here, at load, where the error can still name
            # both loops.
            errors.append(
                f"{path}.out: {entry['out']!r} is already driven by loop "
                f"{out_paths[entry['out']]!r} — one output cannot take a "
                f"command from two loops",
            )
            resolvable = False
        elif point is not None:
            # Registered only once this entry is otherwise resolvable: an
            # entry with a bad pv never produces a binding, so it must not
            # be able to "claim" an out tag and block a later, valid one
            # from it — nor be named as the prior claimant in that later
            # entry's error, which would blame a loop that was never built.
            out_paths[entry["out"]] = tag

        if not resolvable:
            continue

        assert point is not None
        assert device is not None

        try:
            pid = PID(
                kp=entry["kp"],
                ki=entry["ki"],
                kd=entry["kd"],
                output_min=OUTPUT_MIN,
                output_max=OUTPUT_MAX,
                setpoint=entry["sp"],
                action=ACTIONS[entry.get("action", "REVERSE")],
            )
        except ValueError as error:
            errors.append(f"{path}: {error}")
            continue

        output = OUTPUTS[type(device)]
        loop = Loop(pid, mode=MODES[entry["mode"]])
        loop.output = loop.manual_output = float(getattr(device, output.command))

        bindings[tag] = LoopBinding(
            tag=tag,
            loop=loop,
            pv_point=point,
            pv_unit=unit,
            out_tag=entry["out"],
            output_setter=getattr(device, output.setter),
            tunable=entry.get("tunable", False),
        )

    if errors:
        raise LoopConfigError(errors)

    return bindings
