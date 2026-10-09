"""Trend points - T17-3."""

import math

import pytest

from app import config
from app.api.visibility import VISIBLE
from app.historian.points import trend_values
from app.plant.loader import load_plant_file
from app.training.runtime import PlantRuntime
from tests.test_training_runtime import PLANT_FILE


def test_each_section_contributes_only_its_allowlisted_fields():
    view = {
        "equipment": {"P-101": {"speed": 1.0, "running": True, "label": "x"}},
        "nodes": {"N-1": {"pressure": 40.0, "is_boundary": True, "elevation": 0.0}},
        "streams": {"B-1": {"flow": 5, "pressure": 40.0, "composition": {}}},
        "controllers": {"PIC-101": {"pv": 1.0, "sp": 2.0, "out": 0.5, "kp": 0.01, "mode": "AUTO"}},
        "envelope": {"P-101.speed": {"band": "hi"}},
    }

    assert trend_values(view) == {
        "P-101.speed": 1.0,
        "N-1.pressure": 40.0,
        "B-1.flow": 5.0,
        "PIC-101.pv": 1.0,
        "PIC-101.sp": 2.0,
        "PIC-101.out": 0.5,
    }


def test_nan_is_kept_as_a_reading():
    values = trend_values({"nodes": {"N-1": {"pressure": float("nan")}}})

    assert math.isnan(values["N-1.pressure"])


def test_two_sections_publishing_one_point_is_an_error():
    view = {"nodes": {"X": {"pressure": 1.0}}, "equipment": {"X": {"pressure": 2.0}}}

    with pytest.raises(ValueError, match="X.pressure"):
        trend_values(view)


def test_a_field_the_operator_view_hides_is_never_a_point():
    runtime = PlantRuntime.from_plant(load_plant_file(PLANT_FILE))
    points = set(runtime.trend_points())
    engine = runtime.engine
    row = engine.snapshot().equipment

    hidden = {
        f"{tag}.{name}"
        for tag, fields in row.items()
        for name in fields
        if name not in VISIBLE.get(type(engine.equipment[tag]), frozenset())
    }

    assert hidden
    assert not hidden & points
    assert "V-101.level" in points
    assert "P-101.running" not in points


def test_points_are_sorted_and_fixed_at_construction():
    runtime = PlantRuntime.from_plant(load_plant_file(PLANT_FILE))
    before = runtime.trend_points()

    for _ in range(3):
        runtime.step(config.SIMULATION_STEP_SECONDS)

    assert before == tuple(sorted(before))
    assert runtime.trend_points() == before


def test_the_construction_snapshot_is_the_first_sample_and_each_step_adds_one():
    runtime = PlantRuntime.from_plant(load_plant_file(PLANT_FILE))
    start = runtime.engine.clock.sim_time

    for _ in range(5):
        runtime.step(config.SIMULATION_STEP_SECONDS)

    samples = runtime.trend_history(["V-101.level"])["V-101.level"]

    assert [sample.timestamp for sample in samples] == pytest.approx([start + i for i in range(6)])
    assert samples[-1].value == pytest.approx(runtime.snapshot().equipment["V-101"]["level"])


def test_an_unknown_point_is_a_key_error_naming_it():
    runtime = PlantRuntime.from_plant(load_plant_file(PLANT_FILE))

    with pytest.raises(KeyError, match="Z-999.level"):
        runtime.trend_history(["V-101.level", "Z-999.level"])
