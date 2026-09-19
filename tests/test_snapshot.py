import dataclasses
import json

import pytest

from app.engine.snapshot import build_snapshot


C4_KEYS = {
    "sim_time",
    "speed",
    "running",
    "equipment",
    "nodes",
    "streams",
    "controllers",
    "envelope",
    "alarms",
    "solver",
}


def make_snapshot(**overrides):
    kwargs = dict(
        sim_time=12.0,
        speed=1.0,
        running=True,
        equipment={"K-101": {"load": 0.5, "running": True}},
    )
    kwargs.update(overrides)

    return build_snapshot(**kwargs)


def test_snapshot_matches_c4_shape():
    snapshot = make_snapshot()

    assert set(snapshot.as_dict()) == C4_KEYS


def test_snapshot_is_json_serialisable():
    snapshot = make_snapshot()

    assert json.loads(json.dumps(snapshot.as_dict())) == snapshot.as_dict()


def test_unpopulated_sections_default_to_empty():
    snapshot = make_snapshot()

    assert snapshot.as_dict()["nodes"] == {}
    assert snapshot.as_dict()["streams"] == {}
    assert snapshot.as_dict()["controllers"] == {}
    assert snapshot.as_dict()["envelope"] == {}
    assert snapshot.as_dict()["alarms"] == []


def test_solver_defaults_to_a_converged_placeholder():
    snapshot = make_snapshot()

    assert snapshot.as_dict()["solver"] == {
        "converged": True,
        "iterations": 0,
        "residual": 0.0,
    }


def test_equipment_section_carries_get_state_through():
    snapshot = make_snapshot(
        equipment={"K-101": {"load": 0.5, "running": True}},
    )

    assert snapshot.as_dict()["equipment"] == {
        "K-101": {"load": 0.5, "running": True},
    }


def test_snapshot_top_level_fields_cannot_be_reassigned():
    snapshot = make_snapshot()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.sim_time = 999.0


def test_snapshot_mappings_cannot_be_mutated():
    snapshot = make_snapshot()

    with pytest.raises(TypeError):
        snapshot.equipment["K-101"] = {}

    with pytest.raises(TypeError):
        snapshot.equipment["K-101"]["load"] = 1.0

    with pytest.raises(TypeError):
        snapshot.nodes["N-01"] = {"pressure": 0.0}


def test_snapshot_alarms_cannot_be_mutated():
    snapshot = make_snapshot(alarms=[{"id": "a1"}])

    assert isinstance(snapshot.alarms, tuple)

    with pytest.raises(AttributeError):
        snapshot.alarms.append({"id": "a2"})


def test_mutating_the_source_dict_after_construction_does_not_leak():
    source = {"K-101": {"load": 0.5}}
    snapshot = make_snapshot(equipment=source)

    source["K-101"]["load"] = 0.9
    source["P-101"] = {"speed": 1.0}

    assert snapshot.as_dict()["equipment"] == {"K-101": {"load": 0.5}}
