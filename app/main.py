import functools
import uuid

from flask import Flask, Response, g, has_app_context, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from app import config
from app import logging as plant_logging
from app.api import validate
from app.api.action import create_action_blueprint
from app.api.alarms import create_alarm_blueprint
from app.api.health import create_health_blueprint
from app.api.scenario import create_scenario_blueprint
from app.api.stream import create_stream_blueprint
from app.engine.scheduler import Scheduler
from app.engine.sessions import SessionRegistry
from app.scenarios.runner import ScenarioLibrary
from app.training.session import TrainingSession


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

SESSION_COOKIE = "plant_session_id"

library = ScenarioLibrary()

sessions = SessionRegistry(factory=functools.partial(TrainingSession, library))

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
validate.install(app, lambda: rate_limiter)


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

    if session is None:
        session_id = uuid.uuid4().hex
        session = sessions.create(session_id)

    g.session_id = session_id
    g.plant = session


app.register_blueprint(create_scenario_blueprint(lambda: g.plant))
app.register_blueprint(create_action_blueprint(apply=lambda target, action, value: g.plant.act(target, action, value)))
app.register_blueprint(
    create_alarm_blueprint(
        lambda: g.plant.alarm_entries(),
        lambda alarm_id: g.plant.acknowledge(alarm_id),
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

    return response


@app.get("/")
def landing() -> ResponseReturnValue:
    # Never starts the scheduler: only the console's render does.
    standing = g.plant.standing()

    return render_template(
        "index.html",
        standing=standing,
        title=TITLES.get(standing.scenario_id) if standing.scenario_id else None,
        phase_labels=PHASE_LABELS,
        scenarios=CATALOGUE,
    )


@app.get("/console")
def console() -> ResponseReturnValue:
    # The first route that starts a scheduler: the page's stream and alarm
    # polling read a plant that advances on its own clock. Idempotent.
    g.plant.training_scheduler.start()

    return render_template("console.html", stream_interval_seconds=config.STREAM_INTERVAL_SECONDS)


@app.get("/api/snapshot")
def api_snapshot() -> ResponseReturnValue:
    # Never starts the scheduler: only the console's render does.
    plant = g.plant

    return jsonify(plant.operator_view(plant.training_scheduler.snapshot()))


if __name__ == "__main__":
    app.run(debug=True)
