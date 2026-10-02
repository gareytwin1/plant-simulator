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
import itertools
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
from app.safety.actions import number
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
# Only the low side of the vessel's level, easing off as it fills: a high band
# is not in this table and fails the lookup.
SEVERITY = {"lololo": 3, "lolo": 2, "lo": 1, "met": 1}
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
    met = {}

    for tag, condition in INTERLOCKS.items():
        row = snapshot.equipment[condition.tag]

        if condition.variable not in row:
            continue

        value = number(row[condition.variable])
        assert value is not None, f"{tag} reads {row[condition.variable]!r}"

        if condition.is_met(value):
            met[tag] = "met"

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
    assert all(not reading for reading in seen[cleared:])

    for before, after in itertools.pairwise(seen):
        assert set(after) <= set(before), (before, after)
        assert all(SEVERITY[after[key]] <= SEVERITY[before[key]] for key in after)


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

    assert reasons == ("step 'start_compressor' is not next, 'line_up' is",)
    assert run.active is None
    assert not plant.log.events


def test_a_shutdown_step_cannot_be_skipped_even_when_its_permissives_hold(on_spec):
    plant = Plant(on_spec)
    machine = plant.sequences.machine(plant.snapshot, state=S.ON_SPEC)
    run = plant.run("normal_shutdown", machine)
    run.update(plant.snapshot)

    for _ in range(HORIZON):
        if plant.snapshot.equipment["K-101"]["load"] <= 0.0:
            break

        plant.snapshot = plant.engine.step(DT)

    assert run.request("stop_feed", plant.snapshot) == ("step 'stop_feed' is not next, 'stop_compressor' is",)
    assert plant.engine.equipment["P-101"].running
    assert machine.state is S.SHUTTING_DOWN


def test_a_step_whose_permissives_fail_is_blocked_with_every_reason():
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot, state=S.PRESSURISED)
    run = SequenceRun(plant.sequences.procedures["cold_start"][2:], machine, plant.act)

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
    sequencer.start("normal_shutdown", plant.snapshot)
    normal = sequencer.run

    for _ in range(5):
        sequencer.update(plant.snapshot)
        plant.snapshot = plant.engine.step(DT)

    assert sequencer.machine.state is S.SHUTTING_DOWN
    assert plant.engine.equipment["P-101"].running

    before = len(plant.log)
    sequencer.start("emergency_shutdown", plant.snapshot)

    assert not plant.engine.equipment["P-101"].running

    plant.drive(sequencer)

    assert normal.aborted
    assert sequencer.machine.state is S.COLD
    assert [message for _, message in plant.actions()[before:]] == [
        "K-101 stop",
        "P-101 stop",
        "LV-101 set_position_target 0.1",
    ]


def starting(state):
    """A plant part way through its cold start: in `state`, with a step of
    the start running."""
    plant = Plant(condition("cold_shutdown"))
    sequencer = Sequencer(plant.sequences, plant.sequences.machine(plant.snapshot), plant.act)
    sequencer.start("cold_start", plant.snapshot)

    for _ in range(HORIZON):
        if sequencer.machine.state is state and sequencer.run.active is not None:
            return plant, sequencer

        sequencer.update(plant.snapshot)
        plant.snapshot = plant.engine.step(DT)

    pytest.fail(f"cold start not {state} after {HORIZON} steps")


def test_a_start_cut_off_by_an_emergency_shutdown_takes_no_further_action():
    plant, sequencer = starting(S.PRESSURISED)
    start = sequencer.run

    before = len(plant.log)

    assert sequencer.start("emergency_shutdown", plant.snapshot) == ()
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
        (S.PRESSURISED, "normal_shutdown", "runs from ['circulating', 'on_spec'], the plant is pressurised"),
        (S.COLD, "emergency_shutdown", "the plant is cold"),
    ],
)
def test_a_procedure_that_cannot_start_leaves_the_current_run_in_place(state, procedure, reason):
    plant, sequencer = starting(state)
    current = sequencer.run

    (refusal,) = sequencer.start(procedure, plant.snapshot)

    assert reason in refusal
    assert sequencer.run is current
    assert sequencer.procedure == "cold_start"
    assert current.active is not None
    assert not current.aborted


def test_a_procedure_already_running_is_not_started_again(on_spec):
    plant = Plant(on_spec)
    sequencer = Sequencer(plant.sequences, plant.sequences.machine(plant.snapshot, state=S.ON_SPEC), plant.act)
    sequencer.start("emergency_shutdown", plant.snapshot)
    sequencer.update(plant.snapshot)
    plant.snapshot = plant.engine.step(DT)
    current = sequencer.run

    assert sequencer.start("emergency_shutdown", plant.snapshot) == ("procedure 'emergency_shutdown' is already running",)

    plant.drive(sequencer)

    assert sequencer.run is current
    assert [message for _, message in plant.actions()] == [
        "K-101 stop",
        "P-101 stop",
        "LV-101 set_position_target 0.1",
    ]


