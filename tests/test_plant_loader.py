import copy
import json

import pytest

from app.equipment.base import INLET, OUTLET, Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.plant.loader import (
    Plant,
    PlantConfigError,
    load_plant,
    load_plant_file,
)


class ManifoldDouble(Equipment):
    """Two inlets and an outlet: wiring by direction alone is ambiguous, so a
    port is left with nothing to claim it."""

    def __init__(self, tag="M-101"):
        super().__init__(
            tag,
            ports={
                "inlet_a": INLET,
                "inlet_b": INLET,
                "outlet": OUTLET,
            },
        )

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return 0.0

    def get_state(self):
        return {}


def valid_config():
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 50.0},
            {"id": "N-02", "boundary": False, "pressure": 60.0},
            {"id": "N-03", "boundary": True, "pressure": 875.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {"shutoff_pressure_rise": 90.0, "max_flow": 1000.0},
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-02",
                "node_out": "N-03",
                "design": {},
            },
        ],
        "limits": [
            {"tag": "P-101", "variable": "suction_pressure", "lo": 30.0},
        ],
        "controllers": [],
        "interlocks": [],
    }


def rejected(config, **kwargs):
    with pytest.raises(PlantConfigError) as raised:
        load_plant(config, **kwargs)

    return raised.value.errors


def test_loads_a_valid_config_into_a_topology():
    plant = load_plant(valid_config())

    assert isinstance(plant, Plant)
    assert set(plant.topology.nodes) == {"N-01", "N-02", "N-03"}
    assert set(plant.topology.boundary_nodes) == {"N-01", "N-03"}
    assert isinstance(plant.topology.device("P-101"), CentrifugalPump)
    assert isinstance(plant.topology.device("K-101"), GasCompressor)
    assert plant.topology.unconnected_ports() == ()


def test_wires_devices_between_the_named_nodes():
    plant = load_plant(valid_config())

    pump = plant.topology.device("P-101")

    assert pump.port("suction").node is plant.topology.node("N-01")
    assert pump.port("discharge").node is plant.topology.node("N-02")


def test_design_values_are_applied_to_the_device():
    plant = load_plant(valid_config())

    pump = plant.topology.device("P-101")

    assert pump.shutoff_pressure_rise == pytest.approx(90.0)
    assert pump.max_flow == pytest.approx(1000.0)


def test_node_pressures_come_from_the_config():
    plant = load_plant(valid_config())

    assert plant.topology.node("N-02").pressure == pytest.approx(60.0)
    assert plant.topology.node("N-03").pressure == pytest.approx(875.0)


def test_loads_from_a_json_file(tmp_path):
    path = tmp_path / "plant.json"
    path.write_text(json.dumps(valid_config()))

    plant = load_plant_file(path)

    assert set(plant.topology.devices) == {"P-101", "K-101"}


# Rejections


def test_a_schema_violation_is_rejected_with_its_path():
    config = valid_config()
    del config["nodes"][0]["pressure"]

    errors = rejected(config)

    assert any("$.nodes[0]" in error and "pressure" in error for error in errors)


def test_a_device_with_more_ports_than_the_config_can_wire_is_rejected():
    # C3 names only node_in/node_out, so a two-inlet device cannot be wired
    # from config; the load error must name the item rather than leave a
    # dangling port for the solver to trip over.
    config = valid_config()
    config["equipment"][0]["type"] = "vessel"
    config["equipment"][0]["design"] = {}

    errors = rejected(
        config,
        device_types={
            "vessel": ManifoldDouble,
            "compressor": GasCompressor,
        },
    )

    assert any("$.equipment[0]" in error and "inlet" in error for error in errors)


def test_duplicate_tag_is_rejected():
    config = valid_config()
    config["equipment"][1]["tag"] = "P-101"

    errors = rejected(config)

    assert any(
        "$.equipment[1].tag" in error and "duplicate" in error and "$.equipment[0]" in error
        for error in errors
    )


def test_duplicate_node_id_is_rejected():
    config = valid_config()
    config["nodes"][2]["id"] = "N-01"

    errors = rejected(config)

    assert any("$.nodes[2].id" in error and "duplicate" in error for error in errors)


def test_zero_pressure_boundary_is_rejected():
    config = valid_config()
    config["nodes"][0]["pressure"] = 0.0

    errors = rejected(config)

    assert any("$.nodes[0].pressure" in error and "boundary" in error for error in errors)


def test_negative_pressure_boundary_is_rejected():
    config = valid_config()
    config["nodes"][2]["pressure"] = -5.0

    errors = rejected(config)

    assert any("$.nodes[2].pressure" in error for error in errors)


def test_zero_pressure_on_an_internal_node_is_allowed():
    config = valid_config()
    config["nodes"][1]["pressure"] = 0.0

    load_plant(config)


# R1 DEFECT REPRODUCTION: `pressure <= 0.0` is silently False for both NaN
# and every non-finite value, so before the fix a non-finite boundary
# pressure reached `Node()` and, downstream, the solver.
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_boundary_pressure_is_rejected(value):
    config = valid_config()
    config["nodes"][0]["pressure"] = value

    errors = rejected(config)

    assert any("$.nodes[0].pressure" in error for error in errors)


