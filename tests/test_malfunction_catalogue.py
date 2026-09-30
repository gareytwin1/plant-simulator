"""
Malfunction catalogue - T13-4.

`config/malfunctions/*.yaml` is the fault library: one file per fault, each
carrying a scenario-ready `malfunction` entry, the response it must produce
and the symptoms an operator would see. The build-plan tests are the ones that
run each fault through the solver: every `expected_response` direction is
checked against an unfaulted twin run under the same operator actions, fouling
raises the temperature downstream, and instrument drift moves the indication
while the true value stays exactly where the twin has it.
"""

import json
import warnings
from pathlib import Path

import pytest
import yaml

from app.disturbances.malfunction import Malfunction, MalfunctionRegistry
from app.engine.engine import Engine
from app.engine.instruments import Instrument
from app.equipment.registry import EquipmentRegistry
from app.plant.loader import load_plant, load_plant_file
from app.plant.validate import validate
from app.scenarios.runner import malfunction_from_config

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "config" / "malfunctions"
PLANTS = ROOT / "config" / "plants"

SCENARIO_SCHEMA = json.loads((ROOT / "config" / "schema" / "scenario.schema.json").read_text())
MALFUNCTION_SCHEMA = SCENARIO_SCHEMA["properties"]["malfunctions"]["items"]

EXPECTED_IDS = {
    "compressor_trip",
    "pump_trip",
    "loss_of_feed",
    "fouled_exchanger",
    "restricted_line",
    "stuck_valve",
    "cooling_loss",
    "instrument_drift",
}

SETTLE_STEPS = 200
RESPONSE_STEPS = 200
STEP = 1.0

# A margin on "changed", so a direction is never read out of solver noise.
MARGIN = 1e-3

# The catalogue names `plant: null` where no shipped plant carries the device.
# The exchanger faults run on this one: a pump feeding a cooler whose outlet
# node is internal, because a boundary node never takes the temperature
# arriving at it.
COOLER_TRAIN = {
    "nodes": [
        {"id": "N-101", "boundary": True, "pressure": 50.0, "domain": "liquid"},
        {"id": "N-102", "boundary": False, "pressure": 50.0, "domain": "liquid"},
        {"id": "N-103", "boundary": False, "pressure": 50.0, "domain": "liquid"},
        {"id": "N-104", "boundary": True, "pressure": 100.0, "domain": "liquid"},
    ],
    "equipment": [
        {"tag": "P-101", "type": "pump", "node_in": "N-101", "node_out": "N-102", "design": {"speed": 1.0}},
        {"tag": "E-101", "type": "heat_exchanger", "node_in": "N-102", "node_out": "N-103", "design": {}},
        {"tag": "FV-101", "type": "control_valve", "node_in": "N-103", "node_out": "N-104", "design": {}},
    ],
}
FEED_TEMPERATURE = 200.0


def load_catalogue():
    return {
        path.stem: yaml.safe_load(path.read_text())
        for path in sorted(CATALOGUE.glob("*.yaml"))
    }


ENTRIES = load_catalogue()


def start_machines(plant):
    for device in plant.devices.values():
        if hasattr(device, "set_speed_target"):
            device.set_speed_target(1.0)
            device.start()
        if hasattr(device, "set_load_target"):
            device.set_load_target(1.0)
            device.start()


def build(entry):
    """A running engine for `entry`'s plant, and the registry that faults it."""
    instruments = []

    if entry["plant"] is None:
        plant = load_plant(COOLER_TRAIN)
        boundary_temperatures = {"N-101": FEED_TEMPERATURE}
    else:
        plant = load_plant_file(PLANTS / f"{entry['plant']}.yaml")
        boundary_temperatures = None

    if entry["malfunction"]["target_tag"] == "LT-101":
        instruments.append(Instrument("LT-101", "equipment", "V-101", "level"))

    start_machines(plant)

    with warnings.catch_warnings():
        # olefins_lite names limits no device publishes yet; unrelated here.
        warnings.simplefilter("ignore", UserWarning)
        engine = Engine.from_plant(
            plant,
            boundary_temperatures=boundary_temperatures,
            instruments=instruments,
        )

    if "E-101" in engine.equipment:
        # Nothing in a running plant writes an exchanger's inlet temperature
        # yet (exchanger.py, "Known interim limitation"), so the caller does.
        engine.equipment["E-101"].inlet_temperature = FEED_TEMPERATURE

    equipment = EquipmentRegistry()
    for device in engine.equipment.values():
        equipment.register(device)

    return engine, MalfunctionRegistry(equipment, engine.instruments.values())


def run(entry, faulted):
    engine, malfunctions = build(entry)

    if faulted:
        malfunctions.add(malfunction_from_config(entry["malfunction"]))

    for _ in range(SETTLE_STEPS):
        engine.step(STEP)

    malfunctions.update(engine.snapshot())

    for action in entry.get("operator_actions", []):
        getattr(engine.equipment[action["device"]], action["call"])(*action["args"])

    for _ in range(RESPONSE_STEPS):
        snapshot = engine.step(STEP)

    return snapshot


