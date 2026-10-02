"""
Plant state machine (T12-3).

Gates are the test's own numbers: the machine takes them as input, so each test
states what it needs and nothing here is a plant constant. The reference
conditions in config/initial_conditions are real captured states, and the gates
below are chosen so cold_shutdown reads cold and normal_operation on-spec.
"""

import dataclasses
import json
import warnings
from pathlib import Path
from types import MappingProxyType

import pytest

from app.engine.engine import Engine
from app.engine.persistence import restore_state
from app.plant.loader import load_plant_file
from app.plant.states import (
    TRANSITIONS,
    IllegalTransition,
    PlantStateMachine,
)
from app.plant.states import (
    PlantState as S,
)

CONFIG = Path(__file__).resolve().parent.parent / "config"
OLEFINS = CONFIG / "plants" / "olefins_lite.yaml"
LIBRARY = CONFIG / "initial_conditions"

GATES = {
    (S.COLD, S.PURGED): ["V-101.level >= 0.0"],
    (S.PURGED, S.PRESSURISED): ["V-101.pressure >= 150"],
    (S.PRESSURISED, S.CIRCULATING): ["K-101.load >= 0.9", "P-101.speed >= 0.8"],
    (S.CIRCULATING, S.ON_SPEC): ["V-101.level >= 0.4", "P-101.speed >= 0.95"],
    (S.ON_SPEC, S.CIRCULATING): ["P-101.speed >= 0.0"],
    (S.SHUTTING_DOWN, S.COLD): ["K-101.load <= 0.0", "P-101.speed <= 0.0"],
}


def snapshot_of(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        engine = Engine.from_plant(load_plant_file(OLEFINS))
        restore_state(engine, json.loads((LIBRARY / f"{name}.json").read_text()))

        return engine.step(0.0)


def with_reading(snapshot, tag, variable, value):
    row = {**snapshot.equipment[tag], variable: value}
    equipment = MappingProxyType({**snapshot.equipment, tag: MappingProxyType(row)})

    return dataclasses.replace(snapshot, equipment=equipment)


def machine_at(state, snapshot):
    return PlantStateMachine(snapshot, GATES, state=state)


@pytest.fixture(scope="module")
def normal():
    return snapshot_of("normal_operation")


@pytest.fixture(scope="module")
def cold():
    return snapshot_of("cold_shutdown")


def test_a_new_machine_starts_cold(normal):
    assert PlantStateMachine(normal, GATES).state is S.COLD


def test_the_whole_chain_advances_on_a_plant_that_meets_every_gate(normal):
    machine = PlantStateMachine(normal, GATES)

    for target in (S.PURGED, S.PRESSURISED, S.CIRCULATING, S.ON_SPEC):
        assert machine.advance(target, normal) == ()
        assert machine.state is target


# Per edge: the snapshot that meets it, and one reading that breaks it.
BREAKS = {
    (S.COLD, S.PURGED): ("normal", "V-101", "level", -1.0),
    (S.PURGED, S.PRESSURISED): ("normal", "V-101", "pressure", 100.0),
    (S.PRESSURISED, S.CIRCULATING): ("normal", "K-101", "load", 0.5),
    (S.CIRCULATING, S.ON_SPEC): ("normal", "V-101", "level", 0.2),
    (S.ON_SPEC, S.CIRCULATING): ("normal", "P-101", "speed", float("nan")),
    (S.SHUTTING_DOWN, S.COLD): ("cold", "K-101", "load", 0.5),
}


@pytest.mark.parametrize("edge", sorted(BREAKS))
def test_each_transition_requires_its_real_conditions(edge, normal, cold):
    source, target = edge
    which, tag, variable, bad = BREAKS[edge]
    holds = {"normal": normal, "cold": cold}[which]
    machine = machine_at(source, holds)

    assert machine.advance(target, with_reading(holds, tag, variable, bad)) != ()
    assert machine.state is source
    assert machine.advance(target, holds) == ()
    assert machine.state is target


def test_every_gated_edge_is_covered():
    assert set(BREAKS) == set(GATES)


def test_every_condition_on_an_edge_is_needed(normal):
    machine = machine_at(S.PRESSURISED, normal)
    slow = with_reading(normal, "P-101", "speed", 0.5)
    reasons = machine.advance(S.CIRCULATING, slow)

    assert len(reasons) == 1
    assert "P-101.speed" in reasons[0]
    assert machine.state is S.PRESSURISED


def test_cold_shutdown_reads_cold_and_normal_operation_on_spec(normal, cold):
    assert machine_at(S.SHUTTING_DOWN, cold).blocked(S.COLD, cold) == ()
    assert machine_at(S.CIRCULATING, normal).blocked(S.ON_SPEC, normal) == ()
    assert machine_at(S.CIRCULATING, cold).blocked(S.ON_SPEC, cold) != ()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (S.COLD, S.ON_SPEC),
        (S.COLD, S.CIRCULATING),
        (S.COLD, S.SHUTTING_DOWN),
        (S.PURGED, S.CIRCULATING),
        (S.PRESSURISED, S.PURGED),
        (S.ON_SPEC, S.COLD),
        (S.SHUTTING_DOWN, S.ON_SPEC),
        (S.SHUTTING_DOWN, S.PURGED),
        (S.COLD, S.COLD),
    ],
)
def test_illegal_transitions_are_rejected(source, target, normal):
    machine = machine_at(source, normal)

    with pytest.raises(IllegalTransition):
        machine.advance(target, normal)

    assert machine.state is source


