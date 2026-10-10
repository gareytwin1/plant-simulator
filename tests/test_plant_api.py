"""Plant description - T20-2, contract C5's GET /api/plant."""

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from flask import Flask

from app import config, main
from app.api.action import ACTIONS
from app.api.plant import create_plant_blueprint
from app.api.visibility import KIND
from app.plant.loader import DEVICE_TYPES
from app.scenarios.runner import ScenarioLibrary, scenario_key
from app.training.session import TrainingSession
from tests.test_training_session import scheduler_workers

ROOT = Path(__file__).resolve().parent.parent
GRAPHICS = ROOT / "static" / "graphics"
PLANTS = ROOT / "config" / "plants"

# A second plant, so following the shown plant can be seen: olefins_lite's
# devices under another file name, with no graphic of its own.
OTHER = "olefins_copy"

SCENARIO = (
    "id: copy-trip\n"
    f"plant: {OTHER}\n"
    "initial_condition: {condition: feed_pump_trip}\n"
    "objectives:\n"
    "  - id: recover-level\n"
    "    success: {condition: 'V-101.level >= 0.25', hold_duration_s: 20}\n"
    "time_limit_s: 900\n"
    "difficulty: easy\n"
    "seed: 7\n"
)


@pytest.fixture
def library(tmp_path):
    plants = tmp_path / "plants"
    scenarios = tmp_path / "scenarios"
    plants.mkdir()
    scenarios.mkdir()
    shutil.copy(PLANTS / f"{config.FREE_PLAY_PLANT}.yaml", plants)

    other = yaml.safe_load((PLANTS / f"{config.FREE_PLAY_PLANT}.yaml").read_text())
    other["equipment"][0]["service"] = "Feed pump"
    other["controllers"][0]["service"] = "Drum pressure controller"
    (plants / f"{OTHER}.yaml").write_text(yaml.safe_dump(other))
    (scenarios / "copy-trip.yaml").write_text(SCENARIO)

    return ScenarioLibrary(scenarios=scenarios, plants=plants)


@pytest.fixture
def session(library):
    made = TrainingSession(library)

    yield made

    made.end()


@pytest.fixture
def client(session):
    app = Flask(__name__, static_folder=str(ROOT / "static"))
    app.register_blueprint(create_plant_blueprint(session.plant_description, GRAPHICS))

    return app.test_client()


def described(client):
    response = client.get("/api/plant")
    assert response.status_code == 200

    return response.get_json()


def test_the_description_follows_the_plant_the_snapshot_shows(session, client):
    assert described(client)["plant"] == config.FREE_PLAY_PLANT

    session.load("copy-trip")
    assert described(client)["plant"] == OTHER

    session.unload()
    assert described(client)["plant"] == config.FREE_PLAY_PLANT


def test_a_plant_with_a_graphic_names_it_and_one_without_names_none(session, client):
    assert described(client)["graphic"] == f"/static/graphics/{config.FREE_PLAY_PLANT}.svg"
    assert client.get(described(client)["graphic"]).status_code == 200

    session.load("copy-trip")

    assert described(client)["graphic"] is None


def test_each_device_lists_its_operator_visible_points_and_its_branchs_stream(session, client):
    body = described(client)
    view = session.operator_view(session.snapshot())
    branches = {
        branch.device.tag: branch.id
        for topology in session.free.engine.topologies.values()
        for branch in topology.branches.values()
    }

    assert set(body["equipment"]) == set(view["equipment"])
    for tag, entry in body["equipment"].items():
        expected = {f"{tag}.{field}" for field in view["equipment"][tag]}
        if tag in branches:
            expected.add(f"{branches[tag]}.flow")
        assert set(entry["points"]) == expected, tag
        assert len(entry["points"]) == len(expected), tag


def test_a_coupling_device_lists_only_its_own_points(client):
    assert described(client)["equipment"]["V-101"]["points"] == ["V-101.level", "V-101.pressure"]


def test_each_device_lists_its_actions_and_kind(session, client):
    equipment = described(client)["equipment"]

    for tag, entry in equipment.items():
        device = session.free.engine.equipment[tag]
        assert entry["kind"] == KIND[type(device)]
        if entry["driven_by"] is None:
            assert entry["actions"] == [spec.method for spec in ACTIONS.get(type(device), ())]


