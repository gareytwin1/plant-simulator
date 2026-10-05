"""
Alarm API (T10-3, contract C7 exposed over HTTP).

Two things a console needs beyond what `AlarmManager.evaluate()` returns: the
running record for the debrief (`GET /api/alarms/history`) and a way for the
operator to acknowledge (`POST /api/alarms/acknowledge`). Same shape as
`app/api/action.py` (T15-1): `create_alarm_blueprint` takes the history entries
and the acknowledge call as callables resolved once per request, so this module
makes no assumption about where they live - whoever wires this blueprint into
`app/main.py` decides that.

The acknowledge sequence itself is `app.alarms.acknowledge` (T16-6), run by
whatever owns the alarm manager under the lock it already holds around the step
that evaluates alarms. This module only maps its answer to HTTP:

    UNKNOWN   400  the manager has never seen the id
    ALREADY   200  `recorded: false`, nothing written
    NO_EVENT  409  history has no event for the id, state left untouched
    RECORDED  200  `recorded: true`
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app.alarms.acknowledge import Acknowledged
from app.alarms.history import AcknowledgeRecord, ClearRecord, HistoryEntry
from app.api import validate


def _serialize(entry: HistoryEntry) -> dict[str, object]:
    if isinstance(entry, AcknowledgeRecord):
        return {
            "type": "acknowledge",
            "id": entry.alarm_id,
            "tag": entry.tag,
            "sim_time": entry.sim_time,
        }

    if isinstance(entry, ClearRecord):
        return {
            "type": "clear",
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
    get_entries: Callable[[], tuple[HistoryEntry, ...]],
    acknowledge: Callable[[str], Acknowledged],
) -> Blueprint:
    """Build the `/api/alarms/*` blueprint against the alarm history and the
    acknowledge call of whichever plant the caller's own session machinery
    has already resolved for this request.
    """
    blueprint = Blueprint("alarms", __name__)

    @blueprint.get("/api/alarms/history")
    def get_alarm_history() -> ResponseReturnValue:
        return jsonify([_serialize(entry) for entry in get_entries()]), 200

    @blueprint.post("/api/alarms/acknowledge")
    def post_alarm_acknowledge() -> ResponseReturnValue:
        body = validate.read_object({"alarm_id"})
        if isinstance(body, tuple):
            return body

        alarm_id = validate.string_field(body, "alarm_id")
        if isinstance(alarm_id, tuple):
            return alarm_id

        outcome = acknowledge(alarm_id)

        if outcome is Acknowledged.UNKNOWN:
            return jsonify({"error": f"unknown alarm_id: {alarm_id!r}"}), 400

        if outcome is Acknowledged.NO_EVENT:
            return jsonify(
                {"error": f"alarm_id {alarm_id!r} has no recorded event in history"}
            ), 409

        return jsonify({"ok": True, "recorded": outcome is Acknowledged.RECORDED}), 200

    return blueprint
