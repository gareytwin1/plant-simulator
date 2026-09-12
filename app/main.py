from flask import Flask, redirect, render_template, url_for

from simulator import PlantSimulator

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

simulator = PlantSimulator()


@app.route("/")
def home():
    return render_template("index.html", state=simulator.get_state())


@app.route("/start", methods=["POST"])
def start():
    simulator.start()
    return redirect(url_for("home"))


@app.route("/stop", methods=["POST"])
def stop():
    simulator.stop()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
