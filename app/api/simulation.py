"""
Simulation API (T20-14, additive to contract C5).

    POST /api/simulation/pause
    POST /api/simulation/resume

No body. Each answers `{"running": bool}`: whether the plant the session shows
is now advancing. Free play pauses while idle, the run while it is running;
any other phase (loaded, complete, aborted) is a 409, the runner's own state
error, because nothing there advances.

These are not `/api/action` targets. Pausing is not an action on the plant, so
it stays out of the `ActionLog` and the score; a paused run replays from the
clock state each journaled step records. Both are idempotent: pausing a paused
plant answers 200.

Same shape as `app/api/scenario.py`: the session is a callable resolved once
per request, so this module makes no assumption about where it lives.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app.scenarios.runner import ScenarioStateError


class SimulationControl(Protocol):
    """What the simulation routes need of a session."""

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...


def create_simulation_blueprint(get_session: Callable[[], SimulationControl]) -> Blueprint:
    blueprint = Blueprint("simulation", __name__)

    def answer(apply: Callable[[SimulationControl], bool]) -> ResponseReturnValue:
        try:
            return jsonify({"running": apply(get_session())}), 200
        except ScenarioStateError as error:
            return jsonify({"error": str(error)}), 409

    @blueprint.post("/api/simulation/pause")
    def post_pause() -> ResponseReturnValue:
        return answer(lambda session: session.pause())

    @blueprint.post("/api/simulation/resume")
    def post_resume() -> ResponseReturnValue:
        return answer(lambda session: session.resume())

    return blueprint
