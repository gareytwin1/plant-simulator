"""Point units (T20-1): every trend point's unit comes from the plant, never
from a field or domain name."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.visibility import VISIBLE
from app.engine.engine import Engine
from app.plant.loader import load_plant, load_plant_file
from app.plant.units import FIELD_UNITS, FRACTION, domain_units
from app.training.runtime import PlantRuntime
from app.training.session import TrainingSession


def runtime_of(config):
    plant = load_plant(config)

    return PlantRuntime(Engine.from_plant(plant), plant)


@pytest.fixture(scope="module")
def free_play():
    session = TrainingSession()

    yield session.trend_units(), session.trend_points()

    session.end()


def test_every_trend_point_of_the_reference_plant_has_a_unit(free_play):
    units, points = free_play

    assert set(units) == set(points)
    assert all(units.values())


def test_reference_plant_units_follow_the_quantity(free_play):
    units, _ = free_play

    assert units["N-201.pressure"] == "psia"
    assert units["V-101.pressure"] == "psia"
    assert units["K-101.outlet_pressure"] == "psia"
    assert units["V-101.level"] == FRACTION
    assert units["P-101.speed"] == FRACTION
    assert units["K-101.load_target"] == FRACTION
    assert units["LV-101.position"] == FRACTION
    assert units["P-101.flow"] == "GPM"
    assert units["K-101.flow"] == "SCFM"


def test_every_stream_takes_its_domains_flow_unit(free_play):
    units, points = free_play
    streams = {point: units[point] for point in points if point.startswith("B-")}

    assert streams
    assert streams["B-P-101.flow"] == streams["B-LV-101.flow"] == "GPM"
    assert streams["B-K-101.flow"] == streams["B-PV-101.flow"] == "SCFM"


def test_a_loops_pv_and_sp_take_its_measurements_unit_and_out_is_a_fraction(free_play):
    units, _ = free_play

    assert units["PIC-101.pv"] == units["PIC-101.sp"] == "psia"
    assert units["PIC-101.out"] == FRACTION


VALVE_ONLY = {
    "nodes": [
        {"id": "N-1", "boundary": True, "pressure": 60.0, "domain": "gas"},
        {"id": "N-2", "boundary": True, "pressure": 50.0, "domain": "gas"},
    ],
    "equipment": [
        {"tag": "XV-1", "type": "control_valve", "node_in": "N-1", "node_out": "N-2", "design": {}},
    ],
}


def test_a_domain_nothing_classifies_gets_no_unit_whatever_its_name():
    units = runtime_of(VALVE_ONLY).trend_units()

    assert units["B-XV-1.flow"] == ""
    assert units["XV-1.position"] == FRACTION


def test_a_vessels_declared_phase_classifies_a_valve_only_domain():
    config = {
        "nodes": [
            {"id": "N-1", "boundary": True, "pressure": 85.0, "domain": "a"},
            {"id": "N-2", "boundary": True, "pressure": 60.0, "domain": "a"},
            {"id": "N-11", "boundary": True, "pressure": 300.0, "domain": "b"},
            {"id": "N-12", "boundary": True, "pressure": 200.0, "domain": "b"},
        ],
        "equipment": [
            {"tag": "FV-1", "type": "control_valve", "node_in": "N-1", "node_out": "N-2", "design": {}},
            {"tag": "FV-2", "type": "control_valve", "node_in": "N-11", "node_out": "N-12", "design": {}},
            {
                "tag": "V-1",
                "type": "vessel",
                "ports": {
                    "feed": {"node": "N-2", "phase": "liquid", "purpose": "process", "direction": "inlet"},
                    "vapor_in": {"node": "N-12", "phase": "vapor", "purpose": "process", "direction": "inlet"},
                },
                "paths": [],
                "design": {},
            },
        ],
    }

    units = runtime_of(config).trend_units()

    assert units["B-FV-1.flow"] == "GPM"
    assert units["B-FV-2.flow"] == "SCFM"


REFERENCE_PLANT = Path(__file__).resolve().parent.parent / "config" / "plants" / "olefins_lite.yaml"


def branch(device):
    return SimpleNamespace(device=device)


def test_two_sources_that_disagree_in_one_domain_refuse_naming_both():
    plant = load_plant_file(REFERENCE_PLANT)
    pump = plant.devices["P-101"]
    compressor = plant.devices["K-101"]
    topology = SimpleNamespace(branches={"a": branch(pump), "b": branch(compressor)})

    with pytest.raises(ValueError, match="P-101 says GPM.*K-101 says SCFM|K-101 says SCFM.*P-101 says GPM"):
        domain_units({"mixed": topology}, ())


def test_an_attachment_and_a_machine_that_agree_give_one_unit():
    plant = load_plant_file(REFERENCE_PLANT)
    topology = SimpleNamespace(branches={"a": branch(plant.devices["P-101"])})
    attachment = SimpleNamespace(domain="d", unit="GPM", port=SimpleNamespace(name="feed"))
    coupling = SimpleNamespace(vessel=SimpleNamespace(tag="V-1"), attachments=(attachment,))

    assert domain_units({"d": topology}, [coupling]) == {"d": "GPM"}


def test_every_numeric_operator_visible_field_has_a_unit():
    flags = {"running", "lifted"}

    for kind, fields in VISIBLE.items():
        missing = sorted(fields - flags - set(FIELD_UNITS.get(kind, {})))

        assert not missing, f"{kind.__name__} publishes {missing} with no entry in FIELD_UNITS"


def test_a_loaded_scenario_serves_its_own_plants_units():
    session = TrainingSession()
    try:
        session.load("pump_trip")

        assert set(session.trend_units()) == set(session.trend_points())
        assert session.trend_units()["P-101.flow"] == "GPM"
    finally:
        session.end()
