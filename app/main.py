from flask import Flask, jsonify, redirect, render_template, url_for

from .simulator import PlantSimulator


app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
)

simulator = PlantSimulator()


@app.route("/")
def home():
    return render_template("index.html", state=simulator.get_state())


@app.route("/start")
def start():
    simulator.start()
    return redirect(url_for("home"))


@app.route("/stop")
def stop():
    simulator.stop()
    return redirect(url_for("home"))


@app.route("/api/state")
def api_state():
    return jsonify(simulator.get_state())


@app.route("/api/start", methods=["POST"])
def api_start():
    simulator.start()
    return jsonify(simulator.get_state())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    simulator.stop()
    return jsonify(simulator.get_state())


@app.route("/api/step", methods=["POST"])
def api_step():
    simulator.step()
    return jsonify(simulator.get_state())


if __name__ == "__main__":
    app.run(debug=True)
