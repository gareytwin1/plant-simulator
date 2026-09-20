import copy
import math
import warnings
from pathlib import Path

import pytest

from app.engine.network import NetworkSolver
from app.plant.loader import PlantConfigError, load_plant, load_plant_file

PLANTS = Path(__file__).resolve().parent.parent / "config" / "plants"

# Two identical devices in series between boundaries P_lo and P_hi
# (ADR 0001 section 7.1): the internal node sits at the mean, and each branch
# carries sqrt((2 * shutoff - (P_hi - P_lo)) / (2 * R)).
FIXTURES = {
    "liquid_transfer": {
        "domain": "liquid",
        "tags": ("P-101", "P-102"),
        "internal": "N-102",
        "boundaries": ("N-101", "N-103"),
        "lo": 50.0,
        "hi": 180.0,
        "shutoff": 75.0,
        "resistance_attribute": "pump_resistance",
        "design_key": "speed",
    },
    "gas_compression": {
        "domain": "gas",
        "tags": ("K-101", "K-102"),
        "internal": "N-202",
        "boundaries": ("N-201", "N-203"),
        "lo": 60.0,
        "hi": 480.0,
        "shutoff": 220.0,
        "resistance_attribute": "compressor_resistance",
        "design_key": "load",
    },
}


def expected_internal_pressure(spec):
    return (spec["lo"] + spec["hi"]) / 2.0


def expected_flow(spec, plant):
    device = plant.topology.devices[spec["tags"][0]]
    resistance = getattr(device, spec["resistance_attribute"])
    rise = spec["hi"] - spec["lo"]

    return math.sqrt((2.0 * spec["shutoff"] - rise) / (2.0 * resistance))


def load(name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        return load_plant_file(PLANTS / f"{name}.yaml")


def solve(plant):
    result = NetworkSolver(plant.topology).solve()

    assert result.converged

    return result


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_loads_as_one_topology_in_its_domain(name):
    spec = FIXTURES[name]

    plant = load(name)

    assert list(plant.topologies) == [spec["domain"]]
    assert set(plant.topology.devices) == set(spec["tags"])
    assert set(plant.nodes) == {*spec["boundaries"], spec["internal"]}


@pytest.mark.parametrize("name", FIXTURES)
def test_every_node_declares_its_domain(name):
    spec = FIXTURES[name]

    config = load(name).to_config()

    assert [node["domain"] for node in config["nodes"]] == [spec["domain"]] * 3


@pytest.mark.parametrize("name", FIXTURES)
def test_design_value_reaches_both_devices(name):
    spec = FIXTURES[name]

    plant = load(name)

    for tag in spec["tags"]:
        assert getattr(plant.topology.devices[tag], spec["design_key"]) == pytest.approx(1.0)


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_solves_and_converges(name):
    result = solve(load(name))

    assert result.residual <= 1.0


@pytest.mark.parametrize("name", FIXTURES)
def test_solved_flow_matches_closed_form(name):
    spec = FIXTURES[name]

    plant = load(name)

    solve(plant)

    for branch in plant.topology.branches.values():
        assert branch.flow == pytest.approx(expected_flow(spec, plant))


@pytest.mark.parametrize("name", FIXTURES)
def test_solved_internal_pressure_matches_closed_form(name):
    spec = FIXTURES[name]

    plant = load(name)

    solve(plant)

    assert plant.nodes[spec["internal"]].pressure == pytest.approx(
        expected_internal_pressure(spec),
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_boundary_pressures_are_untouched_by_the_solve(name):
    spec = FIXTURES[name]

    plant = load(name)

    solve(plant)

    assert plant.nodes[spec["boundaries"][0]].pressure == pytest.approx(spec["lo"])
    assert plant.nodes[spec["boundaries"][1]].pressure == pytest.approx(spec["hi"])


@pytest.mark.parametrize("name", FIXTURES)
def test_mass_balance_closes_at_the_internal_node(name):
    spec = FIXTURES[name]

    plant = load(name)

    solve(plant)

    net = 0.0

    for branch in plant.topology.branches_at(spec["internal"]):
        net += branch.flow if branch.to_node.id == spec["internal"] else -branch.flow

    assert net == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("name", FIXTURES)
def test_round_trip_plant_config_plant_is_identical(name):
    plant = load(name)

    config = plant.to_config()
    again = load_plant(copy.deepcopy(config))

    assert again.to_config() == config
    assert list(again.nodes) == list(plant.nodes)


@pytest.mark.parametrize("name", FIXTURES)
def test_mixed_domain_variant_is_rejected_naming_the_branch(name):
    spec = FIXTURES[name]

    config = load(name).to_config()

    other = "gas" if spec["domain"] == "liquid" else "liquid"

    for node in config["nodes"]:
        if node["id"] == spec["internal"]:
            node["domain"] = other

    with pytest.raises(PlantConfigError) as excinfo:
        load_plant(config)

    message = str(excinfo.value)

    for tag in spec["tags"]:
        assert f"{tag!r} joins two flow domains" in message
