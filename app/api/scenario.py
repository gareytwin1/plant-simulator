"""
Scenario API (T14-4, contract C5's scenario routes).

    POST /api/scenario/load    {"scenario": "<id>"}
    POST /api/scenario/start
    POST /api/scenario/abort
    POST /api/scenario/unload
    GET  /api/scenario/result

Every route but unload answers with the run's `ScenarioResult` as JSON, so a
console polls one shape whether the run is loaded, running or over. Unload
answers `{"phase": "idle"}`: there is no run left to report. Same shape as
`app/api/action.py` and `app/api/alarms.py`: `create_scenario_blueprint`
takes its runner as a callable resolved once per request, so this module
makes no assumption about where the runner lives - whoever wires it into
`app/main.py` decides that, and it is one file one branch at a time. What it
needs of one is the small `ScenarioControl` protocol: a bare `ScenarioRunner`
satisfies it, and so does a `TrainingSession`, whose writes publish under the
session's scheduler lock.

A refusal is one of three, so a client can tell them apart:

    404  the scenario, its plant or its initial condition does not exist
    409  the runner's phase does not allow the call (start before load,
         load while running, abort after completion, a result with nothing
         loaded)
    400  the scenario itself is bad: it fails the schema, names a tag the
         plant lacks, or asks for something the engine refuses
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app.api import validate
from app.scenarios.runner import (
    ScenarioNotFound,
    ScenarioResult,
    ScenarioStateError,
)


class ScenarioControl(Protocol):
    """What the scenario routes need of a runner."""

    def load(self, scenario_id: str) -> ScenarioResult: ...

    def start(self) -> ScenarioResult: ...

    def abort(self) -> ScenarioResult: ...

    def unload(self) -> None: ...

    def result(self) -> ScenarioResult: ...


def _ok(result: ScenarioResult) -> ResponseReturnValue:
    return jsonify(result.as_dict()), 200


def create_scenario_blueprint(get_runner: Callable[[], ScenarioControl]) -> Blueprint:
    blueprint = Blueprint("scenario", __name__)

    @blueprint.post("/api/scenario/load")
    def post_load() -> ResponseReturnValue:
        body = validate.read_object({"scenario"})
        if isinstance(body, tuple):
            return body

        scenario = validate.string_field(
            body,
            "scenario",
            "body must be a JSON object with a string 'scenario'",
        )
        if isinstance(scenario, tuple):
            return scenario

        try:
            return _ok(get_runner().load(scenario))
        except ScenarioNotFound as error:
            return jsonify({"error": str(error)}), 404
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409
        except (ValueError, KeyError) as error:
            # KeyError is a malfunction naming a tag the plant lacks; its
            # str() is the repr of its message, so read the message itself.
            message = error.args[0] if isinstance(error, KeyError) else str(error)

            return jsonify({"error": message}), 400

    @blueprint.post("/api/scenario/start")
    def post_start() -> ResponseReturnValue:
        try:
            return _ok(get_runner().start())
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

    @blueprint.post("/api/scenario/abort")
    def post_abort() -> ResponseReturnValue:
        try:
            return _ok(get_runner().abort())
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

    @blueprint.post("/api/scenario/unload")
    def post_unload() -> ResponseReturnValue:
        try:
            get_runner().unload()
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

        return jsonify({"phase": "idle"}), 200

    @blueprint.get("/api/scenario/result")
    def get_result() -> ResponseReturnValue:
        try:
            return _ok(get_runner().result())
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

    return blueprint
