import dataclasses
import json

import pytest

from app.engine.network import SolverResult
from app.engine.snapshot import SOLVER_KEYS, build_snapshot, solver_status


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
    snapshot = make_snapshot(alarms=[{"id": "a1", "severity": "high"}])

    assert isinstance(snapshot.alarms, tuple)

    with pytest.raises(AttributeError):
        snapshot.alarms.append({"id": "a2"})

    with pytest.raises(TypeError):
        snapshot.alarms[0]["severity"] = "low"


def test_mutating_the_source_dict_after_construction_does_not_leak():
    source = {"K-101": {"load": 0.5}}
    snapshot = make_snapshot(equipment=source)

    source["K-101"]["load"] = 0.9
    source["P-101"] = {"speed": 1.0}

    assert snapshot.as_dict()["equipment"] == {"K-101": {"load": 0.5}}


def test_mutating_the_source_alarm_after_construction_does_not_leak():
    alarm = {"id": "a1", "severity": "high"}
    snapshot = make_snapshot(alarms=[alarm])

    alarm["severity"] = "low"

    assert snapshot.as_dict()["alarms"] == [{"id": "a1", "severity": "high"}]


def test_solver_explicit_empty_dict_is_not_replaced_by_the_default():
    """solver={} is a meaningful value (distinct from "not provided"),
    unlike nodes/streams/controllers/envelope where empty and unset are
    the same thing either way."""
    snapshot = make_snapshot(solver={})

    assert snapshot.as_dict()["solver"] == {}


def test_solver_status_projects_a_result_onto_the_c4_keys():
    result = SolverResult(
        converged=True,
        iterations=4,
        residual=0.25,
        pressure_residual=2.5e-8,
        flow_residual=0.0,
    )

    assert solver_status(result) == {
        "converged": True,
        "iterations": 4,
        "residual": 0.25,
    }


def test_solver_status_leaves_the_richer_diagnostics_out_of_c4():
    """pressure_residual, flow_residual and failure stay on the result.
    Widening C4's solver section is a contract change, not a convenience."""
    result = SolverResult(
        converged=False,
        iterations=50,
        residual=24.7,
        pressure_residual=2.47e-6,
        flow_residual=0.0,
        failure="iteration_cap",
    )

    assert set(solver_status(result)) == set(SOLVER_KEYS)


def test_a_failed_solve_reaches_the_snapshot_flagged_as_failed():
    result = SolverResult(
        converged=False,
        iterations=50,
        residual=24.7,
        pressure_residual=2.47e-6,
        flow_residual=0.0,
        failure="iteration_cap",
    )

    snapshot = make_snapshot(solver=solver_status(result))

    assert snapshot.as_dict()["solver"] == {
        "converged": False,
        "iterations": 50,
        "residual": 24.7,
    }


def test_half_a_solver_section_is_refused():
    """A section with a residual but no converged flag reads as a clean solve
    to any consumer that treats the flag as optional."""
    with pytest.raises(ValueError, match="never part of one"):
        make_snapshot(solver={"iterations": 50, "residual": 24.7})


def test_an_unexpected_solver_key_is_refused():
    with pytest.raises(ValueError, match="unexpected"):
        make_snapshot(
            solver={
                "converged": True,
                "iterations": 4,
                "residual": 0.25,
                "pressure_residual": 2.5e-8,
            },
        )
