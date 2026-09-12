
# Plant Simulator

Plant Simulator is a web-based process simulation application built with Python, Flask, 
HTML, CSS, and JavaScript.

The project is being developed incrementally. The goal is to keep the application 
structure simple while building a clear separation between the web interface and the 
plant simulation logic.

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

`PlantSimulator` provides the interface between the web application and the underlying 
plant model.

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

This separation allows the simulation model to be tested independently from the web 
application.

## Current Simulator State

The initial simulator tracks:

* Running status
* Pressure
* Temperature
* Flow

The current controls allow the user to:

* Start the simulator
* Stop the simulator

These values are intentionally simple while the application architecture is being 
established.

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

## Development Status

Plant Simulator is currently in the early development stage.

The current focus is establishing a clean application architecture before adding 
detailed plant equipment and engineering behavior.
