# Plant Simulator

Plant Simulator is a web-based process simulation application built with Python, Flask, HTML, CSS, and JavaScript.

The project models individual pieces of process equipment using simple engineering relationships and interactive operator controls.

## Architecture

```text
Browser
   ↓
HTML / CSS / JavaScript
   ↓
Flask Controller
   ↓
Equipment Simulator
   ↓
Equipment Physics and Process State
```

Engineering logic remains separate from Flask and browser code.

## Current Equipment

### Gas Compressor

The first equipment model includes:

* Start and stop controls
* Compressor load target and actual load
* Discharge valve target and actual position
* Valve actuator timing
* Suction and discharge pressure
* Flow
* Pressure spread
* Discharge temperature
* Compressor and system resistance relationships
* Variable upstream and downstream pressures
* Valve pressure drop
* Live pressure trend
* Configurable simulation timing

The compressor is maintained as its own equipment module.

