from flask import Flask, jsonify, redirect, render_template, request, url_for

from app.equipment.compressor import GasCompressor


app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

compressor = GasCompressor()


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


if __name__ == "__main__":
    app.run(debug=True)
