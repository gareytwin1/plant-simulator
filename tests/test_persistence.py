"""
Plant state save and restore - T12-1.

The build-plan tests come first: snapshot, restore and step matches never
having snapshotted; a JSON round trip is lossless; a missing field in an old
save fails loudly. The rest pin what "exact" covers (the controller integral,
the arbiter, the envelope history, a held solver failure), that a refused
restore changes nothing, and what a save is refused for.

The fixture is the integrated reference plant with its loop switched to AUTO
and pushed off its resting state, so every part of the state carries a
non-default value that a restore that dropped it would visibly lose.
"""

import copy
import json
import math
from pathlib import Path

import pytest

from app.controls.arbitration import Source
from app.controls.modes import Mode
from app.engine.engine import Engine
from app.engine.instruments import Instrument
from app.engine.network import SolverResult
from app.engine.persistence import STATE_VERSION, StateError, capture_state, restore_state
from app.plant.loader import load_plant_file


pytestmark = pytest.mark.filterwarnings("ignore:envelope limit")

CONFIG = Path(__file__).resolve().parent.parent / "config" / "plants"
OLEFINS = CONFIG / "olefins_lite.yaml"
GAS = CONFIG / "gas_compression.yaml"

DT = 1.0
WARMUP = 40
HORIZON = 40


def build(path=OLEFINS):
    """A freshly built engine. Instruments are wiring, not state, so the
    reference plant gets the same transmitter every engine in this file has."""
    engine = Engine.from_plant(load_plant_file(path))

    if path == OLEFINS:
        engine.add_instrument(Instrument("PT-201", "nodes", "N-201", "pressure"))

    return engine


def perturbed():
    """The reference plant, mid-upset: PIC-101 in AUTO on a new setpoint, a
    biased transmitter, a raised compressor load and a high separator level."""
    engine = build()
    engine.instruments["PT-201"].bias = 1.5

    loop = engine.loops["PIC-101"].loop
    loop.mode = Mode.AUTO
    loop.pid.setpoint = 190.0
    engine.equipment["K-101"].load_target = 0.9
    engine.equipment["V-101"]._level = 0.85

    for _ in range(WARMUP):
        engine.step(DT)

    return engine


def run(engine, steps=HORIZON):
    return [engine.step(DT) for _ in range(steps)]


def views(snapshots):
    return [(s.as_dict(), s.truth.as_dict()) for s in snapshots]


def through_json(state):
    return json.loads(json.dumps(state))


# --- the build-plan tests ---


def test_restoring_a_capture_then_stepping_matches_never_having_captured():
    original = perturbed()
    state = capture_state(original)
    expected = views(run(original))

    restored = build()
    restore_state(restored, state)

    assert views(run(restored)) == expected


def test_a_json_round_trip_of_the_state_is_lossless():
    state = capture_state(perturbed())

    assert through_json(state) == state


def test_restoring_from_a_json_round_trip_steps_identically():
    original = perturbed()
    state = through_json(capture_state(original))
    expected = views(run(original))

    restored = build()
    restore_state(restored, state)

    assert views(run(restored)) == expected


def test_a_missing_field_in_an_old_save_fails_loudly():
    engine = perturbed()
    state = capture_state(engine)
    del state["equipment"]["K-101"]["load_rate"]

    with pytest.raises(StateError, match=r"equipment\.K-101.*'load_rate'"):
        restore_state(build(), state)


# --- what exact covers ---


def test_a_restored_engine_reads_the_same_before_it_is_stepped():
    original = perturbed()
    restored = build()

    restore_state(restored, capture_state(original))

    assert restored.snapshot().as_dict() == original.snapshot().as_dict()
    assert restored.snapshot().truth.as_dict() == original.snapshot().truth.as_dict()


def test_restoring_onto_the_same_engine_rewinds_it():
    engine = perturbed()
    state = capture_state(engine)
    expected = views(run(engine))

    restore_state(engine, state)

    assert views(run(engine)) == expected


