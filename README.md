
# Plant Simulator

Plant Simulator is a web-based process simulation application built with Python, Flask, HTML, CSS, and JavaScript.

The project is being developed incrementally. The goal is to keep the application structure simple while building a clear separation between the web interface and the plant simulation logic.

## Current Goals

The initial version will focus on:

* Building a simple web-based plant interface
* Creating a reusable plant simulation engine
* Modeling plant state such as pressure, temperature, and flow
* Adding basic plant start and stop controls
* Keeping engineering calculations separate from the web application
* Expanding equipment and process behavior as the simulator develops

## Application Architecture

The application follows a simple layered architecture:

```text
Browser
   ↓
HTML / CSS / JavaScript
   ↓
Flask Controller
   ↓
PlantSimulator
   ↓
Plant State and Engineering Logic
```

### Browser

The browser provides the interface for the simulator.

### HTML / CSS / JavaScript

The presentation layer handles:

* Page structure
* Styling
* User controls
* Display updates
* Communication with Flask

### Flask Controller

Flask connects the browser to the simulation engine.

Its responsibilities include:

* Web routes
* User requests
* Input validation
* Calling simulator methods
* Returning data or pages to the browser

Plant calculations should not be placed inside Flask routes.

### PlantSimulator

`PlantSimulator` provides the interface between the web application and the underlying plant model.

It is responsible for controlling simulation behavior such as:

* Starting the plant
* Stopping the plant
* Updating plant state
* Returning current simulation values

The simulator should remain independent of Flask and the browser.

### Plant State and Engineering Logic

This layer represents the physical plant and its behavior.

It will eventually contain items such as:

* Pressure
* Temperature
* Flow
* Equipment status
* Compressors
* Pumps
* Valves
* Controllers
* Alarms
* Process calculations

Engineering logic should not depend on Flask, HTML, CSS, or JavaScript.

This separation allows the simulation model to be tested independently from the web application.

## Current Project Structure

```text
plant-simulator/
├── app/
│   ├── main.py
│   └── simulator.py
├── static/
│   ├── app.js
│   └── style.css
├── templates/
│   └── index.html
├── tests/
├── .gitignore
├── README.md
├── requirements.txt
├── package.json
└── package-lock.json
```

The folder structure will remain small while the project is in its early stages.

New folders and modules should only be added when the application becomes large enough to justify them.

## File Responsibilities

### `app/main.py`

Contains the Flask application and web routes.

This file acts as the controller between the browser and the simulator.

### `app/simulator.py`

Contains the `PlantSimulator` class and simulation behavior.

This file currently represents the main simulation model.

### `templates/index.html`

Contains the main HTML interface.

### `static/style.css`

Contains application styling.

### `static/app.js`

Contains browser-side JavaScript.

This file will eventually handle live updates and communication with Flask API routes.

### `tests/`

Contains automated tests for the simulator and application.

## Development Environment

The project uses Conda for Python environment management.

Create the environment:

```bash
conda create -n plant-simulator python=3.12
```

Activate it:

```bash
conda activate plant-simulator
```

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

The project also uses local Node.js dependencies for JavaScript development tooling.

Install them with:

```bash
npm install
```

## Running the Application

Activate the Conda environment:

```bash
conda activate plant-simulator
```

Start the Flask application:

```bash
python app/main.py
```

Flask should start the development server at:

```text
http://127.0.0.1:5000
```

Open that address in a browser.

## Current Simulator State

The initial simulator tracks:

* Running status
* Pressure
* Temperature
* Flow

The current controls allow the user to:

* Start the simulator
* Stop the simulator

These values are intentionally simple while the application architecture is being established.

## Design Principles

The project will follow a few basic design rules.

1. Keep the folder structure simple until additional structure is needed.
2. Keep engineering calculations outside Flask routes.
3. Keep the simulation engine independent of the browser.
4. Use `PlantSimulator` as the interface between the web application and plant logic.
5. Keep plant state organized and predictable.
6. Add equipment models only when they are needed.
7. Test simulation behavior independently from the user interface.
8. Prefer small, understandable modules over premature complexity.

## Planned Development

Near-term development will likely include:

* Continuous plant state updates
* JSON API routes
* Browser-side state updates using JavaScript
* Additional plant controls
* Equipment models
* Process calculations
* Alarms and status indicators
* Historical values and trends
* Automated simulator tests

The project structure will expand as these features are introduced.

## Version Control

The project uses Git and GitHub for version control.

Remote repository:

```text
git@github.com:gareytwin1/plant-simulator.git
```

Typical workflow:

```bash
git status
git add .
git commit -m "Describe changes"
git push
```

## Development Status

Plant Simulator is currently in the early development stage.

The current focus is establishing a clean application architecture before adding detailed plant equipment and engineering behavior.
