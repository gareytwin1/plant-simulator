"""
Startup and shutdown sequences (T12-4).

The build-plan tests come first: a full cold start with no alarm or trip, an
out-of-order step blocked by permissives, and a normal shutdown that ends
restartable. Each drives the real reference plant from a named initial
condition (T12-2), stepping the engine between sequence scans, and acts only
through `apply_action`, the C5 path an operator uses.

The cold start begins from `cold_shutdown`, whose drained vessel already reads
lololo on V-101.level (tests/test_initial_conditions.py's INTENDED) and meets
LSLL-101's trip condition. "No alarm or trip" therefore means the start never
enters an envelope band, or meets an interlock condition, that the cold plant
was not already in, and the standing ones clear and never return. Interlocks
are not live in a session, so the test reads their conditions itself, and only
against what a device publishes: PSHH-101's K-101.discharge_pressure is not
published, so it cannot be checked here.
"""

import dataclasses
import json
import textwrap
import warnings
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from app.api.action import ACTIONS, apply_action
from app.engine.engine import Engine
from app.engine.persistence import capture_state, restore_state
from app.plant.loader import load_plant_file
from app.plant.sequences import Sequencer, SequenceRun, load_sequences
from app.plant.states import PlantState as S
from app.safety.interlocks import Condition
from app.scoring.actionlog import ActionLog

pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

CONFIG = Path(__file__).resolve().parent.parent / "config"
OLEFINS = CONFIG / "plants" / "olefins_lite.yaml"
SEQUENCES = CONFIG / "sequences" / "olefins_lite.yaml"
LIBRARY = CONFIG / "initial_conditions"

DT = 1.0
HORIZON = 10_000
COLD_READINGS = {"V-101.level": "lololo", "LSLL-101": "met"}
INTERLOCKS = {
    entry["tag"]: Condition.parse(entry["condition"])
    for entry in yaml.safe_load(OLEFINS.read_text())["interlocks"]
}


class Plant:
    """An engine, its action log, and the sequences file loaded against it."""

    def __init__(self, state):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.engine = Engine.from_plant(load_plant_file(OLEFINS))

        restore_state(self.engine, state)
        self.log = ActionLog()
        self.snapshot = self.engine.step(0.0)
        self.sequences = load_sequences(SEQUENCES, self.snapshot, operable(self.engine))

    def act(self, tag, action, value):
        apply_action(self.engine.equipment, self.log, self.engine.clock.sim_time, tag, action, value)

    def run(self, name, machine):
        return SequenceRun(self.sequences.procedures[name], machine, self.act)

    def drive(self, run, watch=None):
        """Scan `run` (or a `Sequencer`'s current run) and step the engine
        until it is done."""
        for _ in range(HORIZON):
            run.update(self.snapshot)

            if (run.run if isinstance(run, Sequencer) else run).done:
                return

            self.snapshot = self.engine.step(DT)

            if watch is not None:
                watch(self.snapshot)

        pytest.fail(f"sequence still running after {HORIZON} steps")

    def actions(self):
        return [(event.sim_time, event.message) for event in self.log.events]


def operable(engine):
    return {
        tag: {spec.method: spec.takes_value for spec in ACTIONS.get(type(device), ())}
        for tag, device in engine.equipment.items()
    }


def condition(name):
    return json.loads((LIBRARY / f"{name}.json").read_text())


def readings(snapshot):
    """Every envelope band entered and every interlock condition met."""
    met = {
        tag: "met"
        for tag, condition in INTERLOCKS.items()
        if isinstance(value := snapshot.equipment[condition.tag].get(condition.variable), float)
        and condition.is_met(value)
    }

    return {**{key: row["band"] for key, row in snapshot.envelope.items()}, **met}


@pytest.fixture(scope="module")
def cold_start():
    """One full cold start, with every band it read along the way."""
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot)
    seen = [readings(plant.snapshot)]

    plant.drive(plant.run("cold_start", machine), watch=lambda s: seen.append(readings(s)))

    return plant, machine, seen


@pytest.fixture(scope="module")
def on_spec(cold_start):
    plant, _, _ = cold_start

    return capture_state(plant.engine)


