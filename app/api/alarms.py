"""
Alarm API (T10-3, contract C7 exposed over HTTP).

Two things a console needs beyond what `AlarmManager.evaluate()` returns: the
running record for the debrief (`GET /api/alarms/history`) and a way for the
operator to acknowledge (`POST /api/alarms/acknowledge`). Same shape as
`app/api/action.py` (T15-1): `create_alarm_blueprint` takes its
`AlarmManager`, `AlarmHistory` and a sim-time source as callables resolved
once per request, so this module makes no assumption about where they live -
whoever wires this blueprint into `app/main.py` decides that.

Acknowledging checks three things in sequence, all under one lock so two
concurrent requests for the same id cannot both act: `manager.get()` resolves
the real `Alarm` (`None` is an unknown id, 400); `alarm.acknowledged` is the
redundancy check (already ACKED, cleared to NORMAL, or never raised all read
as `True`, per `Alarm`'s own state machine - including a point that has
never left NORMAL, since `AlarmManager.evaluate`'s `setdefault` still binds
it an `Alarm`) - `True` reports `recorded: false` without writing a second
`AcknowledgeRecord`; and `history.tag_of()` is checked before
`manager.acknowledge()` runs, since a history that has drifted from the
manager (fresh history, reused manager; a missed `record_events` call) has
no tag for the id - `None` is a 409 with the alarm's state left untouched,
rather than acknowledging first and having nothing to record. The fetched
tag passes straight to `record_acknowledge()`, which trusts it rather than
looking it up again.
"""

from __future__ import annotations

import threading
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
    lock = threading.Lock()

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

        manager = get_manager()
        history = get_history()

        with lock:
            alarm = manager.get(alarm_id)
            if alarm is None:
                return jsonify({"error": f"unknown alarm_id: {alarm_id!r}"}), 400

            if alarm.acknowledged:
                return jsonify({"ok": True, "recorded": False}), 200

            tag = history.tag_of(alarm_id)
            if tag is None:
                return jsonify(
                    {"error": f"alarm_id {alarm_id!r} has no recorded event in history"}
                ), 409

            sim_time = get_sim_time()
            manager.acknowledge(alarm_id, sim_time)
            history.record_acknowledge(alarm_id, tag, sim_time)

        return jsonify({"ok": True, "recorded": True}), 200

    return blueprint
