import copy

import pytest

from app.equipment.base import INLET, OUTLET, Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.plant.loader import PlantConfigError, load_plant
from app.plant.validate import validate


class SeparatorDouble(Equipment):
    """One inlet and two outlets: no pairing is inferable from the ports, so
    every branch has to come from an explicit path."""

    def __init__(self, tag="V-101"):
        super().__init__(
            tag,
            ports={
                "liquid_in": INLET,
                "liquid_out": OUTLET,
                "vapor_out": OUTLET,
            },
        )

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return 0.0

    def get_state(self):
        return {}


DEVICE_TYPES = {
    "pump": CentrifugalPump,
    "compressor": GasCompressor,
    "vessel": SeparatorDouble,
}


def two_domain_config():
    return {
        "nodes": [
            {"id": "L-01", "boundary": True, "pressure": 50.0, "domain": "liquid"},
            {"id": "L-02", "boundary": False, "pressure": 60.0, "domain": "liquid"},
            {"id": "G-01", "boundary": True, "pressure": 100.0, "domain": "gas"},
            {"id": "G-02", "boundary": True, "pressure": 900.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "L-01",
                "node_out": "L-02",
                "design": {"max_flow": 1000.0},
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "G-01",
                "node_out": "G-02",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "liquid_in": "L-02",
                    "liquid_out": "L-01",
                    "vapor_out": "G-01",
                },
                "paths": [],
                "design": {},
            },
        ],
    }


def pathed_config():
    # A separator with one hydraulic path, liquid_in -> liquid_out, and a vapor
    # outlet that is only attached.
    config = two_domain_config()
    del config["equipment"][:2]

    config["nodes"] = [
        {"id": "L-01", "boundary": True, "pressure": 50.0, "domain": "liquid"},
        {"id": "L-02", "boundary": True, "pressure": 60.0, "domain": "liquid"},
        {"id": "G-01", "boundary": True, "pressure": 100.0, "domain": "gas"},
    ]
    config["equipment"][0]["ports"] = {
        "liquid_in": "L-01",
        "liquid_out": "L-02",
        "vapor_out": "G-01",
    }
    config["equipment"][0]["paths"] = [{"from": "liquid_in", "to": "liquid_out"}]

    return config


def load(config):
    return load_plant(config, device_types=DEVICE_TYPES)


def rejected(config):
    with pytest.raises(PlantConfigError) as raised:
        load(config)

    return raised.value.errors


# Named form loads


def test_a_named_path_becomes_one_branch_with_explicit_ports():
    plant = load(pathed_config())

    topology = plant.topologies["liquid"]
    branch = topology.branches["B-V-101"]

    assert branch.from_node.id == "L-01"
    assert branch.to_node.id == "L-02"
    assert branch.from_port.name == "liquid_in"
    assert branch.to_port.name == "liquid_out"
    assert len(topology.branches) == 1


def test_a_port_no_path_claims_is_attached_to_its_node():
    plant = load(pathed_config())

    vapor = plant.devices["V-101"].port("vapor_out")

    assert vapor.node is plant.nodes["G-01"]
    assert plant.topologies["gas"].branches == {}


def test_a_coupling_device_attached_across_two_domains_loads():
    plant = load(two_domain_config())

    vessel = plant.devices["V-101"]

    assert vessel.port("liquid_in").node is plant.nodes["L-02"]
    assert vessel.port("liquid_out").node is plant.nodes["L-01"]
    assert vessel.port("vapor_out").node is plant.nodes["G-01"]


def test_a_coupling_device_is_on_plant_devices_and_in_no_topology():
    plant = load(two_domain_config())

    assert list(plant.devices) == ["P-101", "K-101", "V-101"]

    for topology in plant.topologies.values():
        assert "V-101" not in topology.devices

    assert set(plant.topologies["liquid"].devices) == {"P-101"}
    assert set(plant.topologies["gas"].devices) == {"K-101"}