def test_a_full_cold_start_reaches_on_spec(cold_start):
    plant, machine, _ = cold_start
    row = plant.snapshot.equipment["V-101"]

    assert machine.state is S.ON_SPEC
    assert row["level"] == pytest.approx(0.5, abs=0.05)
    assert row["pressure"] == pytest.approx(200.0, abs=10.0)
    assert plant.snapshot.equipment["K-101"]["load"] == pytest.approx(1.0)
    assert plant.snapshot.equipment["P-101"]["speed"] == pytest.approx(1.0)


def test_a_cold_start_raises_no_alarm_or_trip(cold_start):
    _, _, seen = cold_start
    cleared = next((index for index, reading in enumerate(seen) if not reading), None)

    assert seen[0] == COLD_READINGS
    assert cleared is not None
    assert all(set(reading) <= set(COLD_READINGS) for reading in seen)
    assert all(not reading for reading in seen[cleared:])


def test_a_cold_start_takes_its_steps_in_order(cold_start):
    plant, _, _ = cold_start
    messages = [message for _, message in plant.actions()]

    assert messages == [
        "LV-101 set_position_target 0.1",
        "P-101 set_speed_target 1.0",
        "P-101 start",
        "K-101 set_load_target 1.0",
        "K-101 start",
        "LV-101 set_position_target 0.5",
    ]


def test_a_cold_start_is_deterministic(cold_start):
    plant, _, _ = cold_start
    again = Plant(condition("cold_shutdown"))
    again.drive(again.run("cold_start", again.sequences.machine(again.snapshot)))

    assert again.actions() == plant.actions()
    assert capture_state(again.engine) == capture_state(plant.engine)


def test_a_step_out_of_order_is_blocked_and_does_nothing():
    plant = Plant(condition("cold_shutdown"))
    run = plant.run("cold_start", plant.sequences.machine(plant.snapshot))

    reasons = run.request("start_compressor", plant.snapshot)

    assert reasons == ("step 'start_compressor' runs from ['pressurised'], the plant is cold",)
    assert run.active is None
    assert not plant.log.events


def test_a_step_whose_permissives_fail_is_blocked_with_every_reason():
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot, state=S.PRESSURISED)
    run = plant.run("cold_start", machine)

    reasons = run.request("start_compressor", plant.snapshot)

    assert reasons == (
        "permissive P-101.speed >= 0.99 not satisfied: P-101.speed reads 0",
        "permissive V-101.level >= 0.3 not satisfied: V-101.level reads 0",
    )
    assert not plant.log.events
    assert not plant.engine.equipment["K-101"].running


def test_a_sequence_waits_at_a_step_until_its_permissives_hold():
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot, state=S.PRESSURISED)
    run = SequenceRun(plant.sequences.procedures["cold_start"][2:], machine, plant.act)
    filled = with_reading(plant.snapshot, "V-101", "level", 0.3, plant.snapshot.sim_time)
    ready = with_reading(filled, "P-101", "speed", 1.0, plant.snapshot.sim_time)

    for snapshot in (plant.snapshot, filled):
        assert run.update(snapshot)
        assert run.active is None
        assert not plant.log.events

    assert run.update(ready) == ()
    assert run.active == "start_compressor"
    assert [message for _, message in plant.actions()] == ["K-101 set_load_target 1.0", "K-101 start"]


def test_a_normal_shutdown_ends_cold_and_restartable(on_spec):
    plant = Plant(on_spec)
    machine = plant.sequences.machine(plant.snapshot, state=S.ON_SPEC)

    plant.drive(plant.run("normal_shutdown", machine))

    assert machine.state is S.COLD
    assert not plant.engine.equipment["K-101"].running
    assert not plant.engine.equipment["P-101"].running

    plant.drive(plant.run("cold_start", machine))

    assert machine.state is S.ON_SPEC


def test_a_normal_shutdown_unloads_the_compressor_before_stopping_it(on_spec):
    plant = Plant(on_spec)
    machine = plant.sequences.machine(plant.snapshot, state=S.ON_SPEC)
    loads = {}

    def watch(snapshot):
        loads[snapshot.sim_time] = snapshot.equipment["K-101"]["load"]

    plant.drive(plant.run("normal_shutdown", machine), watch=watch)
    times = {message: time for time, message in plant.actions()}

    assert loads[times["K-101 stop"]] == pytest.approx(0.0)
    assert times["K-101 set_load_target 0.0"] < times["K-101 stop"] < times["P-101 stop"]


