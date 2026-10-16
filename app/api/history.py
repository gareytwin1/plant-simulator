"""
Run history (T20-16) - one browser's finished runs and personal bests.

    GET /api/history -> {"recent": [{"title", "difficulty", "total", "outcome", "recorded_at"}, ...],
                         "best": {key: {"title", "total"}}}

**Identity is a browser, never a person.** `app/main.py` files every run under
an anonymous operator id: a random uuid4 hex in the `plant_operator_id` cookie
(`OPERATOR_COOKIE`), HttpOnly, SameSite=Lax, lasting
`config.OPERATOR_COOKIE_SECONDS`. It is not the session cookie, which goes
when a session is reclaimed, so a browser's history outlives its sessions and
an app restart. A missing or malformed cookie gets a fresh id
(`valid_operator_id`) and so an empty history; no request can name another
browser's id except by holding its cookie.

**Recording.** `record` is what a `TrainingSession` calls from the step that
completes a run: one `ScoreStore` row under the operator id, the scenario id
and the store's config version. `recorded_at` is wall-clock seconds since the
epoch, read here in `app/api` and never in a model; it orders a history and
plays no part in a run. A store that fails to write is logged and skipped,
because a lost history row must never stop the plant the scheduler steps.

**Reading.** `recent` is the caller's latest `config.HISTORY_RECENT_LIMIT`
runs, newest first, from any config version. `best` is the caller's highest
total per scenario among results of the store's config major (T18-5), so an
older plant's result is listed but never a best. Both name a scenario by its
catalogue key and title, never its id (T16-12), and leave out a run whose
scenario file is gone, since it has no title to show.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable, Sequence

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from app import config
from app.logging import REQUEST_LOGGER
from app.scenarios.runner import CatalogueEntry, ScenarioResult, scenario_key
from app.scoring.store import ScoreStore
from app.statetypes import JSONValue

logger = logging.getLogger(REQUEST_LOGGER)

OPERATOR_COOKIE = "plant_operator_id"

_OPERATOR_ID = re.compile(r"[0-9a-f]{32}")


def valid_operator_id(value: str | None) -> bool:
    """Whether `value` is an id this app could have issued: 32 lowercase hex
    digits, as `uuid.uuid4().hex` writes one."""
    return value is not None and _OPERATOR_ID.fullmatch(value) is not None


def record(
    store: ScoreStore,
    operator: str,
    result: ScenarioResult,
    clock: Callable[[], float] = time.time,
) -> None:
    """Store `result`, a COMPLETE run, under `operator`."""
    if result.score is None or result.metrics is None:
        logger.warning("run of scenario %s completed without a score; not stored", scenario_key(result.scenario_id))
        return

    try:
        store.record(operator, result.scenario_id, clock(), result.score, result.metrics)
    except sqlite3.Error:
        logger.exception("could not store a completed run")


def create_history_blueprint(
    operator: Callable[[], str],
    store: Callable[[], ScoreStore],
    catalogue: Sequence[CatalogueEntry],
    limit: int = config.HISTORY_RECENT_LIMIT,
) -> Blueprint:
    """`operator` and `store` are resolved once per request, as the other
    blueprints resolve their session."""
    entries = {entry.id: entry for entry in catalogue}
    blueprint = Blueprint("history", __name__)

    @blueprint.get("/api/history")
    def history() -> ResponseReturnValue:
        scores = store()
        caller = operator()

        recent: list[JSONValue] = []
        for result in scores.recent(caller, limit):
            entry = entries.get(result.scenario)
            if entry is not None:
                recent.append(
                    {
                        "title": entry.title,
                        "difficulty": entry.difficulty,
                        "total": result.score.total,
                        "outcome": result.metrics.outcome,
                        "recorded_at": result.recorded_at,
                    },
                )

        best: dict[str, JSONValue] = {}
        for scenario, result in scores.personal_bests(caller).items():
            entry = entries.get(scenario)
            if entry is not None:
                best[entry.key] = {"title": entry.title, "total": result.score.total}

        return jsonify({"recent": recent, "best": best})

    return blueprint
