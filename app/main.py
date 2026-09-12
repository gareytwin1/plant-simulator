from flask import Flask, render_template

app = Flask(
        name,
        template_folder="../templates",
        static_folder = "../static"
      )

@app.route("/")
def home():
    return render_template("index.html")

if name == "main":
    app.run(debug=True)

