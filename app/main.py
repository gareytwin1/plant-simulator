import math
import uuid

from flask import Flask, Response, g, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from app.engine.sessions import SessionRegistry


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

SESSION_COOKIE = "plant_session_id"

sessions = SessionRegistry()


@app.before_request
def load_session() -> None:
    session_id = request.cookies.get(SESSION_COOKIE)
    session = sessions.get(session_id) if session_id else None

    if session is None:
        session_id = uuid.uuid4().hex
        session = sessions.create(session_id)

    g.session_id = session_id
    g.plant = session


@app.after_request
def persist_session_cookie(response: Response) -> Response:
    response.set_cookie(SESSION_COOKIE, g.session_id, httponly=True)
    return response


STEP_UNAVAILABLE_REASON = (
    "manual stepping is unavailable while the background scheduler is running"
)


def _number_field(field: str) -> tuple[float, None] | tuple[None, ResponseReturnValue]:
    """Read `field` from a JSON body as a finite int or float, or a 400 error."""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return None, (jsonify({"error": "request body must be a JSON object"}), 400)

    if field not in data:
        return None, (jsonify({"error": f"missing field: {field}"}), 400)

    value = data[field]

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, (jsonify({"error": f"{field} must be a number"}), 400)

    if not math.isfinite(value):
        return None, (jsonify({"error": f"{field} must be finite"}), 400)

    return float(value), None


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
    with g.plant.compressor_scheduler.step_lock:
        g.plant.compressor.start()

    return redirect(url_for("compressor_page"))


@app.route("/stop")
def stop() -> ResponseReturnValue:
    with g.plant.compressor_scheduler.step_lock:
        g.plant.compressor.stop()

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
    with g.plant.compressor_scheduler.step_lock:
        g.plant.compressor.start()

    return jsonify(g.plant.compressor_state())


@app.route("/api/stop", methods=["POST"])
def api_stop() -> ResponseReturnValue:
    with g.plant.compressor_scheduler.step_lock:
        g.plant.compressor.stop()

    return jsonify(g.plant.compressor_state())


@app.route("/api/step", methods=["POST"])
def api_step() -> ResponseReturnValue:
    if g.plant.compressor_scheduler.running:
        return jsonify({"error": STEP_UNAVAILABLE_REASON}), 409

    g.plant.step_compressor()
    return jsonify(g.plant.compressor_state())


@app.post("/api/load")
def set_load() -> ResponseReturnValue:
    load_target, error = _number_field("load_target")
    if error is not None:
        return error

    with g.plant.compressor_scheduler.step_lock:
        g.plant.compressor.set_load_target(load_target)

    return jsonify(g.plant.compressor_state())


@app.route("/api/pump/state")
def api_pump_state() -> ResponseReturnValue:
    return jsonify(g.plant.pump_state())


@app.route("/api/pump/start", methods=["POST"])
def api_pump_start() -> ResponseReturnValue:
    with g.plant.pump_scheduler.step_lock:
        g.plant.pump.start()

    return jsonify(g.plant.pump_state())


@app.route("/api/pump/stop", methods=["POST"])
def api_pump_stop() -> ResponseReturnValue:
    with g.plant.pump_scheduler.step_lock:
        g.plant.pump.stop()

    return jsonify(g.plant.pump_state())


@app.route("/api/pump/step", methods=["POST"])
def api_pump_step() -> ResponseReturnValue:
    if g.plant.pump_scheduler.running:
        return jsonify({"error": STEP_UNAVAILABLE_REASON}), 409

    g.plant.step_pump()
    return jsonify(g.plant.pump_state())


@app.post("/api/pump/speed")
def set_pump_speed() -> ResponseReturnValue:
    speed_target, error = _number_field("speed_target")
    if error is not None:
        return error

    with g.plant.pump_scheduler.step_lock:
        g.plant.pump.set_speed_target(speed_target)

    return jsonify(g.plant.pump_state())


if __name__ == "__main__":
    app.run(debug=True)