def test_a_procedure_whose_first_permissives_fail_is_not_started(on_spec):
    plant = Plant(on_spec)
    sequencer = Sequencer(plant.sequences, plant.sequences.machine(plant.snapshot), plant.act)

    assert sequencer.start("cold_start", plant.snapshot) == (
        "permissive K-101.load <= 0 not satisfied: K-101.load reads 1",
        "permissive P-101.speed <= 0 not satisfied: P-101.speed reads 1",
    )
    assert sequencer.run is None
    assert not plant.log.events


def test_a_stalled_procedure_can_be_aborted_and_started_again(on_spec):
    plant = Plant(on_spec)
    sequencer = Sequencer(plant.sequences, plant.sequences.machine(plant.snapshot, state=S.ON_SPEC), plant.act)
    sequencer.start("emergency_shutdown", plant.snapshot)
    stalled = sequencer.run
    plant.engine.equipment["P-101"].start()
    plant.engine.equipment["P-101"].set_speed_target(1.0)

    for _ in range(100):
        sequencer.update(plant.snapshot)
        plant.snapshot = plant.engine.step(DT)

    assert stalled.active == "confirm_stopped"
    assert sequencer.start("emergency_shutdown", plant.snapshot)

    sequencer.abort()

    assert sequencer.procedure is None
    assert sequencer.run is None
    assert sequencer.start("emergency_shutdown", plant.snapshot) == ()
    assert stalled.aborted
    assert [message for _, message in plant.actions()].count("P-101 stop") == 2


def test_a_hold_is_never_judged_on_the_snapshot_its_step_started_on(tmp_path):
    plant = Plant(condition("cold_shutdown"))
    sequences = load_text(tmp_path, plant, VALID.replace("for_s: 5", "for_s: 0").replace("LV-101.position <= 0.11", "LV-101.position <= 1.0"))
    sequencer = Sequencer(sequences, sequences.machine(plant.snapshot), plant.act)

    assert sequencer.start("start", plant.snapshot) == ()
    assert sequencer.update(plant.snapshot) == ("step 'line_up' waiting for a reading taken after its actions",)
    assert sequencer.machine.state is S.COLD

    plant.snapshot = plant.engine.step(DT)
    sequencer.update(plant.snapshot)

    assert sequencer.machine.state is S.PURGED


def test_a_timed_hold_counts_from_the_first_snapshot_after_its_step_started(tmp_path):
    plant = Plant(condition("cold_shutdown"))
    sequences = load_text(tmp_path, plant, VALID.replace("LV-101.position <= 0.11", "LV-101.position <= 1.0"))
    machine = sequences.machine(plant.snapshot)
    run = SequenceRun(sequences.procedures["start"], machine, plant.act)
    base = plant.snapshot.sim_time

    def at(offset):
        return dataclasses.replace(plant.snapshot, sim_time=base + offset)

    assert run.request("line_up", at(0)) == ()
    assert run.update(at(0))

    for offset in range(1, 6):
        run.update(at(offset))
        assert run.active == "line_up"

    assert run.update(at(6)) == ()
    assert machine.state is S.PURGED


def test_a_next_step_is_never_judged_on_a_reading_from_before_the_last_actions(tmp_path):
    two_steps = VALID[: VALID.index("sequences:")] + textwrap.dedent(
        """
        sequences:
          start:
            - step: close_drain
              in: [cold]
              actions: ["LV-101.set_position_target 0.1"]
            - step: check_open
              in: [cold]
              permissives: ["LV-101.position >= 0.5"]
        """
    )
    plant = Plant(condition("cold_shutdown"))
    sequences = load_text(tmp_path, plant, two_steps)
    sequencer = Sequencer(sequences, sequences.machine(plant.snapshot), plant.act)

    assert sequencer.start("start", plant.snapshot) == ()
    assert sequencer.update(plant.snapshot) == (
        "step 'check_open' waiting for a reading taken after the last step's actions",
    )

    plant.snapshot = plant.engine.step(DT)

    assert sequencer.update(plant.snapshot) == (
        "permissive LV-101.position >= 0.5 not satisfied: LV-101.position reads 0.45",
    )
    assert sequencer.run.active is None


