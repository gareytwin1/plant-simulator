import copy
import json

import pytest
import yaml

from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.plant.loader import PlantConfigError, load_plant, load_plant_file


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
    }


def write(tmp_path, name, config):
    path = tmp_path / name

    if path.suffix == ".json":
        path.write_text(json.dumps(config))
    else:
        path.write_text(yaml.safe_dump(config, sort_keys=False))

    return path


def rejected_file(path, **kwargs):
    with pytest.raises(PlantConfigError) as excinfo:
        load_plant_file(path, **kwargs)

    return excinfo.value.errors


@pytest.mark.parametrize("name", ["plant.yaml", "plant.yml", "plant.json"])
def test_each_supported_suffix_loads_the_same_plant(tmp_path, name):
    plant = load_plant_file(write(tmp_path, name, valid_config()))

    assert set(plant.topology.devices) == {"P-101", "K-101"}
    assert set(plant.topology.nodes) == {"N-01", "N-02", "N-03"}


def test_yaml_and_json_produce_identical_configs(tmp_path):
    from_yaml = load_plant_file(write(tmp_path, "plant.yaml", valid_config()))
    from_json = load_plant_file(write(tmp_path, "plant.json", valid_config()))

    assert from_yaml.to_config() == from_json.to_config()


def test_yaml_design_values_reach_live_equipment(tmp_path):
    plant = load_plant_file(write(tmp_path, "plant.yaml", valid_config()))

    pump = plant.topology.devices["P-101"]

    assert pump.shutoff_pressure_rise == pytest.approx(90.0)
    assert pump.max_flow == pytest.approx(1000.0)


def test_yaml_boundary_pressures_are_loaded(tmp_path):
    plant = load_plant_file(write(tmp_path, "plant.yaml", valid_config()))

    assert plant.topology.nodes["N-01"].pressure == pytest.approx(50.0)
    assert plant.topology.nodes["N-03"].pressure == pytest.approx(875.0)
    assert plant.topology.nodes["N-01"].is_boundary
    assert not plant.topology.nodes["N-02"].is_boundary


def test_yaml_plant_round_trips(tmp_path):
    path = write(tmp_path, "plant.yaml", valid_config())

    config = load_plant_file(path).to_config()

    assert load_plant(copy.deepcopy(config)).to_config() == config


def test_an_integer_in_yaml_may_set_a_float_attribute(tmp_path):
    config = valid_config()
    config["equipment"][0]["design"]["max_flow"] = 1000

    plant = load_plant_file(write(tmp_path, "plant.yaml", config))

    assert plant.topology.devices["P-101"].max_flow == pytest.approx(1000.0)


# Rejections: YAML goes through the same C3 validation as JSON


def test_malformed_yaml_is_a_clear_error(tmp_path):
    path = tmp_path / "plant.yaml"
    path.write_text("nodes: [unclosed\n  - {")

    errors = rejected_file(path)

    assert len(errors) == 1
    assert "plant.yaml" in errors[0]
    assert "YAML" in errors[0]


def test_malformed_json_is_a_clear_error(tmp_path):
    path = tmp_path / "plant.json"
    path.write_text("{not json")

    errors = rejected_file(path)

    assert len(errors) == 1
    assert "JSON" in errors[0]


def test_an_unsupported_suffix_is_rejected(tmp_path):
    path = tmp_path / "plant.toml"
    path.write_text("")

    errors = rejected_file(path)

    assert "unsupported plant file type" in errors[0]


@pytest.mark.parametrize("name", ["plant.yaml", "plant.yml"])
def test_an_empty_yaml_file_is_rejected_naming_the_file(tmp_path, name):
    path = tmp_path / name
    path.write_text("")

    errors = rejected_file(path)

    assert len(errors) == 1
    assert str(path) in errors[0]
    assert "empty" in errors[0]


def test_a_yaml_schema_violation_gets_the_same_errors_as_json(tmp_path):
    config = valid_config()
    del config["nodes"][0]["pressure"]

    from_yaml = rejected_file(write(tmp_path, "plant.yaml", config))
    from_json = rejected_file(write(tmp_path, "plant.json", config))

    assert from_yaml == from_json
    assert any("$.nodes[0]" in error and "pressure" in error for error in from_yaml)


def test_a_yaml_reference_error_gets_the_same_errors_as_json(tmp_path):
    config = valid_config()
    config["equipment"][0]["node_out"] = "N-99"

    from_yaml = rejected_file(write(tmp_path, "plant.yaml", config))
    from_json = rejected_file(write(tmp_path, "plant.json", config))

    assert from_yaml == from_json
    assert any("N-99" in error for error in from_yaml)


def test_yaml_cannot_smuggle_python_objects(tmp_path):
    path = tmp_path / "plant.yaml"
    path.write_text("nodes: !!python/object/apply:os.getcwd []\n")

    assert rejected_file(path)


# C3's `type` enum names more kinds than exist. A type with no model must be
# refused rather than loaded with a stand-in. The vessel gained its model at
# T5-2, the furnace at T6-4 - closing the schema's last such gap - so this
# test manufactures the gap directly through a restricted `device_types`
# table rather than naming a real one.


def test_a_type_with_no_model_is_refused_rather_than_stood_in_for(tmp_path):
    config = valid_config()
    config["equipment"].append(
        {
            "tag": "F-101",
            "type": "furnace",
            "node_in": "N-02",
            "node_out": "N-03",
            "design": {},
        },
    )

    errors = rejected_file(
        write(tmp_path, "plant.yaml", config),
        device_types={"pump": CentrifugalPump, "compressor": GasCompressor},
    )

    assert any("'furnace' has no device model yet" in error for error in errors)