def test_sugar_branch_ids_are_unchanged():
    plant = load(two_domain_config())

    assert set(plant.topologies["liquid"].branches) == {"B-P-101"}
    assert set(plant.topologies["gas"].branches) == {"B-K-101"}


def test_empty_paths_are_accepted_with_ports():
    config = two_domain_config()

    assert config["equipment"][2]["paths"] == []
    assert load(config).devices["V-101"].tag == "V-101"


# Form selection


def test_paths_are_required_with_ports():
    config = two_domain_config()
    del config["equipment"][2]["paths"]

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[2]:") and "'paths'" in error for error in errors
    )


def test_both_wiring_forms_at_once_is_rejected():
    config = two_domain_config()
    config["equipment"][2]["node_in"] = "L-02"
    config["equipment"][2]["node_out"] = "L-01"

    errors = rejected(config)

    assert any(error.startswith("$.equipment[2]:") and "mixes" in error for error in errors)


def test_neither_wiring_form_is_rejected():
    config = two_domain_config()
    del config["equipment"][0]["node_in"]
    del config["equipment"][0]["node_out"]

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[0]:") and "no wiring" in error for error in errors
    )


def test_paths_without_ports_is_rejected():
    config = two_domain_config()
    config["equipment"][0]["paths"] = [{"from": "suction", "to": "discharge"}]

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[0]:") and "without 'ports'" in error
        for error in errors
    )


def test_only_one_of_node_in_and_node_out_is_rejected():
    config = two_domain_config()
    del config["equipment"][0]["node_out"]

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[0]:") and "only 'node_in'" in error
        for error in errors
    )


# Named-form rejections


def test_a_ports_key_that_is_not_a_device_port_is_rejected():
    config = two_domain_config()
    config["equipment"][2]["ports"]["bottoms"] = "L-01"

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[2].ports.bottoms:")
        and "liquid_in" in error
        for error in errors
    )


def test_a_device_port_missing_from_ports_is_rejected_as_dangling():
    config = two_domain_config()
    del config["equipment"][2]["ports"]["vapor_out"]

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[2].ports:") and "vapor_out" in error
        for error in errors
    )


def test_a_ports_value_that_is_not_a_node_is_rejected():
    config = two_domain_config()
    config["equipment"][2]["ports"]["vapor_out"] = "G-99"

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[2].ports.vapor_out:") and "G-99" in error
        for error in errors
    )


def test_more_than_one_path_is_rejected_and_says_why():
    config = pathed_config()
    config["equipment"][0]["paths"].append({"from": "liquid_in", "to": "vapor_out"})

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[0].paths:") and "device-wide" in error
        for error in errors
    )


def test_a_path_end_that_is_not_a_ports_key_is_rejected():
    config = pathed_config()
    config["equipment"][0]["paths"] = [{"from": "liquid_in", "to": "bottoms"}]

    errors = rejected(config)

    assert any(error.startswith("$.equipment[0].paths[0].to:") for error in errors)


def test_a_path_must_start_at_an_inlet_and_end_at_an_outlet():
    config = pathed_config()
    config["equipment"][0]["paths"] = [{"from": "liquid_out", "to": "liquid_in"}]

    errors = rejected(config)

    assert any(error.startswith("$.equipment[0].paths[0].from:") for error in errors)
    assert any(error.startswith("$.equipment[0].paths[0].to:") for error in errors)


def test_a_path_between_one_node_is_rejected():
    config = pathed_config()
    config["equipment"][0]["ports"]["liquid_out"] = "L-01"

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[0].paths[0]:") and "starts and ends" in error
        for error in errors
    )


def test_a_path_across_two_domains_is_rejected():
    config = pathed_config()
    config["equipment"][0]["paths"] = [{"from": "liquid_in", "to": "vapor_out"}]

    errors = rejected(config)

    assert any(
        error.startswith("$.equipment[0].paths[0]:")
        and "'liquid'" in error
        and "'gas'" in error
        for error in errors
    )