def test_a_plant_that_meets_a_later_gate_still_cannot_skip_ahead(normal):
    machine = PlantStateMachine(normal, GATES)

    with pytest.raises(IllegalTransition):
        machine.advance(S.ON_SPEC, normal)


@pytest.mark.parametrize("source", [S.PURGED, S.PRESSURISED, S.CIRCULATING, S.ON_SPEC])
def test_a_shutdown_may_begin_from_any_running_state_whatever_the_plant_reads(source, cold):
    machine = machine_at(source, cold)

    assert machine.advance(S.SHUTTING_DOWN, with_reading(cold, "P-101", "speed", float("nan"))) == ()
    assert machine.state is S.SHUTTING_DOWN


def test_a_missing_reading_blocks(normal):
    row = {k: v for k, v in normal.equipment["V-101"].items() if k != "pressure"}
    lost = dataclasses.replace(
        normal,
        equipment=MappingProxyType({**normal.equipment, "V-101": MappingProxyType(row)}),
    )
    machine = machine_at(S.PURGED, normal)

    assert "not published" in machine.advance(S.PRESSURISED, lost)[0]
    assert machine.state is S.PURGED


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "high", None, True])
def test_a_non_numeric_or_non_finite_reading_blocks(value, normal):
    machine = machine_at(S.PURGED, normal)

    assert machine.advance(S.PRESSURISED, with_reading(normal, "V-101", "pressure", value)) != ()
    assert machine.state is S.PURGED


def test_blocked_is_a_pure_query(cold, normal):
    machine = machine_at(S.COLD, normal)

    assert machine.blocked(S.PURGED, normal) == ()
    assert machine.state is S.COLD


def test_the_machine_refuses_a_gate_on_a_move_that_is_not_a_transition(normal):
    with pytest.raises(ValueError, match="not a transition"):
        PlantStateMachine(normal, {**GATES, (S.COLD, S.ON_SPEC): ["V-101.level >= 0"]})


def test_the_machine_refuses_an_edge_with_no_gate(normal):
    gates = {edge: c for edge, c in GATES.items() if edge != (S.PURGED, S.PRESSURISED)}

    with pytest.raises(ValueError, match="no condition"):
        PlantStateMachine(normal, gates)

    with pytest.raises(ValueError, match="no condition"):
        PlantStateMachine(normal, {**GATES, (S.PURGED, S.PRESSURISED): []})


def test_the_machine_refuses_unknown_devices_and_malformed_conditions_together(normal):
    gates = {
        **GATES,
        (S.COLD, S.PURGED): ["X-999.level >= 0"],
        (S.PURGED, S.PRESSURISED): ["not a condition"],
    }

    with pytest.raises(ValueError, match="2 problem") as raised:
        PlantStateMachine(normal, gates)

    assert "X-999" in str(raised.value)


def test_an_unpublished_variable_is_warned_about_at_construction(normal):
    gates = {**GATES, (S.COLD, S.PURGED): ["V-101.nonsense >= 0"]}

    with pytest.warns(UserWarning, match="will always block"):
        PlantStateMachine(normal, gates)


def test_the_graph_has_no_edge_into_cold_except_from_shutting_down():
    assert {edge for edge in TRANSITIONS if edge[1] is S.COLD} == {(S.SHUTTING_DOWN, S.COLD)}
