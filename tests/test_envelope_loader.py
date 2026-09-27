from pathlib import Path

import pytest

from app.envelope.evaluator import Severity
from app.envelope.loader import get_limit, load_limits
from app.plant.loader import load_plant_file
from app.plant.validate import validate

PLANTS = Path(__file__).resolve().parent.parent / "config" / "plants"


def test_limits_load_from_the_limits_key():
    config = {
        "limits": [
            {"tag": "V-101", "variable": "level", "lo": 0.2, "hi": 0.8},
        ],
    }

    evaluators = load_limits(config)

    assert set(evaluators) == {("V-101", "level")}
    assert evaluators[("V-101", "level")].evaluate(0.5, dt=1.0) is Severity.NORMAL
    assert evaluators[("V-101", "level")].evaluate(0.1, dt=1.0) is Severity.WARNING


def test_missing_limits_key_yields_an_empty_mapping():
    assert load_limits({}) == {}


def test_lo_lo_above_lo_is_rejected():
    config = {
        "limits": [
            {"tag": "V-101", "variable": "level", "lo_lo": 0.5, "lo": 0.2},
        ],
    }

    with pytest.raises(ValueError, match="V-101.level"):
        load_limits(config)


def test_a_repeated_tag_and_variable_is_rejected_rather_than_silently_overwritten():
    config = {
        "limits": [
            {"tag": "V-101", "variable": "level", "lo": 0.2},
            {"tag": "V-101", "variable": "level", "lo": 0.3},
        ],
    }

    with pytest.raises(ValueError, match="V-101.level"):
        load_limits(config)


def test_hi_above_hi_hi_is_rejected():
    config = {
        "limits": [
            {"tag": "K-101", "variable": "discharge_pressure", "hi": 400.0, "hi_hi": 350.0},
        ],
    }

    with pytest.raises(ValueError):
        load_limits(config)


def test_trip_flag_sends_the_outer_bounds_to_trip_not_alarm():
    config = {
        "limits": [
            {
                "tag": "V-101",
                "variable": "level",
                "lo_lo": 0.1,
                "hi_hi": 0.9,
                "trip": True,
            },
        ],
    }

    evaluators = load_limits(config)
    evaluator = evaluators[("V-101", "level")]

    assert evaluator.evaluate(0.1, dt=1.0) is Severity.TRIP
    assert evaluator.evaluate(0.5, dt=1.0) is Severity.NORMAL
    assert evaluator.evaluate(0.9, dt=1.0) is Severity.TRIP


def test_trip_false_sends_the_outer_bounds_to_alarm():
    config = {
        "limits": [
            {"tag": "K-101", "variable": "discharge_pressure", "lo_lo": 100.0, "hi_hi": 350.0},
        ],
    }

    evaluators = load_limits(config)
    evaluator = evaluators[("K-101", "discharge_pressure")]

    assert evaluator.evaluate(350.0, dt=1.0) is Severity.ALARM


def test_deadband_passes_through_to_the_evaluator():
    config = {
        "limits": [
            {"tag": "V-101", "variable": "level", "lo": 0.2, "hi": 0.8, "deadband": 0.05},
        ],
    }

    evaluator = load_limits(config)[("V-101", "level")]

    assert evaluator.deadband == 0.05


def test_a_single_sided_entry_leaves_the_other_side_unreachable():
    config = {
        "limits": [
            {"tag": "P-101", "variable": "flow", "lo_lo": -20.0, "trip": True},
        ],
    }

    evaluator = load_limits(config)[("P-101", "flow")]

    assert evaluator.evaluate(1_000_000.0, dt=1.0) is Severity.NORMAL
    assert evaluator.evaluate(-20.0, dt=1.0) is Severity.TRIP


def test_get_limit_returns_the_matching_evaluator():
    limits = load_limits(
        {"limits": [{"tag": "V-101", "variable": "level", "lo": 0.2, "hi": 0.8}]}
    )

    assert get_limit(limits, "V-101", "level") is limits[("V-101", "level")]


def test_get_limit_warns_rather_than_crashes_when_a_tag_has_no_limits():
    limits = load_limits({"limits": []})

    with pytest.warns(UserWarning, match="V-101.level"):
        result = get_limit(limits, "V-101", "level")

    assert result is None


def test_the_reference_plant_config_defines_loadable_limits():
    plant = load_plant_file(PLANTS / "olefins_lite.yaml")
    config = plant.to_config()

    assert validate(config) == []

    evaluators = load_limits(config)

    assert ("V-101", "level") in evaluators
    assert ("K-101", "discharge_pressure") in evaluators
    assert ("P-101", "flow") in evaluators

    # The design point is loaded exactly at steady state (T5-5), so every
    # configured tag reads NORMAL the moment the plant is built.
    assert evaluators[("V-101", "level")].evaluate(0.5, dt=1.0) is Severity.NORMAL
    assert evaluators[("K-101", "discharge_pressure")].evaluate(304.0, dt=1.0) is Severity.NORMAL
    assert evaluators[("P-101", "flow")].evaluate(50.0, dt=1.0) is Severity.NORMAL
