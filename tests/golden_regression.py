"""
Golden-value regression harness.

Captures baseline traces from GasCompressor under standardized scenarios.
Later refactors compare against these to detect unintended behaviour changes.

Each scenario is deterministic: fixed seed, fixed timestep, fixed inputs at
known steps. Traces record get_state() output at each step for inspection and
comparison.
"""

import json
from pathlib import Path

from app.equipment.compressor import GasCompressor


# Directory to store golden traces
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"


def capture_scenario(name, scenario_fn, steps):
    """
    Run a scenario and capture state traces.

    scenario_fn(compressor, step_num) is called at each step to apply
    inputs and return a description of what was commanded at that step.
    Returns a trace dict: {name, description, trace: [state dicts], commands: []}
    """
    comp = GasCompressor()
    trace = []
    commands = []

    for step_num in range(steps):
        state = comp.get_state()
        trace.append(state)
        cmd = scenario_fn(comp, step_num)
        if cmd:
            commands.append({"step": step_num, "command": cmd})
        comp.step()

    # Capture final state after last step
    trace.append(comp.get_state())

    return {
        "name": name,
        "steps": steps,
        "trace": trace,
        "commands": commands,
    }


def scenario_idle(comp, step_num):
    """Machine stopped, no load."""
    return None


def scenario_ramp_load(comp, step_num):
    """Start machine, ramp load to 0.5, hold, stop."""
    if step_num == 0:
        comp.set_load_target(0.5)
        comp.start()
        return "set_load_target(0.5), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def scenario_valve_manipulation(comp, step_num):
    """Ramp load, then manipulate discharge valve."""
    if step_num == 0:
        comp.set_load_target(0.8)
        comp.start()
        return "set_load_target(0.8), start()"
    if step_num == 10:
        comp.set_discharge_valve_position(0.5)
        return "set_discharge_valve_position(0.5)"
    if step_num == 20:
        comp.set_discharge_valve_position(0.1)
        return "set_discharge_valve_position(0.1)"
    if step_num == 30:
        comp.stop()
        return "stop()"
    return None


def scenario_supply_pressure_change(comp, step_num):
    """Ramp load with elevated supply pressure."""
    if step_num == 0:
        comp.supply_pressure = 725.0
        comp.set_load_target(1.0)
        comp.start()
        return "supply_pressure = 725.0, set_load_target(1.0), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def scenario_discharge_header_change(comp, step_num):
    """Ramp load with elevated discharge header."""
    if step_num == 0:
        comp.discharge_header_pressure = 775.0
        comp.set_load_target(1.0)
        comp.start()
        return "discharge_header_pressure = 775.0, set_load_target(1.0), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def scenario_downstream_restriction(comp, step_num):
    """Ramp load with downstream restriction."""
    if step_num == 0:
        comp.downstream_restriction = 0.01
        comp.set_load_target(1.0)
        comp.start()
        return "downstream_restriction = 0.01, set_load_target(1.0), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def capture_all_golden_traces():
    """Capture all golden traces and write to fixtures."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = [
        ("idle", scenario_idle, 5),
        ("ramp_load", scenario_ramp_load, 20),
        ("valve_manipulation", scenario_valve_manipulation, 35),
        ("supply_pressure_change", scenario_supply_pressure_change, 20),
        ("discharge_header_change", scenario_discharge_header_change, 20),
        ("downstream_restriction", scenario_downstream_restriction, 20),
    ]

    results = {}
    for name, scenario_fn, steps in scenarios:
        trace = capture_scenario(name, scenario_fn, steps)
        results[name] = trace
        print(f"✓ {name}: {steps} steps, {len(trace['trace'])} states captured")

    # Write all traces as one JSON file for easy comparison
    output_file = GOLDEN_DIR / "compressor_traces.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Golden traces written to {output_file}")
    return results


if __name__ == "__main__":
    capture_all_golden_traces()