def test_a_step_without_a_hold_never_advances_on_a_reading_from_before_its_actions(tmp_path):
    no_hold = VALID.replace('      hold: {when: ["LV-101.position <= 0.11"], for_s: 5}\n', "").replace(
        "LV-101.position <= 0.11", "LV-101.position <= 1.0"
    )
    plant = Plant(condition("cold_shutdown"))
    sequences = load_text(tmp_path, plant, no_hold)
    sequencer = Sequencer(sequences, sequences.machine(plant.snapshot), plant.act)

    assert sequencer.start("start", plant.snapshot) == ()
    assert sequencer.machine.state is S.COLD
    assert sequencer.update(plant.snapshot)
    assert sequencer.machine.state is S.COLD

    plant.snapshot = plant.engine.step(DT)
    sequencer.update(plant.snapshot)

    assert sequencer.machine.state is S.PURGED


def test_a_new_procedure_is_not_judged_on_a_reading_from_before_the_last_actions(tmp_path):
    two_procedures = VALID[: VALID.index("sequences:")] + textwrap.dedent(
        """
        sequences:
          close:
            - step: close_drain
              in: [cold]
              actions: ["LV-101.set_position_target 0.1"]
              hold: {when: ["LV-101.position <= 0.11"], for_s: 5}
          check:
            - step: check_open
              in: [cold]
              permissives: ["LV-101.position >= 0.5"]
        """
    )
    plant = Plant(condition("cold_shutdown"))
    sequences = load_text(tmp_path, plant, two_procedures)
    sequencer = Sequencer(sequences, sequences.machine(plant.snapshot), plant.act)

    assert sequencer.start("close", plant.snapshot) == ()
    assert sequencer.start("check", plant.snapshot) == (
        "procedure 'check' waiting for a reading taken after the last actions",
    )

    plant.snapshot = plant.engine.step(DT)

    assert sequencer.start("check", plant.snapshot) == (
        "permissive LV-101.position >= 0.5 not satisfied: LV-101.position reads 0.45",
    )
    assert sequencer.procedure == "close"


def test_a_sequencer_refuses_an_unknown_procedure(cold):
    sequencer = Sequencer(cold.sequences, cold.sequences.machine(cold.snapshot), cold.act)

    with pytest.raises(KeyError, match="no procedure 'warm_start'"):
        sequencer.start("warm_start", cold.snapshot)


def test_an_action_that_raises_is_never_retried_with_the_ones_before_it():
    plant = Plant(condition("cold_shutdown"))
    machine = plant.sequences.machine(plant.snapshot, state=S.PURGED)
    taken = []

    def act(tag, action, value):
        taken.append(f"{tag}.{action}")

        if action == "start":
            raise RuntimeError("refused")

    run = SequenceRun(plant.sequences.procedures["cold_start"][1:], machine, act)

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
        ("      advance: purged", "      advance: purged\n      1: x\n      speed: 2", "unknown key(s) [1, 'speed']"),
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
        ('when: ["LV-101.position <= 0.11"], for_s', "for_s", "needs at least one condition"),
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


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("{from: cold, to: purged, ", "{to: purged, ", "missing ['from']"),
        ('hold: {when: ["LV-101.position <= 0.11"], for_s', "hold: {when: null, for_s", "must be a list"),
        ('hold: {when: ["LV-101.position <= 0.11"], for_s: 5}', "hold: []", "hold must be a mapping"),
        (VALID[VALID.index("gates:"):VALID.index("sequences:")], "", "missing ['gates']"),
        (
            '{from: cold, to: purged, when: ["LV-101.position <= 0.11"]}',
            "{from: cold, to: purged, when: {}}",
            "when must be a list",
        ),
        (VALID[VALID.index("sequences:"):], "sequences: []\n", "mapping of name to steps"),
        (VALID[VALID.index("sequences:"):], "sequences:\n", "mapping of name to steps"),
        (
            "sequences:\n  start:",
            "sequences:\n  1: [{step: x, in: [cold]}]\n  start:",
            "named by a non-empty string",
        ),
        (VALID[VALID.index("gates:"):VALID.index("sequences:")], "gates:\n", "gates must be a list"),
    ],
)
def test_a_single_fault_is_reported_once(tmp_path, cold, old, new, message):
    assert old in VALID

    with pytest.raises(ValueError, match=r"rejected, 1 problem\(s\)") as error:
        load_text(tmp_path, cold, VALID.replace(old, new, 1))

    assert message in str(error.value)
    assert "has no condition to gate it" not in str(error.value)


def test_every_problem_is_reported_together(tmp_path, cold):
    text = VALID.replace("LV-101.set_position_target 0.1", "XV-999.open").replace("for_s: 5", "for_s: -1")

    with pytest.raises(ValueError, match=r"2 problem\(s\)"):
        load_text(tmp_path, cold, text)
