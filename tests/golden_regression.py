"""
Golden-value regression harness.

Captures baseline traces from GasCompressor and CentrifugalPump under
standardized scenarios, each run through an Engine and the network solver
(T4-4) — the same path the live pages take. What a trace records is the
page's state row: the device's own state plus the flow and pressures the
solver put on its branch and nodes. Later refactors compare against these to detect
unintended behaviour changes.

Each scenario is deterministic: fixed timestep, fixed inputs at known steps,
no wall-clock source anywhere in the models. Traces record get_state() output
at each step for inspection and comparison.

A scenario function is the single definition of what a run does. Capture calls
it to build the trace; replay calls the same function to reproduce the trace.
Nothing re-derives the script from the recorded command descriptions, so a
scenario cannot drift away from the numbers it produced.
"""

import json
from pathlib import Path

import copy

from app.engine.sessions import COMPRESSOR_PLANT, PUMP_PLANT, Session
from app.equipment.base import Equipment


# Directory to store golden traces
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"


# Comparison tolerances.
#
# A replay recomputes the same double-precision arithmetic in the same order,
# so a faithful refactor agrees to within a few units in the last place —
# about 1e-16 relative. These bounds sit ten orders of magnitude above that
# noise floor, which leaves room for an algebraic rearrangement that is
# mathematically identical, and three orders below the 1% drift the harness
# exists to catch. They are deliberately chosen to span that gap, not read off
# a failing diff; test_harness_catches_an_injected_one_percent_change pins the
# other end of the range.
RELATIVE_TOLERANCE = 1e-5
ABSOLUTE_TOLERANCE = 1e-7


def compressor_idle(comp, step_num):
    """Machine stopped, no load."""
    return None


def compressor_ramp_load(comp, step_num):
    """Start machine, ramp load to 0.5, hold, stop."""
    if step_num == 0:
        comp.set_load_target(0.5)
        comp.start()
        return "set_load_target(0.5), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def compressor_valve_manipulation(comp, step_num):
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


def compressor_supply_pressure_change(comp, step_num):
    """Ramp load with elevated supply pressure (N-201 held at 725 psia)."""
    if step_num == 0:
        comp.set_load_target(1.0)
        comp.start()
        return "set_load_target(1.0), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def compressor_discharge_header_change(comp, step_num):
    """Ramp load with elevated discharge header (N-202 held at 775 psia)."""
    if step_num == 0:
        comp.set_load_target(1.0)
        comp.start()
        return "set_load_target(1.0), start()"
    if step_num == 15:
        comp.stop()
        return "stop()"
    return None


def pump_idle(pump, step_num):
    """Pump stopped, no speed."""
    return None


def pump_ramp_speed(pump, step_num):
    """Start pump, ramp speed to 0.5, hold, stop."""
    if step_num == 0:
        pump.set_speed_target(0.5)
        pump.start()
        return "set_speed_target(0.5), start()"
    if step_num == 15:
        pump.stop()
        return "stop()"
    return None


def pump_speed_manipulation(pump, step_num):
    """Ramp speed, then throttle back and recover."""
    if step_num == 0:
        pump.set_speed_target(0.8)
        pump.start()
        return "set_speed_target(0.8), start()"
    if step_num == 10:
        pump.set_speed_target(0.3)
        return "set_speed_target(0.3)"
    if step_num == 20:
        pump.set_speed_target(0.9)
        return "set_speed_target(0.9)"
    if step_num == 30:
        pump.stop()
        return "stop()"
    return None


def pump_supply_pressure_change(pump, step_num):
    """Ramp speed with elevated supply pressure (N-101 held at 60 psia)."""
    if step_num == 0:
        pump.set_speed_target(1.0)
        pump.start()
        return "set_speed_target(1.0), start()"
    if step_num == 15:
        pump.stop()
        return "stop()"
    return None


def pump_discharge_header_change(pump, step_num):
    """Ramp speed against a header the pump cannot beat until part way up.

    At 50 psia of shutoff rise the pump is dead-headed below roughly 0.58
    speed, so this locks the crossing into and out of the zero-flow branch
    (N-102 held at 75 psia).
    """
    if step_num == 0:
        pump.set_speed_target(1.0)
        pump.start()
        return "set_speed_target(1.0), start()"
    if step_num == 15:
        pump.stop()
        return "stop()"
    return None


class Rig:
    """One machine on an Engine, driven and read the way the live page is."""

    device: Equipment

    def __init__(
        self,
        kind: str,
        suction: float | None = None,
        discharge: float | None = None,
    ) -> None:
        base = COMPRESSOR_PLANT if kind == "compressor" else PUMP_PLANT
        plant = copy.deepcopy(base)

        for node, pressure in zip(plant["nodes"], (suction, discharge)):
            if pressure is not None:
                node["pressure"] = pressure

        if kind == "compressor":
            session = Session(compressor_plant=plant)
            self.device = session.compressor
            self.step = session.step_compressor
            self.get_state = session.compressor_state
        else:
            session = Session(pump_plant=plant)
            self.device = session.pump
            self.step = session.step_pump
            self.get_state = session.pump_state


