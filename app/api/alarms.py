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
as "part of C7's frozen signature; T10-3 records it in history".

Whether an acknowledge is a no-op is read from `Alarm.acknowledged` itself
(via `AlarmManager.get`, T10-3's own additive accessor), never guessed from
what `AlarmHistory` happens to have recorded - a point that has never left
NORMAL still gets an `Alarm` bound to it (`AlarmManager.evaluate`'s
`setdefault`), so its id is real and already "acknowledged" (trivially, per
`Alarm`'s own state machine) despite never appearing in history at all. A
request for an alarm that is already acknowledged - already ACKED, cleared to
NORMAL, or never raised - reports `recorded: false` rather than writing a
second `AcknowledgeRecord`; the read-check-act-record sequence runs under one
lock so two concurrent requests for the same id cannot both see "not yet
acknowledged" and both record.

`AlarmHistory.tag_of` is checked before `manager.acknowledge()` runs, not
after - `record_acknowledge()` can still raise if history and manager have
drifted apart (a fresh history for a reused manager; a missed
`record_events` call), and checking first means that failure leaves the
manager's state untouched instead of acknowledging an alarm whose ack then
never reaches the debrief.
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
