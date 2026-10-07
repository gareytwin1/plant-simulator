"""
Operator action endpoint (T15-1, contract C5's action route).

    POST /api/action  {"target": "K-101", "action": "start", "value": null}

One endpoint for every operator input, not a route per control - the whole
point of C5 is that `app/main.py` stops being the file every branch has to
touch. `ACTIONS` is an explicit per-class allowlist of the device methods an
operator may reach through this endpoint: same shape and same reason as
`app.disturbances.malfunction.WRITABLE` and `app.controls.loader.OUTPUTS` -
a device's operable surface is listed, never inferred from a method that
merely happens to exist under that name.

A loop tag is a target too (T16-14). `LOOP_ACTIONS` lists what an operator
may do to a loop from its faceplate: switch it to MANUAL or AUTO, move its
setpoint, set its output while it is in MANUAL and within its output range,
and retune it where its configuration says it is tunable. A device a loop
drives refuses every device action, naming the loop: the loop posts its
demand to that device every step in every mode (app/engine/engine.py), so a
direct command would hold for one step and then be overwritten without a
word. In MANUAL the loop's output is the operator's command, so the loop is
how that device is commanded.

`create_action_blueprint` takes its `Engine` and `ActionLog`, or an `apply`
callable that replaces both, as callables rather than reading them off
`flask.g` directly, so this module makes no assumption about where a plant
comes from. `main.py` passes the training session's `act`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app.api import validate
from app.controls.loader import LoopBinding
from app.controls.modes import Mode
from app.engine.engine import Engine
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.scoring.actionlog import ActionLog


@dataclass(frozen=True)
class ActionSpec:
    """One operable method: its name, and whether it takes a value."""

    method: str
    takes_value: bool


# Explicit allowlist, not hasattr(): an operator's reach through this
# endpoint is listed, never inferred. Same shape as WRITABLE and OUTPUTS.
ACTIONS: dict[type[Equipment], tuple[ActionSpec, ...]] = {
    GasCompressor: (
        ActionSpec("start", takes_value=False),
        ActionSpec("stop", takes_value=False),
        ActionSpec("set_load_target", takes_value=True),
    ),
    CentrifugalPump: (
        ActionSpec("start", takes_value=False),
        ActionSpec("stop", takes_value=False),
        ActionSpec("set_speed_target", takes_value=True),
    ),
    ControlValve: (
        ActionSpec("set_position_target", takes_value=True),
    ),
}


# What an operator may do to a loop, by the same rule as ACTIONS. No cascade
# action: no configuration builds a cascade loop yet.
LOOP_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("manual", takes_value=False),
    ActionSpec("auto", takes_value=False),
    ActionSpec("set_setpoint", takes_value=True),
    ActionSpec("set_output", takes_value=True),
    ActionSpec("set_kp", takes_value=True),
    ActionSpec("set_ki", takes_value=True),
    ActionSpec("set_kd", takes_value=True),
)

MODES: dict[str, Mode] = {"manual": Mode.MANUAL, "auto": Mode.AUTO}

# Tuning actions, by the gain each one sets. Refused unless the loop is tunable.
GAINS: dict[str, str] = {"set_kp": "kp", "set_ki": "ki", "set_kd": "kd"}


class UnknownAction(ValueError):
    """A request named a target/action pair this endpoint does not allow."""


def _spec_for(device: Equipment, action: str) -> ActionSpec:
    device_type = type(device)
    specs = ACTIONS.get(device_type, ())

    for spec in specs:
        if spec.method == action:
            return spec

    raise UnknownAction(
        f"{device.tag} is a {device_type.__name__}, which allows only "
        f"{sorted(spec.method for spec in specs)}, not {action!r}",
    )


def _loop_spec_for(tag: str, action: str) -> ActionSpec:
    for spec in LOOP_ACTIONS:
        if spec.method == action:
            return spec

    raise UnknownAction(
        f"{tag} is a loop, which allows only "
        f"{sorted(spec.method for spec in LOOP_ACTIONS)}, not {action!r}",
    )


def _checked_value(target: str, spec: ActionSpec, value: float | None) -> float | None:
    """`value` as the action takes it: a float when it takes one, else None."""
    if not spec.takes_value:
        if value is not None:
            raise ValueError(f"{target}.{spec.method} takes no value, got {value!r}")

        return None

    if value is None:
        raise ValueError(f"{target}.{spec.method} requires a value")

    problem = validate.number_problem(value, f"{target}.{spec.method} value")
    if problem is not None:
        raise ValueError(problem)

    # Normalized once, here, so the logged value is always a float
    # regardless of whether the caller's JSON used an int or a float
    # literal - the device call and the log must agree on its type.
    return float(value)


def _apply_loop(binding: LoopBinding, action: str, value: float | None) -> None:
    loop = binding.loop
    pid = loop.pid
    tag = binding.tag

    if action in MODES:
        # Only a real transition goes through the setter: re-entering MANUAL
        # would reseed manual_output from the last computed output and undo a
        # set_output taken since. The press is still logged, as an operator's
        # intent.
        if loop.mode is not MODES[action]:
            loop.mode = MODES[action]
        return

    assert value is not None  # every other loop action takes a value

    if action == "set_setpoint":
        # A loop's pv is an absolute pressure (binding.pv_unit), which cannot
        # be negative; no configuration carries a tighter setpoint range yet.
        if value < 0.0:
            raise ValueError(
                f"{tag}.set_setpoint value must be non-negative {binding.pv_unit}, got {value!r}",
            )

        pid.setpoint = value
    elif action == "set_output":
        if loop.mode is not Mode.MANUAL:
            raise ValueError(f"{tag}.set_output needs {tag} in MANUAL; it is in {loop.mode.name}")

        if not pid.output_min <= value <= pid.output_max:
            raise ValueError(
                f"{tag}.set_output value {value!r} is outside {tag}'s output range "
                f"[{pid.output_min!r}, {pid.output_max!r}]",
            )

        loop.manual_output = value
    else:
        if not binding.tunable:
            raise UnknownAction(f"{tag} is not open to operator tuning, so {action!r} is refused")

        gains = {"kp": pid.kp, "ki": pid.ki, "kd": pid.kd}
        gains[GAINS[action]] = value

        try:
            pid.retune(**gains)
        except ValueError as error:
            raise ValueError(f"{tag}.{action}: {error}") from error


def apply_action(
    equipment: Mapping[str, Equipment],
    log: ActionLog,
    sim_time: float,
    target: str,
    action: str,
    value: float | None,
    *,
    loops: Mapping[str, LoopBinding],
) -> None:
    """Validate and apply one operator action to a loop or a device, then
    record it.

    `loops` is the plant's loops by tag (`Engine.loops`), required so that no
    caller can leave a loop-driven device open to a command its loop would
    overwrite. Raises `KeyError` for an unknown target, `UnknownAction` for a
    disallowed action, and `ValueError` for a bad or missing value - the
    same three failure shapes `MalfunctionRegistry.add` and
    `Malfunction.__post_init__` already use for a device-directed write.
    A refused action changes nothing and is not logged.
    """
    if target in loops:
        spec = _loop_spec_for(target, action)
        value = _checked_value(target, spec, value)
        _apply_loop(loops[target], action, value)
    else:
        if target not in equipment:
            raise KeyError(
                f"no device registered under tag {target!r}, "
                f"only {sorted(equipment)} and loops {sorted(loops)}",
            )

        # The loop loader refuses two loops on one output, so there is at most one.
        driver = next((tag for tag, binding in loops.items() if binding.out_tag == target), None)
        if driver is not None:
            raise UnknownAction(f"{target} is driven by loop {driver}; command it through {driver}")

        spec = _spec_for(equipment[target], action)
        value = _checked_value(target, spec, value)

        if value is None:
            getattr(equipment[target], action)()
        else:
            getattr(equipment[target], action)(value)

    log.record(tag=target, action=action, value=value, sim_time=sim_time)


def create_action_blueprint(
    get_engine: Callable[[], Engine] | None = None,
    get_log: Callable[[], ActionLog] | None = None,
    apply: Callable[[str, str, float | None], None] | None = None,
) -> Blueprint:
    """Build the `/api/action` blueprint against an `Engine` and `ActionLog`
    resolved on demand - once per request, so each call to `get_engine` and
    `get_log` reaches whichever plant and log the caller's own session
    machinery has already resolved for this request.

    `apply`, when given, replaces the default `apply_action` on that engine
    and log: a caller whose plant can be swapped or stepped on another thread
    (`ScenarioRunner.act`) supplies one that holds its own lock across the
    whole action, which two per-request getters cannot. It raises the same
    `KeyError`, `UnknownAction` and `ValueError` `apply_action` does. With
    `apply`, `get_engine` and `get_log` are not used and may be left out;
    without it both are required.
    """
    if apply is None and (get_engine is None or get_log is None):
        raise ValueError("create_action_blueprint needs apply, or both get_engine and get_log")

    blueprint = Blueprint("action", __name__)

    @blueprint.post("/api/action")
    def post_action() -> ResponseReturnValue:
        body = validate.read_object({"target", "action", "value"})
        if isinstance(body, tuple):
            return body

        target = body.get("target")
        action = body.get("action")
        # Not narrowed here: apply_action rejects anything but a number or None.
        value = cast("float | None", body.get("value"))

        if not isinstance(target, str) or not isinstance(action, str):
            return validate.error_response("target and action must be strings")

        try:
            if apply is not None:
                apply(target, action, value)
            else:
                assert get_engine is not None and get_log is not None
                engine = get_engine()
                apply_action(
                    engine.equipment, get_log(), engine.clock.sim_time, target, action, value,
                    loops=engine.loops,
                )
        except (KeyError, UnknownAction, ValueError) as error:
            return jsonify({"error": str(error)}), 400

        return jsonify({"ok": True}), 200

    return blueprint
