import pytest

from app.plant.validate import validate


def valid_config():
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 750.0},
            {"id": "N-02", "boundary": False, "pressure": 700.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {"shutoff_pressure_rise": 60.0},
            },
        ],
        "limits": [
            {"tag": "P-101", "variable": "suction_pressure", "lo": 30.0, "lo_lo": 20.0, "trip": True},
        ],
        "controllers": [
            {
                "tag": "PIC-101",
                "pv": "N-02.pressure",
                "sp": 700.0,
                "out": "P-101.speed_target",
                "mode": "AUTO",
                "kp": 0.5,
                "ki": 0.1,
                "kd": 0.0,
            },
        ],
        "interlocks": [
            {
                "tag": "PSLL-101",
                "condition": "P-101.suction_pressure < 20.0",
                "delay_s": 2.0,
                "actions": ["P-101.stop"],
                "reset": "manual",
            },
        ],
    }


def test_valid_config_passes():
    assert validate(valid_config()) == []


def test_optional_sections_may_be_omitted():
    config = valid_config()
    del config["limits"]
    del config["controllers"]
    del config["interlocks"]

    assert validate(config) == []


def test_missing_required_top_level_key_fails():
    config = valid_config()
    del config["nodes"]

    errors = validate(config)

    assert any("nodes" in e for e in errors)


def test_missing_required_field_names_the_path():
    config = valid_config()
    del config["equipment"][0]["node_out"]

    errors = validate(config)

    assert errors == ["$.equipment[0]: missing required property 'node_out'"]


def test_unknown_top_level_key_rejected():
    config = valid_config()
    config["turbines"] = []

    errors = validate(config)

    assert errors == ["$: unexpected property 'turbines'"]


def test_unknown_property_on_equipment_item_rejected():
    config = valid_config()
    config["equipment"][0]["manufacturer"] = "Acme"

    errors = validate(config)

    assert errors == ["$.equipment[0]: unexpected property 'manufacturer'"]


def test_wrong_type_reported_with_path():
    config = valid_config()
    config["nodes"][0]["pressure"] = "high"

    errors = validate(config)

    assert errors == ["$.nodes[0].pressure: expected number, got string"]


def test_unknown_equipment_type_rejected():
    config = valid_config()
    config["equipment"][0]["type"] = "widget"

    errors = validate(config)

    assert any("$.equipment[0].type" in e for e in errors)


def test_unknown_controller_mode_rejected():
    config = valid_config()
    config["controllers"][0]["mode"] = "CASCADE"

    errors = validate(config)

    assert any("$.controllers[0].mode" in e for e in errors)


def test_empty_nodes_list_rejected():
    config = valid_config()
    config["nodes"] = []

    errors = validate(config)

    assert any(e.startswith("$.nodes:") for e in errors)


def test_interlock_with_no_actions_rejected():
    config = valid_config()
    config["interlocks"][0]["actions"] = []

    errors = validate(config)

    assert any(e.startswith("$.interlocks[0].actions:") for e in errors)


def test_negative_interlock_delay_rejected():
    config = valid_config()
    config["interlocks"][0]["delay_s"] = -1.0

    errors = validate(config)

    assert any("$.interlocks[0].delay_s" in e for e in errors)


def test_multiple_errors_all_reported():
    config = valid_config()
    del config["nodes"][0]["pressure"]
    config["equipment"][0]["type"] = "widget"

    errors = validate(config)

    assert len(errors) == 2