def test_sugar_on_a_multi_port_device_is_still_rejected_at_its_item():
    config = two_domain_config()
    config["equipment"][2] = {
        "tag": "V-101",
        "type": "vessel",
        "node_in": "L-02",
        "node_out": "L-01",
        "design": {},
    }

    errors = rejected(config)

    assert any("$.equipment[2]" in error and "outlet" in error for error in errors)


# Round trip


def test_round_trip_of_the_named_form_is_exact():
    config = pathed_config()

    assert load(config).to_config() == config


def test_round_trip_of_a_coupling_device_is_exact():
    config = two_domain_config()

    assert load(config).to_config() == config


def test_round_trip_of_a_mixed_config_keeps_each_form_and_the_order():
    config = two_domain_config()

    emitted = load(config).to_config()

    assert emitted == config
    assert [item["tag"] for item in emitted["equipment"]] == ["P-101", "K-101", "V-101"]
    assert "node_in" in emitted["equipment"][0]
    assert "ports" in emitted["equipment"][2]


def test_ports_and_paths_keep_config_order():
    config = pathed_config()
    config["equipment"][0]["ports"] = {
        "vapor_out": "G-01",
        "liquid_out": "L-02",
        "liquid_in": "L-01",
    }

    emitted = load(config).to_config()

    assert list(emitted["equipment"][0]["ports"]) == ["vapor_out", "liquid_out", "liquid_in"]
    assert emitted == config


def test_a_round_tripped_config_loads_again():
    emitted = load(two_domain_config()).to_config()

    assert list(load(copy.deepcopy(emitted)).devices) == ["P-101", "K-101", "V-101"]


def test_design_values_are_read_back_from_the_device_in_either_form():
    plant = load(two_domain_config())

    plant.devices["P-101"].max_flow = 750.0

    assert plant.to_config()["equipment"][0]["design"] == {
        "max_flow": pytest.approx(750.0),
    }


# Validator


def test_validator_applies_an_additional_properties_subschema():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "number"}},
        "additionalProperties": {"type": "string", "minLength": 1},
    }

    errors = validate({"a": 1.0, "b": "x", "c": 3, "d": ""}, schema)

    assert len(errors) == 2
    assert any(error.startswith("$.c:") for error in errors)
    assert any(error.startswith("$.d:") for error in errors)


def test_validator_leaves_named_properties_out_of_the_additional_subschema():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "number"}},
        "additionalProperties": {"type": "string"},
    }

    assert validate({"a": 1.0}, schema) == []


def test_validator_additional_properties_false_keeps_its_meaning():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "number"}},
        "additionalProperties": False,
    }

    assert validate({"a": 1.0, "b": 2.0}, schema) == ["$: unexpected property 'b'"]


def test_validator_enforces_min_properties():
    schema = {"type": "object", "minProperties": 1}

    assert validate({}, schema) == ["$: has 0 properties, fewer than the minimum of 1"]
    assert validate({"a": 1}, schema) == []


def test_schema_rejects_empty_ports_and_non_string_node_ids():
    config = two_domain_config()
    config["equipment"][2]["ports"] = {}

    assert any("$.equipment[2].ports" in error for error in validate(config))

    config["equipment"][2]["ports"] = {"liquid_in": 7}

    assert any("$.equipment[2].ports.liquid_in" in error for error in validate(config))


def test_schema_rejects_a_path_missing_an_end_or_carrying_extras():
    config = pathed_config()
    config["equipment"][0]["paths"] = [{"from": "liquid_in"}]

    assert any("$.equipment[0].paths[0]" in error for error in validate(config))

    config["equipment"][0]["paths"] = [{"from": "a", "to": "b", "via": "c"}]

    assert any("unexpected property 'via'" in error for error in validate(config))
