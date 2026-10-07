"""Operator view of the snapshot - T16-13."""

import inspect
import json

import pytest
from flask import Flask

from app import main
from app.api import visibility
from app.api.stream import create_stream_blueprint
from app.api.visibility import VISIBLE, operator_view
from app.disturbances.malfunction import WRITABLE
from app.equipment import (
    compressor,
    exchanger,
    furnace,
    pump,
    relief,
    valve,
    vessel,
)
from app.equipment.base import Equipment
from app.training.session import TrainingSession


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit", "ignore:interlock")

FAULT_FIELDS = {"stuck", "action_reversed", "signal_ok", "effective_capacity"}


def equipment_classes():
    return {
        cls
        for module in (compressor, exchanger, furnace, pump, relief, valve, vessel)
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, Equipment) and cls is not Equipment and cls.__module__ == module.__name__
    }


@pytest.fixture
def session():
    made = TrainingSession()
    yield made
    made.end()


def free_equipment(session):
    return session.free.engine.equipment


def test_every_equipment_class_has_a_visible_entry():
    assert equipment_classes() <= set(VISIBLE)


def test_no_visible_field_is_malfunction_writable():
    for cls, fields in VISIBLE.items():
        assert not fields & WRITABLE[cls], cls.__name__


def test_visible_fields_are_published_by_the_class_they_belong_to(session):
    rows = session.snapshot().equipment

    # A vessel publishes pressure only while it holds gas.
    conditional = {vessel.Vessel: {"pressure"}}

    for tag, device in free_equipment(session).items():
        required = VISIBLE[type(device)] - conditional.get(type(device), set())

        assert required <= set(rows[tag]), tag


def test_an_operator_view_row_holds_exactly_its_classs_visible_fields(session):
    view = session.operator_view(session.snapshot())

    for tag, device in free_equipment(session).items():
        published = set(session.snapshot().equipment[tag])

        assert set(view["equipment"][tag]) == VISIBLE[type(device)] & published, tag
        assert not FAULT_FIELDS & set(view["equipment"][tag])


def test_a_class_with_no_entry_and_an_unknown_tag_show_an_empty_row(session, monkeypatch):
    monkeypatch.delitem(visibility.VISIBLE, type(free_equipment(session)["LV-101"]))

    view = operator_view(session.snapshot(), free_equipment(session))
    unknown = operator_view(session.snapshot(), {})

    assert view["equipment"]["LV-101"] == {}
    assert view["equipment"]["P-101"] != {}
    assert all(row == {} for row in unknown["equipment"].values())


def test_other_sections_pass_through_unchanged(session):
    snapshot = session.snapshot()
    full = snapshot.as_dict()

    view = session.operator_view(snapshot)

    for section in full.keys() - {"equipment"}:
        assert view[section] == full[section], section


def test_a_stuck_valve_and_a_capacity_malfunction_change_nothing_in_the_view_but_values(session):
    valve_device = free_equipment(session)["LV-101"]
    before = session.operator_view(session.snapshot())["equipment"]["LV-101"]

    valve_device.stuck = True
    valve_device.capacity = valve_device.capacity * 0.5
    session.training_scheduler.step_once()
    after = session.operator_view(session.snapshot())["equipment"]["LV-101"]

    assert set(after) == set(before)
    assert not FAULT_FIELDS & set(after)


def test_the_full_snapshot_still_carries_the_fault_fields(session):
    row = session.snapshot().equipment["LV-101"]

    assert FAULT_FIELDS <= set(row)


def test_the_snapshot_route_serves_the_operator_view():
    client = main.app.test_client()

    body = client.get("/api/snapshot").get_json()

    for tag, row in body["equipment"].items():
        assert not FAULT_FIELDS & set(row), tag
    assert set(body["equipment"]["LV-101"]) == VISIBLE[valve.ControlValve]


def test_a_stream_event_is_the_operator_view(session):
    app = Flask(__name__)
    app.register_blueprint(
        create_stream_blueprint(
            lambda: session.training_scheduler,
            0.01,
            get_view=lambda: session.operator_view,
        ),
    )

    response = app.test_client().get("/api/stream")
    try:
        chunk = next(iter(response.response))
    finally:
        response.close()

    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    line = next(line for line in text.split("\n") if line.startswith("data: "))
    equipment = json.loads(line[len("data: "):])["equipment"]

    assert set(equipment["LV-101"]) == VISIBLE[valve.ControlValve]
    assert not FAULT_FIELDS & {name for row in equipment.values() for name in row}