def test_an_emergency_shutdown_stops_everything_in_one_scan(on_spec):
    plant = Plant(on_spec)
    machine = plant.sequences.machine(plant.snapshot, state=S.ON_SPEC)
    run = plant.run("emergency_shutdown", machine)

    run.update(plant.snapshot)

    assert machine.state is S.SHUTTING_DOWN
    assert [message for _, message in plant.actions()] == [
        "K-101 stop",
        "P-101 stop",
        "LV-101 set_position_target 0.1",
    ]
    assert len({time for time, _ in plant.actions()}) == 1
    assert plant.snapshot.equipment["K-101"]["load"] == pytest.approx(1.0)

    plant.drive(run)

    assert machine.state is S.COLD


@pytest.mark.parametrize("state", [S.PURGED, S.PRESSURISED, S.CIRCULATING, S.ON_SPEC])
def test_an_emergency_shutdown_runs_from_any_running_state(on_spec, state):
    plant = Plant(on_spec)
    machine = plant.sequences.machine(plant.snapshot, state=state)

    assert plant.run("emergency_shutdown", machine).update(plant.snapshot) == ()
    assert machine.state is S.SHUTTING_DOWN


def test_a_normal_shutdown_does_not_run_from_a_plant_still_starting(on_spec):
    plant = Plant(on_spec)
    machine = plant.sequences.machine(plant.snapshot, state=S.PRESSURISED)

    assert plant.run("normal_shutdown", machine).update(plant.snapshot)
    assert machine.state is S.PRESSURISED
    assert not plant.log.events


def test_an_emergency_shutdown_overrides_a_normal_one_part_way_through(on_spec):
    plant = Plant(on_spec)
    sequencer = Sequencer(plant.sequences, plant.sequences.machine(plant.snapshot, state=S.ON_SPEC), plant.act)
    sequencer.start("normal_shutdown")
    normal = sequencer.run

    for _ in range(5):
        sequencer.update(plant.snapshot)
        plant.snapshot = plant.engine.step(DT)

    assert sequencer.machine.state is S.SHUTTING_DOWN
    assert plant.engine.equipment["P-101"].running

    sequencer.start("emergency_shutdown")
    before = len(plant.log)

    plant.drive(sequencer)

    assert normal.aborted
    assert sequencer.machine.state is S.COLD
    assert [message for _, message in plant.actions()[before:]] == [
        "K-101 stop",
        "P-101 stop",
        "LV-101 set_position_target 0.1",
    ]


def starting(state):
    """A plant part way through its cold start, the start still running."""
    plant = Plant(condition("cold_shutdown"))
    sequencer = Sequencer(plant.sequences, plant.sequences.machine(plant.snapshot), plant.act)
    sequencer.start("cold_start")

    for _ in range(HORIZON):
        if sequencer.machine.state is state:
            return plant, sequencer

        sequencer.update(plant.snapshot)
        plant.snapshot = plant.engine.step(DT)

    pytest.fail(f"cold start not {state} after {HORIZON} steps")


def test_a_start_cut_off_by_an_emergency_shutdown_takes_no_further_action():
    plant, sequencer = starting(S.PRESSURISED)
    start = sequencer.run

    assert sequencer.start("emergency_shutdown") == ()

    before = len(plant.log)
    plant.drive(sequencer)

    assert start.aborted
    assert sequencer.machine.state is S.COLD
    assert len(plant.log) == before + 3

    for _ in range(100):
        start.update(plant.snapshot)
        plant.snapshot = plant.engine.step(DT)

    assert len(plant.log) == before + 3


@pytest.mark.parametrize(
    ("state", "procedure", "reason"),
    [
        (S.PRESSURISED, "normal_shutdown", "starts from ['circulating', 'on_spec'], the plant is pressurised"),
        (S.COLD, "emergency_shutdown", "the plant is cold"),
    ],
)
def test_a_procedure_that_cannot_start_leaves_the_current_run_in_place(state, procedure, reason):
    _, sequencer = starting(state)
    current = sequencer.run

    (refusal,) = sequencer.start(procedure)

    assert reason in refusal
    assert sequencer.run is current
    assert sequencer.procedure == "cold_start"
    assert not current.aborted


