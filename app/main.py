import uuid
from pathlib import Path

from flask import Flask, Response, g, has_app_context, jsonify, render_template, request, url_for
from flask.typing import ResponseReturnValue

from app import config
from app import logging as plant_logging
from app.api import history, validate
from app.api.action import create_action_blueprint
from app.api.alarms import create_alarm_blueprint
from app.api.health import create_health_blueprint
from app.api.plant import create_plant_blueprint
from app.api.scenario import create_scenario_blueprint
from app.api.simulation import create_simulation_blueprint
from app.api.stream import create_stream_blueprint
from app.api.trend import create_trend_blueprint
from app.engine.scheduler import Scheduler
from app.engine.sessions import SessionRegistry
from app.scenarios.runner import ScenarioLibrary, ScenarioResult
from app.scoring.store import ScoreStore
from app.training.session import TrainingSession


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

SESSION_COOKIE = "plant_session_id"

library = ScenarioLibrary()

# Opened once per process (T20-16); the single worker keeps SQLite single-writer.
Path(config.SCORE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
store = ScoreStore(config.SCORE_DB_PATH)


def _record(operator: str, result: ScenarioResult) -> None:
    history.record(store, operator, result)


def _new_session() -> TrainingSession:
    # load_session sets g.operator_id before it creates a session. One made
    # outside a request (a test, a script) has no operator and records nothing.
    operator_id: str | None = g.get("operator_id") if has_app_context() else None

    return TrainingSession(library, operator_id, _record)


sessions = SessionRegistry(factory=_new_session)

rate_limiter = validate.RateLimiter(config.API_RATE_PER_SECOND, config.API_RATE_BURST)


def _request_sim_time() -> float | None:
    """Sim time of the request's own plant, None where it has none (a health
    probe, a refused request)."""
    plant: TrainingSession | None = g.get("plant") if has_app_context() else None

    if plant is None:
        return None

    return plant.training_scheduler.snapshot().sim_time


plant_logging.configure(sim_time=_request_sim_time)
plant_logging.log_requests(app, _request_sim_time)

# First, so a refused request never reaches the session hook below.
validate.install(app, lambda: rate_limiter, trusted_proxies=config.API_TRUSTED_PROXIES)


def _health_schedulers() -> dict[str, Scheduler]:
    # Never creates a session, and peek() neither touches it nor sweeps.
    session_id = request.cookies.get(SESSION_COOKIE)
    session = sessions.peek(session_id) if session_id else None

    if session is None:
        return {}

    return {"training": session.training_scheduler}


app.register_blueprint(create_health_blueprint(_health_schedulers, lambda: len(sessions)))


@app.before_request
def load_session() -> None:
    if request.blueprint == "health":
        return

    session_id = request.cookies.get(SESSION_COOKIE)
    session = sessions.get(session_id) if session_id else None

    cookie = request.cookies.get(history.OPERATOR_COOKIE)
    g.operator_id = cookie if history.valid_operator_id(cookie) else uuid.uuid4().hex

    if session is None:
        session_id = uuid.uuid4().hex
        session = sessions.create(session_id)

    g.session_id = session_id
    g.plant = session
    # A live session's operator wins over the cookie, so the runs it records
    # and the history this browser reads are always filed under one id. A
    # session made outside a request has none, and records nothing.
    if session.operator_id is not None:
        g.operator_id = session.operator_id


app.register_blueprint(create_scenario_blueprint(lambda: g.plant))
app.register_blueprint(create_simulation_blueprint(lambda: g.plant))
app.register_blueprint(
    create_plant_blueprint(
        lambda: g.plant.plant_description(),
        Path(app.static_folder or "") / "graphics",
    ),
)
app.register_blueprint(create_action_blueprint(apply=lambda target, action, value: g.plant.act(target, action, value)))
app.register_blueprint(
    create_alarm_blueprint(
        lambda: g.plant.alarm_entries(),
        lambda alarm_id: g.plant.acknowledge(alarm_id),
    ),
)
app.register_blueprint(
    create_trend_blueprint(
        lambda: g.plant.trend_points(),
        lambda points: g.plant.trend_history(points),
        lambda: g.plant.trend_limits(),
        lambda: g.plant.trend_descriptors(),
        lambda: g.plant.trend_units(),
    ),
)
app.register_blueprint(
    create_stream_blueprint(
        lambda: g.plant.training_scheduler,
        config.STREAM_INTERVAL_SECONDS,
        hold=lambda: sessions.lease(g.session_id),
        get_view=lambda: g.plant.operator_view,
    ),
)

# Read once at import, so a bad scenario file stops the app at startup rather
# than failing a request. Config is not reloaded while the app runs.
CATALOGUE = library.catalogue()
TITLES = {entry.id: entry.title for entry in CATALOGUE}

app.register_blueprint(history.create_history_blueprint(lambda: g.operator_id, lambda: store, CATALOGUE))

PHASE_LABELS = {
    "idle": "",
    "loaded": "Loaded, not started",
    "running": "Running",
    "complete": "Finished",
    "aborted": "Aborted",
}


@app.after_request
def persist_session_cookie(response: Response) -> Response:
    # A request refused before load_session ran (rate limit) has no session.
    if "session_id" in g:
        response.set_cookie(SESSION_COOKIE, g.session_id, httponly=True)

    # Re-sent on every page load as well, so a browser in use never ages out.
    page = request.endpoint in ("landing", "console")
    if "operator_id" in g and (page or request.cookies.get(history.OPERATOR_COOKIE) != g.operator_id):
        response.set_cookie(
            history.OPERATOR_COOKIE,
            g.operator_id,
            max_age=config.OPERATOR_COOKIE_SECONDS,
            httponly=True,
            samesite="Lax",
        )

    return response


def thumbnail_url() -> str | None:
    """The plant graphic the landing page shows: every scenario runs on the
    free-play plant today (T20-2 gives a plant its own graphic by file stem).
    The page learns nothing else about the plant."""
    name = f"graphics/{config.FREE_PLAY_PLANT}.svg"
    if not (Path(app.static_folder or "") / name).is_file():
        return None

    return url_for("static", filename=name)


@app.get("/")
def landing() -> ResponseReturnValue:
    # Never starts the scheduler: only the console's render does.
    standing = g.plant.standing()

    return render_template(
        "index.html",
        stream_interval_seconds=config.STREAM_INTERVAL_SECONDS,
        plant_running=g.plant.training_scheduler.running,
        standing=standing,
        title=TITLES.get(standing.scenario_id) if standing.scenario_id else None,
        phase_labels=PHASE_LABELS,
        scenarios=CATALOGUE,
        thumbnail=thumbnail_url(),
    )


@app.get("/console")
def console() -> ResponseReturnValue:
    # The first route that starts a scheduler: the page's stream and alarm
    # polling read a plant that advances on its own clock. Idempotent.
    g.plant.training_scheduler.start()
    standing = g.plant.standing()

    return render_template(
        "console.html",
        stream_interval_seconds=config.STREAM_INTERVAL_SECONDS,
        plant_running=True,
        standing=standing,
        title=TITLES.get(standing.scenario_id) if standing.scenario_id else None,
        phase_labels=PHASE_LABELS,
    )


@app.get("/api/snapshot")
def api_snapshot() -> ResponseReturnValue:
    # Never starts the scheduler: only the console's render does.
    plant = g.plant

    return jsonify(plant.operator_view(plant.training_scheduler.snapshot()))


if __name__ == "__main__":
    app.run(debug=True)
