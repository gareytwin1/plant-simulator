"""
Scenario API (T14-4, contract C5's scenario routes).

    POST /api/scenario/load    {"scenario": "<key>"}
    POST /api/scenario/start
    POST /api/scenario/abort
    POST /api/scenario/unload
    GET  /api/scenario/result

Every route but unload answers with the run's result as JSON, so a console
polls one shape whether the run is loaded, running or over. Unload
answers `{"phase": "idle"}`: there is no run left to report. Same shape as
`app/api/action.py` and `app/api/alarms.py`: `create_scenario_blueprint`
takes its runner as a callable resolved once per request, so this module
makes no assumption about where the runner lives - whoever wires it into
`app/main.py` decides that, and it is one file one branch at a time. What it
needs of one is the small `ScenarioControl` protocol: a bare `ScenarioRunner`
satisfies it, and so does a `TrainingSession`, whose writes publish under the
session's scheduler lock.

**The browser never learns a scenario's cause while the run is live (T16-12).**
A scenario's id names its fault (`pump_trip`), so the page holds a `key` (see
`scenario_key`) and load takes that. `public_result` is the one place a result
is rendered: while loaded or running it carries the key, title, phase, times,
objectives as status only and the operator's own actions; once complete or
aborted it carries all of `ScenarioResult.as_dict` plus the title and the
scenario's description as the `debrief`. Inside the server nothing changes.

A refusal is one of three, so a client can tell them apart:

    404  the key, or its plant or initial condition, is not available
    409  the runner's phase does not allow the call (start before load,
         load while running, abort after completion, a result with nothing
         loaded)
    400  the scenario itself is bad: it fails the schema, names a tag the
         plant lacks, or asks for something the engine refuses

Aborting reveals the debrief at once: the reveal is tied to the run being over,
not to how far it got, so a trainee can read it by aborting immediately.

No body quotes an id, file name, tag or parameter: the 400 is one generic
sentence, and the detail goes to the request log.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app.api import validate
from app.logging import REQUEST_LOGGER
from app.scenarios.runner import (
    CatalogueEntry,
    Phase,
    ScenarioConfigError,
    ScenarioLibrary,
    ScenarioNotFound,
    ScenarioResult,
    ScenarioStateError,
    scenario_key,
)

logger = logging.getLogger(REQUEST_LOGGER)

_OVER = (Phase.COMPLETE, Phase.ABORTED)


class ScenarioControl(Protocol):
    """What the scenario routes need of a runner."""

    def load(self, scenario_id: str) -> ScenarioResult: ...

    def start(self) -> ScenarioResult: ...

    def abort(self) -> ScenarioResult: ...

    def unload(self) -> None: ...

    def result(self) -> ScenarioResult: ...


def public_result(result: ScenarioResult, entry: CatalogueEntry | None, debrief: str) -> dict[str, Any]:
    """What a browser may see of `result`. Any: a JSON document."""
    key = entry.key if entry is not None else scenario_key(result.scenario_id)
    title = entry.title if entry is not None else ""

    if result.phase in _OVER:
        return {**result.as_dict(), "key": key, "title": title, "debrief": debrief}

    return {
        "key": key,
        "title": title,
        "phase": result.phase.value,
        "elapsed_s": result.elapsed_s,
        "time_limit_s": result.time_limit_s,
        "difficulty": result.difficulty,
        "objectives": [{"status": r.status.value, "ended_at": r.ended_at} for r in result.objectives],
        "actions": [dict(action) for action in result.actions],
    }


def _debrief(library: ScenarioLibrary, scenario_id: str) -> str:
    """The scenario's description, or "" when its file changed or went away
    after the run was armed: the result is still final, only the text is lost."""
    try:
        document = library.scenario(scenario_id)
    except (ScenarioNotFound, ScenarioConfigError, OSError) as error:
        logger.warning("scenario %s has no debrief: %s", scenario_key(scenario_id), error)

        return ""

    description = document.get("description", "") if isinstance(document, dict) else ""

    return str(description).strip()


def create_scenario_blueprint(
    get_runner: Callable[[], ScenarioControl],
    library: ScenarioLibrary | None = None,
) -> Blueprint:
    blueprint = Blueprint("scenario", __name__)
    library = library or ScenarioLibrary()
    by_key = {entry.key: entry for entry in library.catalogue()}
    by_id = {entry.id: entry for entry in by_key.values()}

    def render(result: ScenarioResult) -> ResponseReturnValue:
        debrief = ""

        if result.phase in _OVER:
            debrief = _debrief(library, result.scenario_id)

        return jsonify(public_result(result, by_id.get(result.scenario_id), debrief)), 200

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

        entry = by_key.get(scenario)
        if entry is None:
            return jsonify({"error": "that scenario is not available"}), 404

        try:
            return render(get_runner().load(entry.id))
        except ScenarioNotFound as error:
            logger.warning("scenario %s not available: %s", entry.key, error)

            return jsonify({"error": "that scenario is not available"}), 404
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409
        except (ValueError, KeyError) as error:
            # KeyError is a malfunction naming a tag the plant lacks; its
            # str() is the repr of its message, so read the message itself.
            detail = error.args[0] if isinstance(error, KeyError) else str(error)
            logger.warning("scenario %s could not be set up: %s", entry.key, detail)

            return jsonify({"error": "that scenario could not be set up"}), 400

    @blueprint.post("/api/scenario/start")
    def post_start() -> ResponseReturnValue:
        try:
            return render(get_runner().start())
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

    @blueprint.post("/api/scenario/abort")
    def post_abort() -> ResponseReturnValue:
        try:
            return render(get_runner().abort())
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
            return render(get_runner().result())
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

    return blueprint