def compressor_rig() -> Rig:
    return Rig("compressor")


def compressor_supply_rig() -> Rig:
    return Rig("compressor", suction=725.0)


def compressor_header_rig() -> Rig:
    return Rig("compressor", discharge=775.0)


def pump_rig() -> Rig:
    return Rig("pump")


def pump_supply_rig() -> Rig:
    return Rig("pump", suction=60.0)


def pump_header_rig() -> Rig:
    return Rig("pump", discharge=75.0)


# (device, name, rig factory, scenario function, steps)
SCENARIOS = [
    ("compressor", "idle", compressor_rig, compressor_idle, 5),
    ("compressor", "ramp_load", compressor_rig, compressor_ramp_load, 20),
    ("compressor", "valve_manipulation", compressor_rig, compressor_valve_manipulation, 35),
    ("compressor", "supply_pressure_change", compressor_supply_rig, compressor_supply_pressure_change, 20),
    ("compressor", "discharge_header_change", compressor_header_rig, compressor_discharge_header_change, 20),
    ("pump", "idle", pump_rig, pump_idle, 5),
    ("pump", "ramp_speed", pump_rig, pump_ramp_speed, 20),
    ("pump", "speed_manipulation", pump_rig, pump_speed_manipulation, 35),
    ("pump", "supply_pressure_change", pump_supply_rig, pump_supply_pressure_change, 20),
    ("pump", "discharge_header_change", pump_header_rig, pump_discharge_header_change, 20),
]

TRACE_FILES = {
    "compressor": "compressor_traces.json",
    "pump": "pump_traces.json",
}


def trace_path(device):
    return GOLDEN_DIR / TRACE_FILES[device]


def load_traces(device):
    with open(trace_path(device)) as f:
        return json.load(f)


def capture_scenario(name, factory, scenario_fn, steps):
    """
    Run a scenario and capture state traces.

    scenario_fn(device, step_num) is called at each step to apply inputs and
    return a description of what was commanded at that step. The rig owns the
    Engine the step runs through; the scenario only ever touches the device.
    Returns a trace dict: {name, steps, trace: [state dicts], commands: []}
    """
    rig = factory()
    trace = []
    commands = []

    for step_num in range(steps):
        state = rig.get_state()
        trace.append(state)
        cmd = scenario_fn(rig.device, step_num)
        if cmd:
            commands.append({"step": step_num, "command": cmd})
        rig.step()

    # Capture final state after last step
    trace.append(rig.get_state())

    return {
        "name": name,
        "steps": steps,
        "trace": trace,
        "commands": commands,
    }


def compare_states(actual, expected, rel=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE):
    """
    Compare one captured state against one replayed state.

    Returns a list of human-readable field diffs, empty when they match.
    """
    mismatches = []

    for key in expected:
        if key not in actual:
            mismatches.append(f"  {key}: missing in actual")
            continue

        exp_val = expected[key]
        act_val = actual[key]

        if isinstance(exp_val, bool):
            if act_val != exp_val:
                mismatches.append(f"  {key}: {act_val} != {exp_val}")
        elif isinstance(exp_val, float):
            if abs(act_val - exp_val) > abs_tol + rel * abs(exp_val):
                mismatches.append(
                    f"  {key}: {act_val} != {exp_val} "
                    f"(Δ {act_val - exp_val:+.2e})"
                )
        else:
            if act_val != exp_val:
                mismatches.append(f"  {key}: {act_val} != {exp_val}")

    return mismatches


def replay_scenario(factory, scenario_fn, expected_trace):
    """
    Re-run a scenario against the current model and diff it with the trace.

    Drives the run from the same scenario function that captured it, so the
    only thing under test is the model. Returns every field diff found across
    the whole run, empty when the model reproduces the trace.
    """
    rig = factory()
    mismatches = []

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        mismatches.extend(
            f"step {step_num}:{diff}"
            for diff in compare_states(rig.get_state(), expected_state)
        )

        scenario_fn(rig.device, step_num)
        rig.step()

    mismatches.extend(
        f"final:{diff}"
        for diff in compare_states(rig.get_state(), expected_trace[-1])
    )

    return mismatches


def capture_all_golden_traces():
    """Capture all golden traces and write one file per device."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    for device, name, factory, scenario_fn, steps in SCENARIOS:
        trace = capture_scenario(name, factory, scenario_fn, steps)
        results.setdefault(device, {})[name] = trace
        print(f"✓ {device} {name}: {steps} steps, {len(trace['trace'])} states captured")

    for device, traces in results.items():
        output_file = trace_path(device)
        with open(output_file, "w") as f:
            json.dump(traces, f, indent=2)

        print(f"\n✓ Golden traces written to {output_file}")

    return results


if __name__ == "__main__":
    capture_all_golden_traces()