def read(snapshot, check):
    view = snapshot if check.get("view", "indicated") == "indicated" else snapshot.truth

    return getattr(view, check["section"])[check["source"]][check["variable"]]


def expected_responses():
    return [
        pytest.param(entry, check, id=f"{name}-{check['source']}-{check['variable']}-{check['direction']}")
        for name, entry in ENTRIES.items()
        for check in entry["expected_response"]
    ]


# ---- every catalogued fault produces the expected response direction ------


@pytest.mark.parametrize(("entry", "check"), expected_responses())
def test_a_fault_moves_its_expected_variable_the_expected_way(entry, check):
    healthy = read(run(entry, faulted=False), check)
    faulted = read(run(entry, faulted=True), check)
    margin = MARGIN * max(1.0, abs(healthy))

    if check["direction"] == "rises":
        assert faulted > healthy + margin
    elif check["direction"] == "falls":
        assert faulted < healthy - margin
    else:
        assert check["direction"] == "unchanged"
        assert faulted == pytest.approx(healthy, abs=margin)


# ---- the named build-plan behaviours, stated on their own -----------------


def test_fouling_raises_the_temperature_downstream():
    entry = ENTRIES["fouled_exchanger"]
    check = {"section": "nodes", "source": "N-103", "variable": "temperature"}

    healthy = read(run(entry, faulted=False), check)
    fouled = read(run(entry, faulted=True), check)

    # The cooler really cools when healthy, so the rise is not a pass-through.
    assert healthy < FEED_TEMPERATURE - 5.0
    assert fouled > healthy + 5.0
    assert fouled <= FEED_TEMPERATURE


def test_instrument_drift_does_not_change_the_true_process_value():
    entry = ENTRIES["instrument_drift"]
    healthy = run(entry, faulted=False)
    drifted = run(entry, faulted=True)

    assert drifted.truth.equipment["V-101"]["level"] == healthy.truth.equipment["V-101"]["level"]
    assert drifted.equipment["V-101"]["level"] == pytest.approx(
        healthy.equipment["V-101"]["level"] + entry["malfunction"]["value"],
    )
    assert drifted.streams == healthy.streams


def test_loss_of_feed_starves_the_pump_without_reversing_it():
    entry = ENTRIES["loss_of_feed"]
    flow = {"section": "streams", "source": "B-P-101", "variable": "flow"}

    starved = read(run(entry, faulted=True), flow)
    healthy = read(run(entry, faulted=False), flow)

    # Backflow would hide inside a "flow falls" check, which is what separates
    # this fault from pump_trip.
    assert 0.0 < starved < 0.2 * healthy


def test_a_pump_trip_reverses_the_pump_where_loss_of_feed_does_not():
    flow = {"section": "streams", "source": "B-P-101", "variable": "flow"}

    assert read(run(ENTRIES["pump_trip"], faulted=True), flow) < 0.0


def test_a_stuck_valve_only_matters_once_the_operator_acts_on_it():
    entry = {**ENTRIES["stuck_valve"], "operator_actions": []}
    flow = {"section": "streams", "source": "B-LV-101", "variable": "flow"}

    assert read(run(entry, faulted=True), flow) == pytest.approx(
        read(run(entry, faulted=False), flow), rel=1e-6,
    )


# ---- the catalogue's own shape --------------------------------------------


def test_the_catalogue_holds_exactly_the_documented_faults():
    assert set(ENTRIES) == EXPECTED_IDS


@pytest.mark.parametrize("name", ENTRIES)
def test_an_entry_is_named_for_its_file_and_documents_its_symptoms(name):
    entry = ENTRIES[name]

    assert entry["id"] == name
    assert entry["name"].strip()
    assert entry["summary"].strip()
    assert entry["diagnosis"].strip()
    assert entry["symptoms"]
    assert all(symptom.strip() for symptom in entry["symptoms"])
    assert entry["expected_response"]


@pytest.mark.parametrize("name", ENTRIES)
def test_an_entry_names_a_plant_or_says_what_it_requires(name):
    entry = ENTRIES[name]

    if entry["plant"] is None:
        assert entry["requires"].strip()
    else:
        assert (PLANTS / f"{entry['plant']}.yaml").is_file()


@pytest.mark.parametrize("name", ENTRIES)
def test_an_entry_malfunction_is_a_valid_scenario_malfunction(name):
    config = ENTRIES[name]["malfunction"]

    assert validate(config, MALFUNCTION_SCHEMA) == []
    assert isinstance(malfunction_from_config(config), Malfunction)


@pytest.mark.parametrize("name", ENTRIES)
def test_an_entry_is_accepted_by_the_registry_before_onset(name):
    entry = ENTRIES[name]
    _, malfunctions = build(entry)

    malfunctions.add(malfunction_from_config(entry["malfunction"]))

    assert len(malfunctions.pending) == 1


def test_no_two_entries_write_the_same_parameter():
    keys = [
        (entry["malfunction"]["target_tag"], entry["malfunction"]["parameter"])
        for entry in ENTRIES.values()
    ]

    assert len(keys) == len(set(keys))