def test_a_sequencer_refuses_an_unknown_procedure(cold):
    sequencer = Sequencer(cold.sequences, cold.sequences.machine(cold.snapshot), cold.act)

    with pytest.raises(KeyError, match="no procedure 'warm_start'"):
        sequencer.start("warm_start")


def test_an_action_that_raises_is_never_retried_with_the_ones_before_it():
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot, state=S.PURGED)
    taken = []

    def act(tag, action, value):
        taken.append(f"{tag}.{action}")

        if action == "start":
            raise RuntimeError("refused")

    run = SequenceRun(plant.sequences.procedures["cold_start"], machine, act)

    lined_up = with_reading(plant.snapshot, "LV-101", "position", 0.1, plant.snapshot.sim_time)

    with pytest.raises(RuntimeError):
        run.request("fill", lined_up)

    assert run.request("fill", lined_up) == ("step 'fill' is still running",)
    assert taken == ["P-101.set_speed_target", "P-101.start"]


def with_reading(snapshot, tag, variable, value, sim_time):
    row = {**snapshot.equipment[tag], variable: value}
    equipment = MappingProxyType({**snapshot.equipment, tag: MappingProxyType(row)})

    return dataclasses.replace(snapshot, equipment=equipment, sim_time=sim_time)


def test_a_hold_restarts_when_its_condition_breaks():
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot)
    run = plant.run("cold_start", machine)
    base = plant.snapshot.sim_time

    def at(offset, position):
        return with_reading(plant.snapshot, "LV-101", "position", position, base + offset)

    run.update(at(0, 0.5))

    assert run.active == "line_up"

    for offset, position in ((1, 0.1), (8, 0.1), (9, 0.5), (10, 0.1), (19, 0.1)):
        run.update(at(offset, position))
        assert machine.state is S.COLD

    run.update(at(20, 0.1))

    assert machine.state is S.PURGED
    assert run.active is None


VALID = """
gates:
  - {from: cold, to: purged, when: ["LV-101.position <= 0.11"]}
  - {from: purged, to: pressurised, when: ["V-101.level >= 0.3"]}
  - {from: pressurised, to: circulating, when: ["K-101.load >= 0.99"]}
  - {from: circulating, to: on_spec, when: ["V-101.level >= 0.45"]}
  - {from: on_spec, to: circulating, when: ["V-101.level >= 0.0"]}
  - {from: shutting_down, to: cold, when: ["K-101.load <= 0.0"]}
sequences:
  start:
    - step: line_up
      in: [cold]
      actions: ["LV-101.set_position_target 0.1"]
      hold: {when: ["LV-101.position <= 0.11"], for_s: 5}
      advance: purged
"""


@pytest.fixture(scope="module")
def cold():
    return Plant(condition("cold_shutdown"))


def load_text(tmp_path, cold, text):
    path = tmp_path / "sequences.yaml"
    path.write_text(textwrap.dedent(text))

    return load_sequences(path, cold.snapshot, operable(cold.engine))


def test_the_reference_sequences_file_holds_all_three_procedures(cold):
    assert set(cold.sequences.procedures) == {"cold_start", "normal_shutdown", "emergency_shutdown"}


