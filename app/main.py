from flask import Flask, jsonify, redirect, render_template, request, url_for

from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

compressor = GasCompressor()
pump = CentrifugalPump()


@app.route("/compressor")
def compressor_page():
    return render_template(
        "compressor.html",
        state=compressor.get_state(),
    )


@app.route("/start")
def start():
    compressor.start()
    return redirect(url_for("compressor_page"))


@app.route("/stop")
def stop():
    compressor.stop()
    return redirect(url_for("compressor_page"))


@app.route("/pump")
def pump_page():
    return render_template(
        "pump.html",
        state=pump.get_state(),
    )


@app.route("/api/state")
def api_state():
    return jsonify(compressor.get_state())


@app.route("/api/start", methods=["POST"])
def api_start():
    compressor.start()
    return jsonify(compressor.get_state())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    compressor.stop()
    return jsonify(compressor.get_state())


@app.route("/api/step", methods=["POST"])
def api_step():
    compressor.step()
    return jsonify(compressor.get_state())


@app.post("/api/valve")
def set_valve():
    data = request.get_json()

    compressor.set_discharge_valve_position(
        data["discharge_valve_position"]
    )

    return jsonify(compressor.get_state())


@app.post("/api/load")
def set_load():
    data = request.get_json()

    compressor.set_load_target(
        float(data["load_target"])
    )

    return jsonify(compressor.get_state())


@app.route("/api/pump/state")
def api_pump_state():
    return jsonify(pump.get_state())


@app.route("/api/pump/start", methods=["POST"])
def api_pump_start():
    pump.start()
    return jsonify(pump.get_state())


@app.route("/api/pump/stop", methods=["POST"])
def api_pump_stop():
    pump.stop()
    return jsonify(pump.get_state())


@app.route("/api/pump/step", methods=["POST"])
def api_pump_step():
    pump.step()
    return jsonify(pump.get_state())


@app.post("/api/pump/speed")
def set_pump_speed():
    data = request.get_json()

    pump.set_speed_target(
        float(data["speed_target"])
    )

    return jsonify(pump.get_state())


if __name__ == "__main__":
    app.run(debug=True)
