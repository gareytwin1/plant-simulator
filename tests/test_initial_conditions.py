"""
Named initial conditions library - T12-2.

`config/initial_conditions/*.json` are saves of the reference plant
(`scripts/initial_conditions.py` says how each was made). The build-plan tests
come first: at least four conditions exist, each loads and holds steady, and
none starts in alarm unless intended. The rest pin what each condition is for.

Two of the four are "intended alarms", and are named in `INTENDED`: a drained
cold plant reads lo_lo on V-101's level, and the pump-trip upset reads lo. Every
other condition reads clean.
"""

import json
from pathlib import Path

import pytest

from app.engine.engine import Engine
from app.engine.persistence import STATE_VERSION, StateError, capture_state, restore_state
from app.plant.loader import load_plant_file


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

CONFIG = Path(__file__).resolve().parent.parent / "config"
LIBRARY = CONFIG / "initial_conditions"
OLEFINS = CONFIG / "plants" / "olefins_lite.yaml"
GAS = CONFIG / "plants" / "gas_compression.yaml"

NAMES = ("cold_shutdown", "hot_standby", "normal_operation", "feed_pump_trip")
STEADY = ("cold_shutdown", "hot_standby", "normal_operation")
INTENDED = {
    "cold_shutdown": {"V-101.level": "lololo"},
    "feed_pump_trip": {"V-101.level": "lo"},
}

DT = 1.0
HORIZON = 300


def load(name):
    """A fresh reference plant with the named condition restored onto it."""
    engine = Engine.from_plant(load_plant_file(OLEFINS))
    restore_state(engine, json.loads((LIBRARY / f"{name}.json").read_text()))

    return engine


def vessel(engine):
    return engine.equipment["V-101"]


def bands(snapshot):
    return {key: row["band"] for key, row in snapshot.envelope.items()}


def test_the_library_holds_at_least_four_named_conditions():
    assert {path.stem for path in LIBRARY.glob("*.json")} >= set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_each_condition_loads_onto_a_fresh_plant(name):
    engine = load(name)

    assert capture_state(engine)["version"] == STATE_VERSION


@pytest.mark.parametrize("name", NAMES)
def test_restoring_a_condition_and_capturing_it_again_gives_the_file(name):
    saved = json.loads((LIBRARY / f"{name}.json").read_text())

    assert capture_state(load(name)) == saved


@pytest.mark.parametrize("name", STEADY)
def test_a_steady_condition_holds_steady(name):
    engine = load(name)
    start = (vessel(engine).level, vessel(engine).pressure)
    first = engine.step(DT)

    for _ in range(HORIZON - 1):
        last = engine.step(DT)

    assert vessel(engine).level == pytest.approx(start[0], abs=1e-4)
    assert vessel(engine).pressure == pytest.approx(start[1], abs=1e-3)
    assert last.solver["converged"]

    for tag in ("inlet_flow", "outlet_flow"):
        assert last.equipment["V-101"][tag] == pytest.approx(
            first.equipment["V-101"][tag], abs=1e-3
        )


@pytest.mark.parametrize("name", NAMES)
def test_no_condition_starts_in_alarm_unless_intended(name):
    snapshot = load(name).step(DT)

    assert bands(snapshot) == INTENDED.get(name, {})
    assert not snapshot.alarms


@pytest.mark.parametrize("name", STEADY)
def test_a_steady_condition_keeps_its_band_while_it_holds(name):
    engine = load(name)

    for _ in range(HORIZON):
        snapshot = engine.step(DT)

    assert bands(snapshot) == INTENDED.get(name, {})


def test_normal_operation_is_the_design_point():
    engine = load("normal_operation")
    snapshot = engine.step(DT)
    row = snapshot.equipment["V-101"]

    assert vessel(engine).level == pytest.approx(0.5, abs=1e-4)
    assert row["pressure"] == pytest.approx(200.0, abs=1e-2)
    assert row["inlet_flow"] == pytest.approx(50.0, abs=1e-2)
    assert row["outlet_flow"] == pytest.approx(50.0, abs=1e-2)
    assert engine.equipment["P-101"].running
    assert engine.equipment["K-101"].running


def test_cold_shutdown_has_both_machines_stopped_and_the_vessel_drained():
    engine = load("cold_shutdown")

    assert not engine.equipment["P-101"].running
    assert not engine.equipment["K-101"].running
    assert engine.equipment["P-101"].speed == pytest.approx(0.0, abs=1e-9)
    assert engine.equipment["K-101"].load == pytest.approx(0.0, abs=1e-9)
    assert vessel(engine).level == pytest.approx(0.0, abs=1e-9)


def test_hot_standby_circulates_a_small_flow_with_the_machines_running():
    engine = load("hot_standby")
    row = engine.step(DT).equipment["V-101"]

    assert engine.equipment["P-101"].running
    assert engine.equipment["K-101"].running
    assert 0.0 < row["inlet_flow"] < 0.5 * 50.0
    assert row["outlet_flow"] == pytest.approx(row["inlet_flow"], abs=1e-2)
    assert 0.2 < vessel(engine).level < 0.8


def test_the_pump_trip_upset_is_still_moving_and_getting_worse():
    engine = load("feed_pump_trip")
    levels = [vessel(engine).level]

    for _ in range(30):
        engine.step(DT)
        levels.append(vessel(engine).level)

    assert not engine.equipment["P-101"].running
    assert all(later < earlier for earlier, later in zip(levels, levels[1:]))
    assert 0.1 < levels[0] < 0.2


def test_a_condition_refuses_to_load_onto_a_different_plant():
    engine = Engine.from_plant(load_plant_file(GAS))
    saved = json.loads((LIBRARY / "normal_operation.json").read_text())

    with pytest.raises(StateError):
        restore_state(engine, saved)