# R1 DEFECT REPRODUCTION: an internal (non-boundary) node's pressure had no
# check at all beyond the schema's bare "number" type, so a non-finite value
# reached `Node()` unrejected.
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_internal_pressure_is_rejected(value):
    config = valid_config()
    config["nodes"][1]["pressure"] = value

    errors = rejected(config)

    assert any("$.nodes[1].pressure" in error for error in errors)


def test_no_boundary_node_is_rejected():
    config = valid_config()

    for node in config["nodes"]:
        node["boundary"] = False

    errors = rejected(config)

    assert any("no boundary node" in error for error in errors)


def test_disconnected_subgraph_is_rejected():
    config = valid_config()
    config["nodes"] += [
        {"id": "N-08", "boundary": True, "pressure": 100.0},
        {"id": "N-09", "boundary": False, "pressure": 90.0},
    ]
    config["equipment"].append(
        {
            "tag": "P-102",
            "type": "pump",
            "node_in": "N-08",
            "node_out": "N-09",
            "design": {},
        },
    )

    errors = rejected(config)

    assert any(
        "disconnected" in error and "N-08" in error and "N-01" in error
        for error in errors
    )


def test_an_orphan_node_is_a_disconnected_subgraph():
    config = valid_config()
    config["nodes"].append({"id": "N-99", "boundary": False, "pressure": 10.0})

    errors = rejected(config)

    assert any("disconnected" in error and "N-99" in error for error in errors)


def test_unknown_node_reference_is_rejected():
    config = valid_config()
    config["equipment"][0]["node_out"] = "N-77"

    errors = rejected(config)

    assert any("$.equipment[0].node_out" in error and "N-77" in error for error in errors)


def test_equipment_between_a_node_and_itself_is_rejected():
    config = valid_config()
    config["equipment"][0]["node_out"] = "N-01"

    errors = rejected(config)

    assert any("$.equipment[0]" in error and "starts and ends" in error for error in errors)


def test_a_type_with_no_device_model_is_rejected():
    config = valid_config()
    config["equipment"][0]["type"] = "furnace"

    errors = rejected(config)

    assert any("$.equipment[0].type" in error and "furnace" in error for error in errors)


def test_unknown_design_key_is_rejected():
    config = valid_config()
    config["equipment"][0]["design"] = {"not_a_real_field": 1.0}

    errors = rejected(config)

    assert any("$.equipment[0].design.not_a_real_field" in error for error in errors)


def test_design_value_of_the_wrong_kind_is_rejected():
    config = valid_config()
    config["equipment"][0]["design"] = {"max_flow": "lots"}

    errors = rejected(config)

    assert any("$.equipment[0].design.max_flow" in error for error in errors)


def test_derived_design_attribute_is_rejected():
    config = valid_config()
    config["equipment"][0]["design"] = {"spread": 5.0}

    errors = rejected(config)

    assert any("$.equipment[0].design.spread" in error for error in errors)


# R1 DEFECT REPRODUCTION: `design` carries no per-key schema (C3 declares it
# only as {"type": "object"}), so the validator cannot see a non-finite
# design number at all — before the fix, `_apply_design` passed it straight
# to setattr, and a device with no range check of its own (R3 is a separate
# task) accepted it silently.
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_design_number_is_rejected(value):
    config = valid_config()
    config["equipment"][0]["design"]["shutoff_pressure_rise"] = value

    errors = rejected(config)

    assert any("$.equipment[0].design.shutoff_pressure_rise" in error for error in errors)


def test_every_problem_is_reported_in_one_error():
    config = valid_config()
    config["nodes"][0]["pressure"] = 0.0
    config["equipment"][1]["tag"] = "P-101"
    config["equipment"][0]["node_out"] = "N-77"

    errors = rejected(config)

    assert len(errors) == 3


# Round trip


def test_round_trip_plant_to_config_to_plant_is_identical():
    config = valid_config()

    first = load_plant(config)
    second = load_plant(first.to_config())

    assert first.to_config() == config
    assert second.to_config() == first.to_config()
    assert second.topology.get_state() == first.topology.get_state()


def test_round_trip_preserves_sections_the_loader_does_not_interpret():
    config = valid_config()

    assert load_plant(config).to_config()["limits"] == config["limits"]


def test_to_config_does_not_alias_the_input_config():
    config = valid_config()
    plant = load_plant(config)

    config["limits"][0]["lo"] = 1.0

    assert plant.to_config()["limits"][0]["lo"] == pytest.approx(30.0)


def test_loading_does_not_mutate_the_config():
    config = valid_config()
    before = copy.deepcopy(config)

    load_plant(config)

    assert config == before


def test_two_loads_give_independent_plants():
    config = valid_config()

    first = load_plant(config)
    second = load_plant(config)

    assert first.topology.device("P-101") is not second.topology.device("P-101")
    assert first.topology.node("N-02") is not second.topology.node("N-02")