def test_the_state_after_running_on_is_the_same_state_restored_or_not():
    original = perturbed()
    state = capture_state(original)
    run(original)

    restored = build()
    restore_state(restored, state)
    run(restored)

    assert capture_state(restored) == capture_state(original)


def test_the_controller_integral_is_captured():
    original = perturbed()
    pid = original.loops["PIC-101"].loop.pid

    assert pid._integral != 0.0

    restored = build()
    restore_state(restored, capture_state(original))
    twin = restored.loops["PIC-101"].loop

    assert twin.mode is Mode.AUTO
    assert twin.pid._integral == pytest.approx(pid._integral)
    assert twin.pid.setpoint == pytest.approx(190.0)
    assert "PIC-101" in restored._primed


def test_the_envelope_history_is_captured():
    original = perturbed()
    key = ("V-101", "level")

    assert original.trackers[key].time_in(original.limits[key].severity) > 0.0

    restored = build()
    restore_state(restored, capture_state(original))

    assert restored.trackers[key]._time_in_band == original.trackers[key]._time_in_band
    assert restored.trackers[key].peak == original.trackers[key].peak
    assert restored._envelope_band[key] == original._envelope_band[key]
    assert restored._envelope_since[key] == original._envelope_since[key]
    assert restored.snapshot().envelope == original.snapshot().envelope


def test_an_instrument_bias_is_captured():
    original = perturbed()
    restored = build()

    assert restored.instruments["PT-201"].bias == 0.0

    restore_state(restored, capture_state(original))

    assert restored.instruments["PT-201"].bias == pytest.approx(1.5)


def test_arbiter_demands_are_captured_and_stale_ones_dropped():
    original = perturbed()
    original.arbiter.demand("PV-101", Source.OPERATOR, "operator", 0.3)
    state = capture_state(original)

    restored = build()
    restored.arbiter.demand("PV-101", Source.INTERLOCK, "stale", 0.9)
    restore_state(restored, state)

    resolution = restored.arbiter.resolve("PV-101")

    assert resolution.source is Source.OPERATOR
    assert resolution.value == pytest.approx(0.3)
    assert capture_state(restored)["arbiter"] == state["arbiter"]


def test_clock_speed_and_pause_are_captured():
    original = perturbed()
    original.clock.set_speed(4.0)
    original.stop()

    restored = build()
    restore_state(restored, capture_state(original))

    assert restored.clock.get_state() == original.clock.get_state()
    assert restored.snapshot().running is False


def test_a_held_solver_failure_is_captured():
    original = perturbed()
    original.solver_results["gas"] = SolverResult(
        converged=False,
        iterations=50,
        residual=3.5,
        pressure_residual=0.2,
        flow_residual=0.1,
        failure="iteration_cap",
    )
    original.transports["gas"].settled = False
    original.transports["gas"].failure = "domain 'gas': held"

    restored = build()
    restore_state(restored, through_json(capture_state(original)))

    assert restored.solver_results["gas"] == original.solver_results["gas"]
    assert restored.transports["gas"].settled is False
    assert restored.transports["gas"].failure == "domain 'gas': held"
    assert restored.snapshot().solver == original.snapshot().solver


def test_an_engine_without_a_topology_round_trips():
    from app.equipment.pump import CentrifugalPump

    engine = Engine([CentrifugalPump("P-101")])
    engine.equipment["P-101"].speed_target = 0.8

    for _ in range(5):
        engine.step(DT)

    state = capture_state(engine)
    expected = views(run(engine, 5))

    twin = Engine([CentrifugalPump("P-101")])
    restore_state(twin, through_json(state))

    assert views(run(twin, 5)) == expected


