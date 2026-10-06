"""Training session - T16-8."""

import threading

import pytest

from app import config
from app.alarms.acknowledge import Acknowledged
from app.engine.scheduler import Scheduler
from app.scenarios.runner import Phase, ScenarioLibrary, ScenarioStateError
from app.training.runtime import PlantRuntime
from app.training.session import TrainingSession


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

SCENARIO = (
    "id: pump-trip\n"
    "plant: olefins_lite\n"
    "initial_condition: {condition: feed_pump_trip}\n"
    "objectives:\n"
    "  - id: recover-level\n"
    "    success: {condition: 'V-101.level >= 0.25', hold_duration_s: 20}\n"
    "    failure: {condition: 'V-101.level <= 0.1'}\n"
    "time_limit_s: 900\n"
    "difficulty: easy\n"
    "seed: 7\n"
)

BLOCKED = 0.3


def scheduler_workers():
    return [t for t in threading.enumerate() if t.name == "engine-scheduler"]


@pytest.fixture
def session(tmp_path):
    (tmp_path / "pump-trip.yaml").write_text(SCENARIO)
    made = TrainingSession(ScenarioLibrary(scenarios=tmp_path))

    yield made

    made.end()


def published(session):
    return session.training_scheduler.snapshot()


def pump(session):
    return published(session).equipment["P-101"]


def actions_of(runtime):
    return [(event.tag, event.data["action"]) for event in runtime.actions.events]


def blocks_while_stepping(session, call):
    """Whether `call` waits for `step_lock`: it must not finish while another
    thread holds the lock, and must finish once it is released."""
    finished = threading.Event()
    failure = []

    def run():
        try:
            call()
        except Exception as error:  # surfaced by the assertion below
            failure.append(error)
        finally:
            finished.set()

    with session.training_scheduler.step_lock:
        thread = threading.Thread(target=run)
        thread.start()
        waited = not finished.wait(BLOCKED)

    thread.join(5.0)
    assert not failure, failure

    return waited and finished.is_set()


def test_a_new_session_runs_free_play_on_the_configured_plant_and_starts_no_thread():
    before = len(scheduler_workers())
    made = TrainingSession()

    try:
        assert isinstance(made.free, PlantRuntime)
        assert made.runner.phase is Phase.IDLE
        assert isinstance(made.training_scheduler, Scheduler)
        assert made.training_scheduler.running is False
        assert len(scheduler_workers()) == before

        snapshot = published(made)

        # normal_operation: the feed pump is running at full speed.
        assert snapshot.equipment["P-101"]["running"] is True
        assert snapshot.sim_time == made.free.snapshot().sim_time
    finally:
        made.end()


def test_the_session_does_not_reuse_the_legacy_session():
    made = TrainingSession()

    try:
        assert not hasattr(made, "compressor")
        assert not hasattr(made, "pump")
        assert not hasattr(made, "compressor_state")
        assert not hasattr(made, "pump_state")
    finally:
        made.end()


def test_a_manual_step_advances_free_play(session):
    before = published(session).sim_time

    stepped = session.training_scheduler.step_once()

    assert stepped is not None
    assert stepped.sim_time == pytest.approx(before + config.SIMULATION_STEP_SECONDS)
    assert published(session).sim_time == stepped.sim_time


def test_loading_a_scenario_switches_the_published_snapshot_in_the_same_command(session):
    free_time = published(session).sim_time
    assert pump(session)["running"] is True

    session.load("pump-trip")

    assert session.runner.phase is Phase.LOADED
    # feed_pump_trip: the pump is stopped, on a clock of its own.
    assert pump(session)["running"] is False
    assert published(session).sim_time != free_time


def test_unloading_hands_the_snapshot_back_to_free_play_where_it_was_left(session):
    session.training_scheduler.step_once()
    left = published(session).sim_time
    session.load("pump-trip")

    session.unload()

    assert session.runner.phase is Phase.IDLE
    assert published(session).sim_time == left
    assert pump(session)["running"] is True


def test_free_play_does_not_advance_while_a_scenario_is_loaded(session):
    left = session.free.snapshot().sim_time
    session.load("pump-trip")
    session.start()

    for _ in range(3):
        session.training_scheduler.step_once()

    assert session.free.snapshot().sim_time == left
    assert session.runner.result().elapsed_s == pytest.approx(3.0)


def test_a_free_play_action_reaches_the_plant_the_snapshot_shows(session):
    session.act("P-101", "set_speed_target", 0.5)

    assert pump(session)["speed_target"] == pytest.approx(0.5)
    assert session.free.engine.equipment["P-101"].speed_target == pytest.approx(0.5)
    assert actions_of(session.free) == [("P-101", "set_speed_target")]