def test_a_device_a_loop_drives_says_so_and_lists_no_action(session, client):
    entry = described(client)["equipment"]["PV-101"]

    assert entry["driven_by"] == "PIC-101"
    assert entry["actions"] == []
    with pytest.raises(ValueError, match="driven by loop PIC-101"):
        session.act("PV-101", "set_position_target", 0.4)
    assert all(
        other["driven_by"] is None
        for tag, other in described(client)["equipment"].items()
        if tag != "PV-101"
    )


def test_each_loop_names_its_pv_point_and_its_out_tag(client):
    body = described(client)

    assert body["controllers"] == {"PIC-101": {"service": None, "pv": "N-201.pressure", "out": "PV-101"}}


def test_a_service_text_reaches_the_description_and_an_entry_without_one_reads_null(session, client):
    assert all(entry["service"] is None for entry in described(client)["equipment"].values())

    session.load("copy-trip")
    body = described(client)

    assert body["equipment"]["P-101"]["service"] == "Feed pump"
    assert body["equipment"]["K-101"]["service"] is None
    assert body["controllers"]["PIC-101"]["service"] == "Drum pressure controller"


def test_an_interlock_lists_the_devices_it_acts_on_and_never_its_condition(client):
    body = described(client)
    text = json.dumps(body)

    assert body["interlocks"] == {
        "LSHH-101": {"devices": ["P-101"]},
        "LSLL-101": {"devices": ["LV-101"]},
        "PSHH-101": {"devices": ["K-101"]},
    }
    assert ">=" not in text and "<=" not in text
    assert "condition" not in text


@pytest.mark.parametrize("scenario", [entry.id for entry in ScenarioLibrary().catalogue()])
def test_a_loaded_scenario_names_no_fault_malfunction_or_scenario_id(scenario):
    session = TrainingSession()
    try:
        app = Flask(__name__, static_folder=str(ROOT / "static"))
        app.register_blueprint(create_plant_blueprint(session.plant_description, GRAPHICS))
        session.load(scenario)
        session.start()
        text = app.test_client().get("/api/plant").get_data(as_text=True)
    finally:
        session.end()

    document = ScenarioLibrary().scenario(scenario)
    hidden = [scenario, scenario_key(scenario), "malfunction", "fault", "condition"]
    hidden += [entry["parameter"] for entry in document.get("malfunctions", [])]
    hidden += [entry["condition"] for entry in yaml.safe_load((PLANTS / f"{document['plant']}.yaml").read_text())["interlocks"]]

    for word in hidden:
        assert word.lower() not in text.lower(), word


def test_the_route_never_starts_a_scheduler():
    client = main.app.test_client()

    response = client.get("/api/plant")
    session = main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)

    assert response.status_code == 200
    assert response.get_json()["plant"] == config.FREE_PLAY_PLANT
    assert response.get_json()["graphic"] == f"/static/graphics/{config.FREE_PLAY_PLANT}.svg"
    assert session.training_scheduler.running is False
    assert not scheduler_workers()


# What the plant description replaces: nothing in the browser's code names a
# tag, a device class or a device action, so a new plant is configuration and
# a graphic, never JavaScript. Comments may cite examples; code may not.

SCENARIO_VERBS = {"start"}  # POST /api/scenario/start, the scenario bar's own verb


def js_code(path):
    """`path` with its comments removed. An approximation, not a tokenizer: a
    `//` right after a colon or a quote is kept (a URL in a string), and a `//`
    inside a string after any other character ends the line early. Neither
    occurs in static/js today; a parser would be the fix if one does."""
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.DOTALL)
    return re.sub(r"(?<![:\"'])//.*$", "", text, flags=re.MULTILINE)


def test_no_browser_script_names_a_tag_a_device_class_or_a_device_action():
    tags = set()
    for plant in PLANTS.glob("*.yaml"):
        document = yaml.safe_load(plant.read_text())
        for section in ("equipment", "controllers", "interlocks"):
            tags |= {entry["tag"] for entry in document.get(section, [])}
    classes = {device_type.__name__ for device_type in DEVICE_TYPES.values()}
    actions = {spec.method for specs in ACTIONS.values() for spec in specs} - SCENARIO_VERBS

    assert tags and classes and actions
    for script in sorted((ROOT / "static" / "js").glob("*.js")):
        code = js_code(script)
        for name in sorted(tags | classes):
            assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", code), f"{script.name} names {name}"
        # An action is a request's value, so only a string literal sends one;
        # `stop` as a function name sends nothing.
        for name in sorted(actions):
            assert not re.search(rf"[\"']{re.escape(name)}[\"']", code), f"{script.name} names {name}"