def test_a_valid_file_loads(tmp_path, cold):
    loaded = load_text(tmp_path, cold, VALID)

    assert [step.name for step in loaded.procedures["start"]] == ["line_up"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("sequences:", "extra: 1\nsequences:", "unknown key(s) ['extra']"),
        ("      advance: purged", "      advance: purged\n      speed: 2", "unknown key(s) ['speed']"),
        ('"LV-101.set_position_target 0.1"', '"XV-999.set_position_target 0.1"', "unknown device 'XV-999'"),
        ('"LV-101.set_position_target 0.1"', '"LV-101.start"', "LV-101 allows only ['set_position_target']"),
        ('"LV-101.set_position_target 0.1"', '"LV-101.set_position_target"', "requires a value"),
        ('"LV-101.set_position_target 0.1"', '"LV-101.set_position_target nan"', "finite number"),
        ('"LV-101.set_position_target 0.1"', '"LV-101 open"', "is not '<tag>.<action> [value]'"),
        ('["LV-101.position <= 0.11"], for_s', '["LV-101.travel <= 0.11"], for_s', "LV-101.travel is not published"),
        ('["LV-101.position <= 0.11"], for_s', '["LV-101.position ~ 0.11"], for_s', "malformed"),
        ("for_s: 5", "for_s: -5", "non-negative"),
        ('when: ["LV-101.position <= 0.11"], for_s', "when: [], for_s", "needs at least one condition"),
        ("advance: purged", "advance: on_spec", "which ['cold'] cannot reach"),
        ("sequences:\n  start:", "sequences:\n  - start:", "must be a mapping of name to steps"),
        ("      advance: purged\n", "      advance: purged\n    - {step: line_up, in: [purged]}\n", "repeats step(s) ['line_up']"),
        ("in: [cold]", "in: [frozen]", "'frozen' is not a plant state"),
        ("in: [cold]", "in: []", "non-empty list of plant states"),
        ('when: ["K-101.load <= 0.0"]', 'when: ["XV-999.load <= 0.0"]', "unknown device 'XV-999'"),
        ('when: ["K-101.load <= 0.0"]', "when: []", "has no condition to gate it"),
        ("- {from: shutting_down", "- {from: on_spec, to: shutting_down, when: ['K-101.load <= 0']}\n  - {from: shutting_down", "never refused"),
    ],
)
def test_a_bad_file_is_rejected_by_name(tmp_path, cold, old, new, message):
    assert old in VALID

    with pytest.raises(ValueError, match="rejected") as error:
        load_text(tmp_path, cold, VALID.replace(old, new, 1))

    assert message in str(error.value)


@pytest.mark.parametrize("text", ["- 1\n", ""])
def test_a_file_that_is_not_a_mapping_is_rejected_on_that_alone(tmp_path, cold, text):
    with pytest.raises(ValueError, match=r"rejected, 1 problem\(s\):\n  the file must be a mapping$"):
        load_text(tmp_path, cold, text)


def test_a_refused_gate_is_not_also_reported_as_ungated(tmp_path, cold):
    text = VALID.replace('when: ["LV-101.position <= 0.11"]}', 'when: "LV-101.position <= 0.11"}', 1)

    with pytest.raises(ValueError, match="when must be a list") as error:
        load_text(tmp_path, cold, text)

    assert "cold -> purged has no condition" not in str(error.value)


def test_a_gate_naming_no_valid_edge_is_not_followed_by_ungated_reports(tmp_path, cold):
    text = VALID.replace("{from: purged, to: pressurised", "{from: purgd, to: pressurised", 1)

    with pytest.raises(ValueError, match=r"rejected, 1 problem\(s\):\n  gate 2: 'purgd' is not a plant state"):
        load_text(tmp_path, cold, text)


def test_a_half_named_gate_hides_only_the_edges_it_may_have_meant(tmp_path, cold):
    text = VALID.replace("{from: purged, to: pressurised", "{from: purgd, to: pressurised", 1).replace(
        '  - {from: on_spec, to: circulating, when: ["V-101.level >= 0.0"]}\n', ""
    )

    with pytest.raises(ValueError, match=r"2 problem\(s\)") as error:
        load_text(tmp_path, cold, text)

    assert "'purgd' is not a plant state" in str(error.value)
    assert "on_spec -> circulating has no condition to gate it" in str(error.value)


def test_a_bad_gate_condition_does_not_hide_the_shape_checks(tmp_path, cold):
    text = VALID.replace('"V-101.level >= 0.3"', '"V-101.depth >= 0.3"').replace(
        '  - {from: on_spec, to: circulating, when: ["V-101.level >= 0.0"]}\n', ""
    )

    with pytest.raises(ValueError, match=r"2 problem\(s\)") as error:
        load_text(tmp_path, cold, text)

    assert "V-101.depth is not published" in str(error.value)
    assert "on_spec -> circulating has no condition to gate it" in str(error.value)


def test_every_problem_is_reported_together(tmp_path, cold):
    text = VALID.replace("LV-101.set_position_target 0.1", "XV-999.open").replace("for_s: 5", "for_s: -1")

    with pytest.raises(ValueError, match=r"2 problem\(s\)"):
        load_text(tmp_path, cold, text)