def test_a_scenario_action_reaches_the_scenarios_plant_not_free_play(session):
    session.load("pump-trip")
    session.start()

    session.act("P-101", "start", None)

    assert pump(session)["running"] is True
    assert session.free.engine.equipment["P-101"].speed_target == pytest.approx(1.0)
    assert [a["action"] for a in session.runner.result().actions] == ["start"]
    assert session.free.actions.events == ()


def test_an_interlock_reset_goes_to_the_runtime_of_the_plant_in_play(session):
    interlock = next(iter(session.free.trips.interlocks))

    session.act(interlock, "reset", None)

    assert actions_of(session.free) == [(interlock, "reset")]

    session.load("pump-trip")
    session.act(interlock, "reset", None)

    assert [a["tag"] for a in session.runner.result().actions] == [interlock]


def test_a_refused_action_raises_and_publishes_nothing(session):
    session.training_scheduler.step_once()
    before = published(session)

    with pytest.raises(KeyError):
        session.act("X-999", "start", None)

    assert published(session) is before


def test_an_action_on_a_finished_scenario_is_refused(session):
    session.load("pump-trip")
    session.start()
    session.abort()

    with pytest.raises(ScenarioStateError):
        session.act("P-101", "start", None)


def test_an_acknowledge_in_free_play_goes_to_the_free_plants_alarms(session):
    assert session.acknowledge("no-such-alarm") is Acknowledged.UNKNOWN


def test_an_acknowledge_in_a_scenario_goes_to_the_runs_alarms(session):
    session.load("pump-trip")
    session.start()
    session.training_scheduler.step_once()

    (entry, *_) = session.alarm_entries()

    assert session.acknowledge(entry.id) is Acknowledged.RECORDED
    assert session.acknowledge(entry.id) is Acknowledged.ALREADY
    assert session.free.alarm_entries() == ()


def test_alarm_entries_follow_the_plant_in_play(session):
    assert session.alarm_entries() == session.free.alarm_entries()

    session.load("pump-trip")
    session.start()
    session.training_scheduler.step_once()

    assert session.alarm_entries() == session.runner.alarm_entries()
    assert session.alarm_entries() != ()

    session.abort()
    session.unload()

    assert session.alarm_entries() == session.free.alarm_entries()


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.act("P-101", "set_speed_target", 0.5),
        lambda s: s.acknowledge("no-such-alarm"),
        lambda s: s.load("pump-trip"),
        lambda s: s.alarm_entries(),
    ],
    ids=["act", "acknowledge", "load", "alarm_entries"],
)
def test_free_play_reads_and_writes_wait_for_the_step(session, call):
    assert blocks_while_stepping(session, lambda: call(session))


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.act("P-101", "start", None),
        lambda s: s.acknowledge("no-such-alarm"),
        lambda s: s.start(),
        lambda s: s.abort(),
        lambda s: s.alarm_entries(),
    ],
    ids=["act", "acknowledge", "start", "abort", "alarm_entries"],
)
def test_scenario_reads_and_writes_wait_for_the_step(session, call):
    session.load("pump-trip")

    assert blocks_while_stepping(session, lambda: call(session))


def test_a_scenario_unload_waits_for_the_step(session):
    session.load("pump-trip")

    assert blocks_while_stepping(session, session.unload)


def test_the_runner_lock_nests_inside_step_lock(session):
    # A load holds step_lock, then the runner lock. A reader that took the
    # runner lock first and then step_lock would deadlock against it, so a
    # worker stepping while requests load and abort must finish.
    session.training_scheduler.start()
    failures = []

    def hammer():
        try:
            for _ in range(20):
                session.load("pump-trip")
                session.start()
                session.act("P-101", "start", None)
                session.alarm_entries()
                session.abort()
                session.unload()
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=hammer)
    thread.start()
    thread.join(60.0)

    assert not thread.is_alive()
    assert not failures
    assert session.training_scheduler.error is None


def test_end_closes_the_scheduler_and_a_reclaimed_session_refuses_start_and_step(session):
    session.end()

    session.training_scheduler.start()

    assert session.training_scheduler.closed
    assert session.training_scheduler.running is False
    assert session.training_scheduler.step_once() is None


def test_end_stops_a_running_worker_and_is_idempotent(session):
    before = len(scheduler_workers())
    session.training_scheduler.start()
    assert len(scheduler_workers()) == before + 1

    session.end()
    session.end()

    assert len(scheduler_workers()) == before


def test_a_running_session_steps_free_play_on_its_own_scheduler(session):
    left = published(session).sim_time
    session.training_scheduler.start()

    try:
        for _ in range(200):
            if published(session).sim_time > left:
                break
            threading.Event().wait(0.05)
    finally:
        session.end()

    assert published(session).sim_time > left
    assert session.training_scheduler.error is None
