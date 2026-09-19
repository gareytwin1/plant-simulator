import uuid

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

from app.engine.sessions import SessionRegistry


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

SESSION_COOKIE = "plant_session_id"

sessions = SessionRegistry()


@app.before_request
def load_session():
    session_id = request.cookies.get(SESSION_COOKIE)
    session = sessions.get(session_id) if session_id else None

    if session is None:
        session_id = uuid.uuid4().hex
        session = sessions.create(session_id)

    g.session_id = session_id
    g.plant = session


@app.after_request
def persist_session_cookie(response):
    response.set_cookie(SESSION_COOKIE, g.session_id, httponly=True)
    return response


@app.route("/compressor")
def compressor_page():
    return render_template(
        "compressor.html",
        state=g.plant.compressor.get_state(),
    )


@app.route("/start")
def start():
    g.plant.compressor.start()
    return redirect(url_for("compressor_page"))


@app.route("/stop")
def stop():
    g.plant.compressor.stop()
    return redirect(url_for("compressor_page"))


@app.route("/pump")
def pump_page():
    return render_template(
        "pump.html",
        state=g.plant.pump.get_state(),
    )


@app.route("/api/state")
def api_state():
    return jsonify(g.plant.compressor.get_state())


@app.route("/api/start", methods=["POST"])
def api_start():
    g.plant.compressor.start()
    return jsonify(g.plant.compressor.get_state())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    g.plant.compressor.stop()
    return jsonify(g.plant.compressor.get_state())


@app.route("/api/step", methods=["POST"])
def api_step():
    g.plant.compressor.step()
    return jsonify(g.plant.compressor.get_state())


@app.post("/api/valve")
def set_valve():
    data = request.get_json()

    g.plant.compressor.set_discharge_valve_position(
        data["discharge_valve_position"]
    )

    return jsonify(g.plant.compressor.get_state())


@app.post("/api/load")
def set_load():
    data = request.get_json()

    g.plant.compressor.set_load_target(
        float(data["load_target"])
    )

    return jsonify(g.plant.compressor.get_state())


@app.route("/api/pump/state")
def api_pump_state():
    return jsonify(g.plant.pump.get_state())


@app.route("/api/pump/start", methods=["POST"])
def api_pump_start():
    g.plant.pump.start()
    return jsonify(g.plant.pump.get_state())


@app.route("/api/pump/stop", methods=["POST"])
def api_pump_stop():
    g.plant.pump.stop()
    return jsonify(g.plant.pump.get_state())


@app.route("/api/pump/step", methods=["POST"])
def api_pump_step():
    g.plant.pump.step()
    return jsonify(g.plant.pump.get_state())


@app.post("/api/pump/speed")
def set_pump_speed():
    data = request.get_json()

    g.plant.pump.set_speed_target(
        float(data["speed_target"])
    )

    return jsonify(g.plant.pump.get_state())


if __name__ == "__main__":
    app.run(debug=True)
