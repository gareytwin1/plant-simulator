# Plant Simulator

Plant Simulator is a Flask-based engineering simulation project for modeling plant equipment and operating behavior.

## Current Equipment

- Gas Compressor

## Features

- Start and stop equipment
- Adjustable operating load
- Simulated pressure, temperature, and flow behavior
- Flask API endpoints for simulator control and state
- Browser-based controls and displays
- Automated tests with pytest

## Project Structure

```text
plant-simulator/
├── app/
│   ├── main.py
│   └── equipment/
├── static/
├── templates/
├── tests/
├── README.md
└── requirements.txt
```

## Run the Application

Activate the project environment, then run:

```bash
flask --app app.main run
```

Open the application in your browser and navigate to the equipment page you want to simulate.

## Run Tests

```bash
python -m pytest
```

Current milestone: 26 tests passing.

## Goal

Build a simple, modular plant simulator that can be expanded with additional equipment models and engineering logic over time.
