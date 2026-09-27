"""
Loop configuration and tag wiring (T8-3): plant config `controllers` -> real `Loop`s.

The C3 schema (already frozen by T3-1) shapes a `controllers` entry as
`{tag, pv, sp, out, mode, kp, ki, kd}` — one string tag for the measurement,
one for the output, no separate "variable" field on either. That shape is
this module's whole design constraint, and it resolves cleanly against what
C3 already has, with no new key:

  - **`pv` resolves to a node id.** A node carries exactly one measured
    quantity, its pressure, so a bare tag string is unambiguous — the same
    reason a `limits` entry needs a `variable` field alongside its `tag` but
    this schema does not. **Instruments are not in C3** (an `Engine` only
    gains one via `Engine(instruments=...)`, never from a config key — see
    the "Known technical debt" entry in `.workspace/memory/project_state.md`),
    so a `pv` cannot yet name a transmitter tag; it names the node whose
    pressure the loop reads, boundary or internal. `N-201` in
    `config/plants/olefins_lite.yaml` already carries a real, physical
    pressure this way — `VesselCoupling` writes it from V-101's inventory
    every step, so binding to it is not a stand-in for a missing transmitter,
    it is the same number a transmitter there would read.

  - **`out` resolves to a device with a settable normalized command.** Only
    `ControlValve.set_position_target` qualifies today (`OUTPUT_SETTERS`,
    deliberately an explicit per-class allowlist and not a `hasattr` check —
    the same shape as `app.disturbances.malfunction.WRITABLE`, and for the
    same reason: a device's writable surface is listed, never inferred from
    whatever method happens to share a name).

  - **The PID's output range is a fixed [0.0, 1.0]**, not introspected per
    device. A loop's output is a normalized 0-100% demand; `ControlValve`
    already clamps its own travel to `[min_position, 1.0]` inside
    `set_position_target`, so the loop does not need to know that floor to
    stay within it.

This loader only loads and binds — it resolves every `pv` and `out` tag once,
at load time, into the real `Node` and the real setter they name, and
constructs the `Loop` (T8-1/T8-2) each entry describes. It does not read a
measurement, call `Loop.compute()`, or write an output: execution order
against the engine step is T8-4's contract, not this one's. A `Plant` already
schema-validates its `controllers` entries (T3-1) before this module ever
sees them, so the only failures here are semantic — a tag that does not
resolve, or resolves to the wrong kind of thing — named clearly enough to
fix from the message alone, exactly as `app.plant.loader.PlantConfigError`
does for the plant it wraps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.controls.modes import Loop, Mode
from app.controls.pid import PID
from app.equipment.base import Equipment
from app.equipment.valve import ControlValve
from app.plant.loader import Plant
from app.plant.topology import Node


MODES: dict[str, Mode] = {
    "AUTO": Mode.AUTO,
    "MANUAL": Mode.MANUAL,
}

# A loop's output is always this normalized range — see the module docstring.
OUTPUT_MIN = 0.0
OUTPUT_MAX = 1.0

# Explicit allowlist, not hasattr(): a device's writable surface is listed,
# never inferred from a method that merely happens to share a name.
OUTPUT_SETTERS: dict[type[Equipment], str] = {
    ControlValve: "set_position_target",
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
    """One `controllers` entry, resolved: `pv` against the `Node` it names,
    `out` against the device's own setter for its normalized command input.
    """

    tag: str
    loop: Loop
    pv_tag: str
    pv_node: Node
    out_tag: str
    output_setter: Callable[[float], None]


def load_loops(plant: Plant) -> dict[str, LoopBinding]:
    entries = plant.to_config().get("controllers", [])

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

        node = plant.nodes.get(entry["pv"])

        if node is None:
            errors.append(
                f"{path}.pv: unknown node {entry['pv']!r}, "
                f"only {sorted(plant.nodes)}",
            )

        device = plant.devices.get(entry["out"])
        resolvable = node is not None and device is not None

        if device is None:
            errors.append(
                f"{path}.out: unknown device {entry['out']!r}, "
                f"only {sorted(plant.devices)}",
            )
        elif type(device) not in OUTPUT_SETTERS:
            errors.append(
                f"{path}.out: {entry['out']!r} is a {type(device).__name__}, "
                f"which a loop cannot drive; only "
                f"{sorted(t.__name__ for t in OUTPUT_SETTERS)}",
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
        else:
            out_paths[entry["out"]] = tag

        if not resolvable:
            continue

        assert node is not None
        assert device is not None

        try:
            pid = PID(
                kp=entry["kp"],
                ki=entry["ki"],
                kd=entry["kd"],
                output_min=OUTPUT_MIN,
                output_max=OUTPUT_MAX,
                setpoint=entry["sp"],
            )
        except ValueError as error:
            errors.append(f"{path}: {error}")
            continue

        bindings[tag] = LoopBinding(
            tag=tag,
            loop=Loop(pid, mode=MODES[entry["mode"]]),
            pv_tag=entry["pv"],
            pv_node=node,
            out_tag=entry["out"],
            output_setter=getattr(device, OUTPUT_SETTERS[type(device)]),
        )

    if errors:
        raise LoopConfigError(errors)

    return bindings