# --- refusals ---


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda s: s.pop("clock"), r"state: missing 'clock'"),
        (lambda s: s["clock"].pop("paused"), r"clock: missing 'paused'"),
        (lambda s: s["clock"].update(extra=1), r"clock: unexpected 'extra'"),
        (lambda s: s["clock"].update(speed="fast"), r"clock\.speed: expected a number"),
        (lambda s: s["clock"].update(speed=math.nan), r"clock\.speed: nan is not finite"),
        (lambda s: s["equipment"].pop("P-101"), r"equipment: missing 'P-101'"),
        (lambda s: s["equipment"]["P-101"].update(speed="fast"), r"equipment\.P-101\.speed"),
        (lambda s: s["equipment"]["P-101"].update(speed=None), r"equipment\.P-101\.speed: expected a number"),
        (lambda s: s["equipment"]["P-101"].update(speed=math.inf), r"equipment\.P-101\.speed: inf is not finite"),
        (lambda s: s["equipment"]["P-101"].update(_pump_resistance=-1.0), r"equipment\.P-101\._pump_resistance: P-101\.pump_resistance must be"),
        (lambda s: s["equipment"]["V-101"].update(_level=1.5), r"equipment\.V-101\._level: V-101\.level must be"),
        (lambda s: s["instruments"]["PT-201"].update(bias=math.nan), r"instruments\.PT-201\.bias: nan is not finite"),
        (lambda s: s["domains"]["gas"]["nodes"].update({"N-204": math.inf}), r"nodes\.N-204: inf is not finite"),
        (lambda s: s["loops"]["PIC-101"]["pid"].update(integral=math.nan), r"pid\.integral: nan is not finite"),
        (lambda s: s["equipment"]["P-101"].update(ghost=1.0), r"equipment\.P-101: unexpected 'ghost'"),
        (lambda s: s["domains"]["gas"]["nodes"].pop("N-204"), r"nodes: missing 'N-204'"),
        (lambda s: s["domains"]["gas"]["nodes"].update({"N-201": -5.0}), r"boundary pressure"),
        (lambda s: s["domains"]["gas"]["solver"].update(converged=False), r"solver"),
        (lambda s: s["domains"]["gas"]["streams"].pop("B-K-101"), r"gas\.streams: missing 'B-K-101'"),
        (lambda s: s["domains"]["gas"]["streams"]["B-K-101"].update(composition={"CH4": 0.5}), r"gas\.streams\.B-K-101: composition fractions must sum to 1\.0"),
        (lambda s: s["domains"]["gas"]["temperatures"].update({"N-201": math.nan}), r"gas\.temperatures\.N-201: nan is not finite"),
        (lambda s: s["loops"]["PIC-101"].update(mode="sideways"), r"loops\.PIC-101\.mode"),
        (lambda s: s["loops"]["PIC-101"]["pid"].pop("integral"), r"pid: missing 'integral'"),
        (lambda s: s["loops"]["PIC-101"].update(mode="cascade"), r"loops\.PIC-101\.mode: the engine cannot run a cascade loop"),
        (lambda s: s["loops"]["PIC-101"]["pid"].update(output_min=2.0, output_max=1.0), r"pid: output_min 2\.0 exceeds output_max 1\.0"),
        (lambda s: s["loops"]["PIC-101"]["pid"].update(ki=-0.1), r"pid\.ki: -0\.1 is negative"),
        (lambda s: s["envelope"]["V-101"]["level"]["tracker"].update(elapsed=-1.0), r"tracker\.elapsed: -1\.0 is negative"),
        (lambda s: s["envelope"]["V-101"]["level"]["tracker"]["time_in_band"].update(ALARM=-1.0), r"time_in_band\.ALARM: -1\.0 is negative"),
        (lambda s: s["envelope"]["V-101"]["level"]["tracker"]["peak"].update(magnitude=-1.0), r"peak\.magnitude: -1\.0 is negative"),
        (lambda s: s["envelope"]["V-101"]["level"]["tracker"]["peak"].update(timestamp=-1.0), r"peak\.timestamp: -1\.0 is negative"),
        (lambda s: s["envelope"]["V-101"]["level"]["evaluator"].update(pending_elapsed=-1.0), r"pending_elapsed: -1\.0 is negative"),
        (lambda s: s["envelope"]["V-101"]["level"]["evaluator"].update(pending={"severity": "NORMAL", "side": "hi", "threshold": 1.0}), r"evaluator\.pending\.severity: a held band is never NORMAL"),
        (lambda s: s["envelope"]["V-101"]["level"].update(band={"severity": "ALARM", "side": None}), r"level\.band: ALARM/None does not match"),
        (lambda s: s["envelope"]["V-101"]["level"]["evaluator"].update(band={"severity": "NORMAL", "side": "hi", "threshold": 1.0}), r"evaluator\.band\.severity: a held band is never NORMAL"),
        (lambda s: s["envelope"]["V-101"]["level"]["evaluator"]["band"].update(threshold=0.7), r"evaluator\.band\.threshold: 0\.7 is not the configured warning_hi \(0\.8\)"),
        (lambda s: s["envelope"]["V-101"]["level"]["evaluator"].update(pending={"severity": "ALARM", "side": "hi", "threshold": 0.85}), r"evaluator\.pending\.threshold: 0\.85 is not the configured alarm_hi \(None\)"),
        (lambda s: s["envelope"]["V-101"].pop("level"), r"envelope\.V-101: missing 'level'"),
        (lambda s: s["arbiter"]["PV-101"].pop("operator"), r"arbiter\.PV-101: missing 'operator'"),
    ],
)
def test_a_damaged_save_is_refused_naming_the_field(damage, message):
    engine = perturbed()
    state = capture_state(engine)
    damage(state)

    with pytest.raises(StateError, match=message):
        restore_state(build(), state)


