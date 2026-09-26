import json
from pathlib import Path

import pytest

from app.plant.validate import validate


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "schema" / "scenario.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())

NON_FINITE = [float("nan"), float("inf"), float("-inf")]


def valid_scenario():
    return {
        "id": "compressor-trip-01",
        "description": "Compressor trips on high discharge pressure; recover before the timeout.",
        "plant": "gas_compression",
        "initial_condition": {
            "condition": "normal_operation",
            "overrides": {"K-101.speed": 0.9},
        },
        "malfunctions": [
            {
                "target_tag": "K-101",
                "parameter": "shutoff_pressure_rise",
                "value": 40.0,
                "profile": {"type": "ramp", "duration_s": 120.0},
                "start_condition": {"type": "at_time", "sim_time": 30.0},
            },
        ],
        "triggers": [
            {"id": "trip-onset", "type": "time", "sim_time": 30.0, "one_shot": True},
            {"id": "high-discharge", "type": "condition", "condition": "K-101.discharge_pressure > 900.0"},
            {"id": "operator-response", "type": "operator_action", "action": "K-101.stop"},
        ],
        "objectives": [
            {
                "id": "stabilize-discharge",
                "success": {"condition": "K-101.discharge_pressure < 800.0", "hold_duration_s": 300.0},
                "failure": {"condition": "K-101.tripped"},
            },
        ],
        "time_limit": 1800.0,
        "difficulty": "medium",
        "seed": 42,
    }


def test_valid_scenario_passes():
    assert validate(valid_scenario(), SCHEMA) == []


def test_optional_sections_may_be_omitted():
    scenario = valid_scenario()
    del scenario["description"]
    del scenario["malfunctions"]
    del scenario["triggers"]
    del scenario["objectives"]
    del scenario["initial_condition"]["overrides"]

    assert validate(scenario, SCHEMA) == []


def test_missing_required_top_level_key_fails():
    scenario = valid_scenario()
    del scenario["seed"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$: missing required property 'seed'"]


def test_unknown_top_level_key_rejected():
    scenario = valid_scenario()
    scenario["author"] = "control-room"

    errors = validate(scenario, SCHEMA)

    assert errors == ["$: unexpected property 'author'"]


def test_missing_initial_condition_name_fails():
    scenario = valid_scenario()
    del scenario["initial_condition"]["condition"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.initial_condition: missing required property 'condition'"]


def test_malfunction_missing_target_tag_fails():
    scenario = valid_scenario()
    del scenario["malfunctions"][0]["target_tag"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.malfunctions[0]: missing required property 'target_tag'"]


def test_malfunction_value_wrong_type_reported_with_path():
    scenario = valid_scenario()
    scenario["malfunctions"][0]["value"] = "low"

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.malfunctions[0].value: expected number, got string"]


def test_malfunction_profile_missing_type_fails():
    scenario = valid_scenario()
    del scenario["malfunctions"][0]["profile"]["type"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.malfunctions[0].profile: missing required property 'type'"]


def test_malfunction_unknown_property_rejected():
    scenario = valid_scenario()
    scenario["malfunctions"][0]["manufacturer"] = "Acme"

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.malfunctions[0]: unexpected property 'manufacturer'"]


def test_trigger_unknown_type_rejected():
    scenario = valid_scenario()
    scenario["triggers"][0]["type"] = "weather"

    errors = validate(scenario, SCHEMA)

    assert any("$.triggers[0].type" in e for e in errors)


def test_trigger_missing_id_fails():
    scenario = valid_scenario()
    del scenario["triggers"][0]["id"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.triggers[0]: missing required property 'id'"]


def test_objective_missing_success_fails():
    scenario = valid_scenario()
    del scenario["objectives"][0]["success"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.objectives[0]: missing required property 'success'"]


def test_objective_success_missing_condition_fails():
    scenario = valid_scenario()
    del scenario["objectives"][0]["success"]["condition"]

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.objectives[0].success: missing required property 'condition'"]


def test_objective_unknown_property_on_success_rejected():
    scenario = valid_scenario()
    scenario["objectives"][0]["success"]["threshold"] = 5

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.objectives[0].success: unexpected property 'threshold'"]


def test_negative_hold_duration_rejected():
    scenario = valid_scenario()
    scenario["objectives"][0]["success"]["hold_duration_s"] = -1.0

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.objectives[0].success.hold_duration_s: -1.0 is below the minimum of 0"]


def test_negative_time_limit_rejected():
    scenario = valid_scenario()
    scenario["time_limit"] = -1.0

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.time_limit: -1.0 is below the minimum of 0"]


def test_unknown_difficulty_rejected():
    scenario = valid_scenario()
    scenario["difficulty"] = "extreme"

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.difficulty: 'extreme' is not one of ['easy', 'medium', 'hard']"]


def test_seed_must_be_integer():
    scenario = valid_scenario()
    scenario["seed"] = 42.5

    errors = validate(scenario, SCHEMA)

    assert errors == ["$.seed: expected integer, got number"]


def test_multiple_errors_all_reported():
    scenario = valid_scenario()
    del scenario["seed"]
    scenario["difficulty"] = "extreme"

    errors = validate(scenario, SCHEMA)

    assert len(errors) == 2


@pytest.mark.parametrize("value", NON_FINITE)
def test_non_finite_time_limit_rejected(value):
    scenario = valid_scenario()
    scenario["time_limit"] = value

    errors = validate(scenario, SCHEMA)

    assert any("$.time_limit" in e for e in errors)


@pytest.mark.parametrize("value", NON_FINITE)
def test_non_finite_malfunction_value_rejected(value):
    scenario = valid_scenario()
    scenario["malfunctions"][0]["value"] = value

    errors = validate(scenario, SCHEMA)

    assert any("$.malfunctions[0].value" in e for e in errors)
