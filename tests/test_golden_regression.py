"""
Regression tests against golden traces.

These tests verify that the models' behaviour matches captured baseline traces.
If a refactor changes model output, these tests fail with the specific state
fields that changed, making the drift visible and measurable.

Every scenario is replayed through the same function that captured it, so the
only thing under test is the model. The last two tests pin the tolerance band
from both sides: a 1% change must be caught, and noise far below it must not
be.
"""

import pytest

from tests.golden_regression import (
    SCENARIOS,
    TRACE_FILES,
    compressor_ramp_load,
    compressor_rig,
    load_traces,
    pump_ramp_speed,
    pump_rig,
    replay_scenario,
    trace_path,
)


SCENARIO_IDS = [
    f"{device}-{name}"
    for device, name, _, _, _ in SCENARIOS
]


# (device, scenario name, rig factory, scenario function, attribute to drift)
INJECTION_CASES = [
    ("compressor", "ramp_load", compressor_rig, compressor_ramp_load, "shutoff_pressure_rise"),
    ("pump", "ramp_speed", pump_rig, pump_ramp_speed, "shutoff_pressure_rise"),
]

INJECTION_IDS = [
    f"{device}-{attribute}"
    for device, _, _, _, attribute in INJECTION_CASES
]


def drifted(factory, attribute, factor):
    """Build a rig whose device has one coefficient off by a known factor."""
    def build():
        rig = factory()
        device = rig.device
        setattr(device, attribute, getattr(device, attribute) * factor)

        return rig

    return build


def test_golden_trace_files_exist():
    for device in TRACE_FILES:
        assert trace_path(device).exists(), (
            f"golden trace file for {device} is missing — "
            f"capture it with `python -m tests.golden_regression`"
        )


@pytest.mark.parametrize(
    "device,name,factory,scenario_fn,steps",
    SCENARIOS,
    ids=SCENARIO_IDS,
)
def test_golden_trace_reproduces_exactly(device, name, factory, scenario_fn, steps):
    traces = load_traces(device)

    assert name in traces, f"{device} trace file has no scenario {name!r}"

    expected = traces[name]

    assert expected["steps"] == steps

    mismatches = replay_scenario(factory, scenario_fn, expected["trace"])

    assert not mismatches, (
        f"{device} {name} drifted from its golden trace:\n"
        + "\n".join(mismatches)
    )


@pytest.mark.parametrize(
    "device,name,factory,scenario_fn,attribute",
    INJECTION_CASES,
    ids=INJECTION_IDS,
)
def test_harness_catches_an_injected_one_percent_change(
    device,
    name,
    factory,
    scenario_fn,
    attribute,
):
    expected_trace = load_traces(device)[name]["trace"]

    assert not replay_scenario(factory, scenario_fn, expected_trace)

    mismatches = replay_scenario(
        drifted(factory, attribute, 1.01),
        scenario_fn,
        expected_trace,
    )

    assert mismatches, (
        f"a 1% change to {device} {attribute} went undetected — "
        f"the harness is not protecting anything"
    )

    # Failing loudly means naming where and what, not just that something moved.
    assert any(line.startswith("step ") for line in mismatches)
    assert any("flow" in line for line in mismatches)


@pytest.mark.parametrize(
    "device,name,factory,scenario_fn,attribute",
    INJECTION_CASES,
    ids=INJECTION_IDS,
)
def test_tolerances_absorb_arithmetic_noise(
    device,
    name,
    factory,
    scenario_fn,
    attribute,
):
    expected_trace = load_traces(device)[name]["trace"]

    mismatches = replay_scenario(
        drifted(factory, attribute, 1.0 + 1e-9),
        scenario_fn,
        expected_trace,
    )

    assert not mismatches, (
        f"{device} {attribute} tolerances are tight enough to flag rounding "
        f"noise, which makes the harness a false-alarm generator:\n"
        + "\n".join(mismatches)
    )
