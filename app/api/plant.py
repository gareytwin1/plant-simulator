"""
Plant description (T20-2, contract C5's plant route).

    GET /api/plant -> {"plant": id, "graphic": url | null,
        "equipment": {tag: {"kind", "service", "points", "actions", "driven_by"}},
        "controllers": {tag: {"service", "pv", "out"}},
        "interlocks": {tag: {"devices": [tag, ...]}}}

What a console needs to draw and operate the plant the session shows, so no
browser code names a tag, a device class or an action. `plant` is the plant
file's stem, the name a scenario's `plant` and `config.FREE_PLAY_PLANT` give
it. `graphic` is `static/graphics/<plant>.svg` by convention, or null when the
plant has none.

For each device: `kind` is its class's `KIND` (`app.api.visibility`), `service`
the plant's own words for it (C3's optional `service`, else null), `points`
its operator-visible `<tag>.<field>` points followed by its branch's stream
flow when it sits in exactly one branch, `actions` its `ACTIONS` method names
(`app.api.action`) and `driven_by` the loop that drives it. A driven device
lists no actions, because the action route refuses them all. A loop gives its
`service`, the point its `pv` reads and its `out` device. An interlock lists
only the devices it acts on: its condition is how a trainee diagnoses a trip,
and stays off the page, as do faults, malfunctions and the scenario's id.

Everything but `plant` and `graphic` is fixed when a `PlantRuntime` is built
(`describe`); the route asks for it through a callable resolved once per
request, as the trend routes do, and never starts a scheduler.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from flask import Blueprint, jsonify, url_for
from flask.typing import ResponseReturnValue

from app.api.action import ACTIONS
from app.api.visibility import KIND, operator_view
from app.engine.engine import Engine
from app.plant.loader import Plant
from app.safety.actions import TripAction
from app.statetypes import JSONValue


def describe(
    plant: Plant,
    engine: Engine,
    trip_actions: Mapping[str, Sequence[TripAction]],
) -> dict[str, JSONValue]:
    """The `equipment`, `controllers` and `interlocks` sections of the plant
    description for `engine`, built from `plant`. `trip_actions` maps each
    interlock tag to its resolved actions (`TripSystem.actions`)."""
    view = operator_view(engine.snapshot(), engine.equipment)
    rows = view["equipment"]
    streams = view["streams"]
    assert isinstance(rows, dict) and isinstance(streams, dict)

    branches: dict[str, list[str]] = {}
    for topology in engine.topologies.values():
        for branch in topology.branches.values():
            branches.setdefault(branch.device.tag, []).append(branch.id)

    drivers = {binding.out_tag: tag for tag, binding in engine.loops.items()}

    equipment: dict[str, JSONValue] = {}
    for tag, device in engine.equipment.items():
        row = rows.get(tag)
        points: list[JSONValue] = [f"{tag}.{field}" for field in sorted(row)] if isinstance(row, dict) else []
        own = branches.get(tag, [])

        if len(own) == 1 and own[0] in streams:
            points.append(f"{own[0]}.flow")

        driver = drivers.get(tag)
        actions: list[JSONValue] = [] if driver is not None else [spec.method for spec in ACTIONS.get(type(device), ())]

        equipment[tag] = {
            "kind": KIND.get(type(device), ""),
            "service": plant.services.get(tag),
            "points": points,
            "actions": actions,
            "driven_by": driver,
        }

    services = {entry["tag"]: entry.get("service") for entry in plant.passthrough("controllers")}
    controllers: dict[str, JSONValue] = {}
    for tag, binding in engine.loops.items():
        _, row_id, field = binding.pv_point
        controllers[tag] = {"service": services.get(tag), "pv": f"{row_id}.{field}", "out": binding.out_tag}

    interlocks: dict[str, JSONValue] = {
        tag: {"devices": list(dict.fromkeys(action.tag for action in actions))}
        for tag, actions in trip_actions.items()
    }

    return {"equipment": equipment, "controllers": controllers, "interlocks": interlocks}


def create_plant_blueprint(
    get_description: Callable[[], tuple[str, dict[str, JSONValue]]],
    graphics: Path,
) -> Blueprint:
    """Build the `/api/plant` blueprint. `get_description` returns the shown
    plant's id and its `describe` sections, read together; `graphics` is the
    directory holding each plant's `<id>.svg`, served as `static/graphics/`."""
    blueprint = Blueprint("plant", __name__)

    @blueprint.get("/api/plant")
    def get_plant() -> ResponseReturnValue:
        plant_id, sections = get_description()
        graphic = url_for("static", filename=f"graphics/{plant_id}.svg") if (graphics / f"{plant_id}.svg").is_file() else None

        return jsonify({"plant": plant_id, "graphic": graphic, **sections}), 200

    return blueprint