@pytest.mark.parametrize("speed", [0.0, -1.0])
def test_any_clock_speed_a_live_clock_holds_round_trips(speed):
    engine = perturbed()
    engine.clock.set_speed(speed)
    engine.clock.sim_time = -5.0

    restored = build()
    restore_state(restored, through_json(capture_state(engine)))

    assert restored.clock.speed == speed
    assert restored.clock.sim_time == -5.0


def test_a_refused_restore_changes_nothing():
    target = perturbed()
    before = capture_state(target)

    state = capture_state(build())
    state["loops"]["PIC-101"]["pid"]["action"] = "sideways"

    with pytest.raises(StateError):
        restore_state(target, state)

    assert capture_state(target) == before


def test_a_save_from_a_different_plant_is_refused():
    with pytest.raises(StateError, match="does not match this plant"):
        restore_state(build(), capture_state(build(GAS)))


def test_a_save_of_another_version_is_refused():
    state = capture_state(build())
    state["version"] = STATE_VERSION + 1

    with pytest.raises(StateError, match="version"):
        restore_state(build(), state)


def test_a_save_with_no_version_is_refused():
    state = capture_state(build())
    del state["version"]

    with pytest.raises(StateError, match="version"):
        restore_state(build(), state)


def test_conflicting_saved_demands_are_refused_before_anything_is_written():
    state = capture_state(build())
    state["arbiter"]["PV-101"]["operator"] = {"a": 0.2, "b": 0.8}
    target = perturbed()
    before = capture_state(target)

    with pytest.raises(StateError, match=r"arbiter\.PV-101\.operator"):
        restore_state(target, state)

    assert capture_state(target) == before


def test_a_device_attribute_that_is_not_plain_data_cannot_be_saved():
    engine = build()
    engine.equipment["P-101"].history = [1.0, 2.0]

    with pytest.raises(StateError, match=r"equipment\.P-101\.history"):
        capture_state(engine)


def test_capturing_does_not_change_the_plant():
    engine = perturbed()
    before = copy.deepcopy(capture_state(engine))

    capture_state(engine)

    assert capture_state(engine) == before
