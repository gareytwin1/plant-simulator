"""
Alarm API (T10-3, contract C7 exposed over HTTP).

Two things a console needs beyond what `AlarmManager` computes each step: the
running record for the debrief (`GET /api/alarms/history`) and a way for the
operator to acknowledge (`POST /api/alarms/acknowledge`). Same shape as
`app/api/action.py` (T15-1): `create_alarm_blueprint` takes its
`AlarmManager`, `AlarmHistory` and a sim-time source as callables resolved
once per request, so this module makes no assumption about where they live -
whoever wires this blueprint into `app/main.py` decides that.

Acknowledging here does two things in sequence, both against the same
`sim_time`: `AlarmManager.acknowledge()` transitions the alarm's own state
machine, and `AlarmHistory.record_acknowledge()` records that it happened -
matching how `AlarmManager.acknowledge`'s docstring already flags `sim_time`
as "part of C7's frozen signature; T10-3 records it in history". A request
for an alarm `AlarmHistory` already has as acknowledged is a no-op reporting
`recorded: false`, not a second `AcknowledgeRecord` - a retried request or a
double click must not read as two separate operator acknowledgements in the
debrief.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from app.alarms.history import AcknowledgeRecord, AlarmHistory, HistoryEntry
from app.alarms.manager import AlarmManager


def _serialize(entry: HistoryEntry) -> dict[str, object]:
    if isinstance(entry, AcknowledgeRecord):
        return {
            "type": "acknowledge",
            "id": entry.alarm_id,
            "tag": entry.tag,
            "sim_time": entry.sim_time,
        }

    return {
        "id": entry.id,
        "sim_time": entry.sim_time,
        "tag": entry.tag,
        "priority": entry.priority.value,
        "message": entry.message,
        "data": entry.data,
        "type": entry.type,
    }


def create_alarm_blueprint(
    get_manager: Callable[[], AlarmManager],
    get_history: Callable[[], AlarmHistory],
    get_sim_time: Callable[[], float],
) -> Blueprint:
    """Build the `/api/alarms/*` blueprint against an `AlarmManager` and
    `AlarmHistory` resolved on demand - once per request, so each call
    reaches whichever manager and history the caller's own session
    machinery has already resolved for this request.
    """
    blueprint = Blueprint("alarms", __name__)

    @blueprint.get("/api/alarms/history")
    def get_alarm_history() -> ResponseReturnValue:
        entries = get_history().entries()
        return jsonify([_serialize(entry) for entry in entries]), 200

    @blueprint.post("/api/alarms/acknowledge")
    def post_alarm_acknowledge() -> ResponseReturnValue:
        body = request.get_json(silent=True)

        if not isinstance(body, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        alarm_id = body.get("alarm_id")

        if not isinstance(alarm_id, str):
            return jsonify({"error": "alarm_id must be a string"}), 400

        history = get_history()

        if history.is_acknowledged(alarm_id):
            return jsonify({"ok": True, "recorded": False}), 200

        sim_time = get_sim_time()

        try:
            get_manager().acknowledge(alarm_id, sim_time)
        except KeyError as error:
            return jsonify({"error": error.args[0]}), 400

        history.record_acknowledge(alarm_id, sim_time)

        return jsonify({"ok": True, "recorded": True}), 200

    return blueprint
