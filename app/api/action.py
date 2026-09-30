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

`create_action_blueprint` takes its `Engine` and `ActionLog` as callables
rather than reading them off `flask.g` directly. No session shape that
carries a config-loaded, multi-device plant exists on `main.py` yet - only
the legacy per-device `Session` - so this module makes no assumption about
where its `Engine` comes from. Whoever wires this blueprint into `main.py`
decides that; coordinate with them before assuming a shape (see the task's
own note in the build plan).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

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


def apply_action(
    equipment: Mapping[str, Equipment],
    log: ActionLog,
    sim_time: float,
    target: str,
    action: str,
    value: float | None,
) -> None:
    """Validate and apply one operator action, then record it.

    Raises `KeyError` for an unknown target, `UnknownAction` for a
    disallowed action, and `ValueError` for a bad or missing value - the
    same three failure shapes `MalfunctionRegistry.add` and
    `Malfunction.__post_init__` already use for a device-directed write.
    """
    if target not in equipment:
        raise KeyError(f"no device registered under tag {target!r}, only {sorted(equipment)}")

    device = equipment[target]
    spec = _spec_for(device, action)

    if spec.takes_value:
        if value is None:
            raise ValueError(f"{target}.{action} requires a value")

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{target}.{action} value must be a number, got {value!r}")

        if not math.isfinite(value):
            raise ValueError(f"{target}.{action} value must be finite, got {value!r}")

        # Normalized once, here, so the logged value is always a float
        # regardless of whether the caller's JSON used an int or a float
        # literal - the device call and the log must agree on its type.
        value = float(value)
        getattr(device, action)(value)
    else:
        if value is not None:
            raise ValueError(f"{target}.{action} takes no value, got {value!r}")

        getattr(device, action)()

    log.record(tag=target, action=action, value=value, sim_time=sim_time)


def create_action_blueprint(
    get_engine: Callable[[], Engine],
    get_log: Callable[[], ActionLog],
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
    `KeyError`, `UnknownAction` and `ValueError` `apply_action` does.
    """
    blueprint = Blueprint("action", __name__)

    @blueprint.post("/api/action")
    def post_action() -> ResponseReturnValue:
        body = request.get_json(silent=True)

        if not isinstance(body, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        target = body.get("target")
        action = body.get("action")
        value = body.get("value")

        if not isinstance(target, str) or not isinstance(action, str):
            return jsonify({"error": "target and action must be strings"}), 400

        try:
            if apply is not None:
                apply(target, action, value)
            else:
                engine = get_engine()
                apply_action(engine.equipment, get_log(), engine.clock.sim_time, target, action, value)
        except (KeyError, UnknownAction, ValueError) as error:
            return jsonify({"error": str(error)}), 400

        return jsonify({"ok": True}), 200

    return blueprint
