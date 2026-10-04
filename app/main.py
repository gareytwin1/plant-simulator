import uuid

from flask import (
    Flask,
    Response,
    g,
    has_app_context,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue

from app import config
from app import logging as plant_logging
from app.api import validate
from app.api.health import create_health_blueprint
from app.engine.scheduler import Scheduler
from app.engine.sessions import SessionRegistry


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

SESSION_COOKIE = "plant_session_id"

sessions = SessionRegistry()

rate_limiter = validate.RateLimiter(config.API_RATE_PER_SECOND, config.API_RATE_BURST)

PUMP_PATH_PREFIX = "/api/pump/"
PUMP_PAGE_PATH = "/pump"


def _request_sim_time() -> float | None:
    """Sim time of the request's own plant, None where it has none (a health
    probe, a refused request)."""
    plant = g.get("plant") if has_app_context() else None

    if plant is None:
        return None

    # The two machines run on separate engines with separate clocks.
    is_pump = request.path == PUMP_PAGE_PATH or request.path.startswith(PUMP_PATH_PREFIX)
    scheduler: Scheduler = (
        plant.pump_scheduler if is_pump else plant.compressor_scheduler
    )

    return scheduler.snapshot().sim_time


plant_logging.configure(sim_time=_request_sim_time)
plant_logging.log_requests(app, _request_sim_time)

# First, so a refused request never reaches the session hook below.
validate.install(app, lambda: rate_limiter)


def _health_schedulers() -> dict[str, Scheduler]:
    # Never creates a session. SessionRegistry.get() does touch the named one
    # and sweeps idle ones, and the registry offers no read-only lookup yet.
    session_id = request.cookies.get(SESSION_COOKIE)
    session = sessions.get(session_id) if session_id else None

    if session is None:
        return {}

    return {
        "compressor": session.compressor_scheduler,
        "pump": session.pump_scheduler,
    }


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


@app.after_request
def persist_session_cookie(response: Response) -> Response:
    # A request refused before load_session ran (rate limit) has no session.
    if "session_id" in g:
        response.set_cookie(SESSION_COOKIE, g.session_id, httponly=True)

    return response


STEP_UNAVAILABLE_REASON = (
    "manual stepping is unavailable while the background scheduler is running"
)
SESSION_ENDED_REASON = "session ended"


def _number_field(field: str) -> float | validate.ErrorResponse:
    """Read `field` from a JSON body as a finite number, or a 4xx error."""
    body = validate.read_object({field})
    if isinstance(body, tuple):
        return body

    return validate.number_field(body, field)


def _step_refused(scheduler: Scheduler) -> ResponseReturnValue:
    """The 409 for a manual step the scheduler refused."""
    reason = SESSION_ENDED_REASON if scheduler.closed else STEP_UNAVAILABLE_REASON

    return jsonify({"error": reason}), 409


@app.route("/compressor")
def compressor_page() -> ResponseReturnValue:
    # A page render is the only real evidence a human is watching this
    # plant, so it is what starts the worker that keeps it running once the
    # response is sent. start() is idempotent, so a reload costs nothing.
    g.plant.compressor_scheduler.start()

    return render_template(
        "compressor.html",
        state=g.plant.compressor_state(),
    )


@app.route("/start")
def start() -> ResponseReturnValue:
    g.plant.compressor_scheduler.command(g.plant.compressor.start)

    return redirect(url_for("compressor_page"))


@app.route("/stop")
def stop() -> ResponseReturnValue:
    g.plant.compressor_scheduler.command(g.plant.compressor.stop)

    return redirect(url_for("compressor_page"))


@app.route("/pump")
def pump_page() -> ResponseReturnValue:
    g.plant.pump_scheduler.start()

    return render_template(
        "pump.html",
        state=g.plant.pump_state(),
    )


@app.route("/api/state")
def api_state() -> ResponseReturnValue:
    return jsonify(g.plant.compressor_state())


@app.route("/api/start", methods=["POST"])
def api_start() -> ResponseReturnValue:
    g.plant.compressor_scheduler.command(g.plant.compressor.start)

    return jsonify(g.plant.compressor_state())


@app.route("/api/stop", methods=["POST"])
def api_stop() -> ResponseReturnValue:
    g.plant.compressor_scheduler.command(g.plant.compressor.stop)

    return jsonify(g.plant.compressor_state())


@app.route("/api/step", methods=["POST"])
def api_step() -> ResponseReturnValue:
    scheduler = g.plant.compressor_scheduler

    if scheduler.step_once() is None:
        return _step_refused(scheduler)

    return jsonify(g.plant.compressor_state())


@app.post("/api/load")
def set_load() -> ResponseReturnValue:
    load_target = _number_field("load_target")
    if isinstance(load_target, tuple):
        return load_target

    g.plant.compressor_scheduler.command(
        lambda: g.plant.compressor.set_load_target(load_target)
    )

    return jsonify(g.plant.compressor_state())


@app.route("/api/pump/state")
def api_pump_state() -> ResponseReturnValue:
    return jsonify(g.plant.pump_state())


@app.route("/api/pump/start", methods=["POST"])
def api_pump_start() -> ResponseReturnValue:
    g.plant.pump_scheduler.command(g.plant.pump.start)

    return jsonify(g.plant.pump_state())


@app.route("/api/pump/stop", methods=["POST"])
def api_pump_stop() -> ResponseReturnValue:
    g.plant.pump_scheduler.command(g.plant.pump.stop)

    return jsonify(g.plant.pump_state())


@app.route("/api/pump/step", methods=["POST"])
def api_pump_step() -> ResponseReturnValue:
    scheduler = g.plant.pump_scheduler

    if scheduler.step_once() is None:
        return _step_refused(scheduler)

    return jsonify(g.plant.pump_state())


@app.post("/api/pump/speed")
def set_pump_speed() -> ResponseReturnValue:
    speed_target = _number_field("speed_target")
    if isinstance(speed_target, tuple):
        return speed_target

    g.plant.pump_scheduler.command(
        lambda: g.plant.pump.set_speed_target(speed_target)
    )

    return jsonify(g.plant.pump_state())


if __name__ == "__main__":
    app.run(debug=True)
