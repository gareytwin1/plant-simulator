"""
Regression tests against golden traces.

These tests verify that the model's behaviour matches captured baseline traces.
If a refactor changes model output, these tests will fail with the specific
state fields that changed, making the drift visible and measurable.
"""

import json
from pathlib import Path

import pytest

from app.equipment.compressor import GasCompressor


GOLDEN_FILE = Path(__file__).parent / "fixtures" / "golden" / "compressor_traces.json"


@pytest.fixture
def golden_traces():
    """Load golden traces from fixture."""
    if not GOLDEN_FILE.exists():
        pytest.skip(f"Golden traces not found at {GOLDEN_FILE}")
    with open(GOLDEN_FILE) as f:
        return json.load(f)


def approx_state_match(actual, expected, rel=1e-5, abs_tol=1e-7):
    """
    Check if actual state matches expected state within tolerances.

    Returns (match: bool, mismatches: list of field diffs).
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
            # Use approx comparison for floats
            if abs(act_val - exp_val) > abs_tol + rel * abs(exp_val):
                mismatches.append(
                    f"  {key}: {act_val} != {exp_val} "
                    f"(Δ {act_val - exp_val:+.2e})"
                )
        else:
            if act_val != exp_val:
                mismatches.append(f"  {key}: {act_val} != {exp_val}")

    return len(mismatches) == 0, mismatches


def test_golden_idle(golden_traces):
    """Verify idle (no load) scenario."""
    expected_trace = golden_traces["idle"]["trace"]
    comp = GasCompressor()

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        actual_state = comp.get_state()
        match, diffs = approx_state_match(actual_state, expected_state)
        assert match, (
            f"Idle step {step_num} mismatch:\n"
            + "\n".join(diffs)
        )
        comp.step()

    # Final state
    actual_state = comp.get_state()
    expected_state = expected_trace[-1]
    match, diffs = approx_state_match(actual_state, expected_state)
    assert match, (
        f"Idle final state mismatch:\n" + "\n".join(diffs)
    )


def test_golden_ramp_load(golden_traces):
    """Verify load ramp scenario."""
    expected_trace = golden_traces["ramp_load"]["trace"]
    expected_commands = golden_traces["ramp_load"]["commands"]

    comp = GasCompressor()
    cmd_idx = 0

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        actual_state = comp.get_state()
        match, diffs = approx_state_match(actual_state, expected_state)
        assert match, (
            f"Ramp load step {step_num} mismatch:\n"
            + "\n".join(diffs)
        )

        # Apply commands for this step (after checking state, before stepping)
        if cmd_idx < len(expected_commands):
            cmd = expected_commands[cmd_idx]
            if cmd["step"] == step_num:
                if "set_load_target(0.5)" in cmd["command"]:
                    comp.set_load_target(0.5)
                if "start()" in cmd["command"]:
                    comp.start()
                if "stop()" in cmd["command"]:
                    comp.stop()
                cmd_idx += 1

        comp.step()

    # Final state
    actual_state = comp.get_state()
    expected_state = expected_trace[-1]
    match, diffs = approx_state_match(actual_state, expected_state)
    assert match, (
        f"Ramp load final state mismatch:\n" + "\n".join(diffs)
    )


def test_golden_valve_manipulation(golden_traces):
    """Verify discharge valve manipulation scenario."""
    expected_trace = golden_traces["valve_manipulation"]["trace"]
    expected_commands = golden_traces["valve_manipulation"]["commands"]

    comp = GasCompressor()
    cmd_idx = 0

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        actual_state = comp.get_state()
        match, diffs = approx_state_match(actual_state, expected_state)
        assert match, (
            f"Valve manipulation step {step_num} mismatch:\n"
            + "\n".join(diffs)
        )

        # Apply commands for this step (after checking state, before stepping)
        if cmd_idx < len(expected_commands):
            cmd = expected_commands[cmd_idx]
            if cmd["step"] == step_num:
                if "set_load_target(0.8)" in cmd["command"]:
                    comp.set_load_target(0.8)
                if "start()" in cmd["command"]:
                    comp.start()
                if "set_discharge_valve_position(0.5)" in cmd["command"]:
                    comp.set_discharge_valve_position(0.5)
                if "set_discharge_valve_position(0.1)" in cmd["command"]:
                    comp.set_discharge_valve_position(0.1)
                if "stop()" in cmd["command"]:
                    comp.stop()
                cmd_idx += 1

        comp.step()

    # Final state
    actual_state = comp.get_state()
    expected_state = expected_trace[-1]
    match, diffs = approx_state_match(actual_state, expected_state)
    assert match, (
        f"Valve manipulation final state mismatch:\n" + "\n".join(diffs)
    )


def test_golden_supply_pressure_change(golden_traces):
    """Verify scenario with altered supply pressure."""
    expected_trace = golden_traces["supply_pressure_change"]["trace"]
    expected_commands = golden_traces["supply_pressure_change"]["commands"]

    comp = GasCompressor()
    cmd_idx = 0

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        actual_state = comp.get_state()
        match, diffs = approx_state_match(actual_state, expected_state)
        assert match, (
            f"Supply pressure step {step_num} mismatch:\n"
            + "\n".join(diffs)
        )

        # Apply commands for this step (after checking state, before stepping)
        if cmd_idx < len(expected_commands):
            cmd = expected_commands[cmd_idx]
            if cmd["step"] == step_num:
                if "supply_pressure = 725.0" in cmd["command"]:
                    comp.supply_pressure = 725.0
                if "set_load_target(1.0)" in cmd["command"]:
                    comp.set_load_target(1.0)
                if "start()" in cmd["command"]:
                    comp.start()
                if "stop()" in cmd["command"]:
                    comp.stop()
                cmd_idx += 1

        comp.step()

    # Final state
    actual_state = comp.get_state()
    expected_state = expected_trace[-1]
    match, diffs = approx_state_match(actual_state, expected_state)
    assert match, (
        f"Supply pressure final state mismatch:\n" + "\n".join(diffs)
    )


def test_golden_discharge_header_change(golden_traces):
    """Verify scenario with altered discharge header pressure."""
    expected_trace = golden_traces["discharge_header_change"]["trace"]
    expected_commands = golden_traces["discharge_header_change"]["commands"]

    comp = GasCompressor()
    cmd_idx = 0

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        actual_state = comp.get_state()
        match, diffs = approx_state_match(actual_state, expected_state)
        assert match, (
            f"Discharge header step {step_num} mismatch:\n"
            + "\n".join(diffs)
        )

        # Apply commands for this step (after checking state, before stepping)
        if cmd_idx < len(expected_commands):
            cmd = expected_commands[cmd_idx]
            if cmd["step"] == step_num:
                if "discharge_header_pressure = 775.0" in cmd["command"]:
                    comp.discharge_header_pressure = 775.0
                if "set_load_target(1.0)" in cmd["command"]:
                    comp.set_load_target(1.0)
                if "start()" in cmd["command"]:
                    comp.start()
                if "stop()" in cmd["command"]:
                    comp.stop()
                cmd_idx += 1

        comp.step()

    # Final state
    actual_state = comp.get_state()
    expected_state = expected_trace[-1]
    match, diffs = approx_state_match(actual_state, expected_state)
    assert match, (
        f"Discharge header final state mismatch:\n" + "\n".join(diffs)
    )


def test_golden_downstream_restriction(golden_traces):
    """Verify scenario with downstream restriction."""
    expected_trace = golden_traces["downstream_restriction"]["trace"]
    expected_commands = golden_traces["downstream_restriction"]["commands"]

    comp = GasCompressor()
    cmd_idx = 0

    for step_num, expected_state in enumerate(expected_trace[:-1]):
        actual_state = comp.get_state()
        match, diffs = approx_state_match(actual_state, expected_state)
        assert match, (
            f"Downstream restriction step {step_num} mismatch:\n"
            + "\n".join(diffs)
        )

        # Apply commands for this step (after checking state, before stepping)
        if cmd_idx < len(expected_commands):
            cmd = expected_commands[cmd_idx]
            if cmd["step"] == step_num:
                if "downstream_restriction = 0.01" in cmd["command"]:
                    comp.downstream_restriction = 0.01
                if "set_load_target(1.0)" in cmd["command"]:
                    comp.set_load_target(1.0)
                if "start()" in cmd["command"]:
                    comp.start()
                if "stop()" in cmd["command"]:
                    comp.stop()
                cmd_idx += 1

        comp.step()

    # Final state
    actual_state = comp.get_state()
    expected_state = expected_trace[-1]
    match, diffs = approx_state_match(actual_state, expected_state)
    assert match, (
        f"Downstream restriction final state mismatch:\n" + "\n".join(diffs)
    )
