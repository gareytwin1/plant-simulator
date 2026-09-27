"""
Loop execution in the engine step (T8-4).

The plant is one pressure loop: PV-101 throttles a 100 psia supply into the
internal node N-02, which drains through a fixed valve HV-101 to a 20 psia
header. Both valves are linear with capacity 20.0, so at the design travel of
0.5 each is Cv 10 and splits the 80 psi evenly - N-02 sits at 60.0 psia, the
setpoint. Dropping the supply to 80 psia takes N-02 to 50.0 with PV-101 where
it was; holding 60.0 needs PV-101's Cv at sqrt(4000 / 20), which is travel
1/sqrt(2).

PIC-101 is direct-acting on PV-101 - opening the supply valve raises the
pressure it measures - which is the only action the PID block has.
"""

import math

import pytest

from app.controls.arbitration import Source
from app.controls.loader import load_loops
from app.controls.modes import Mode
from app.engine.engine import Engine, _reading
from app.engine.instruments import Instrument
from app.plant.loader import load_plant


DT = 1.0
KP = 0.005
KI = 0.005
SETPOINT = 60.0
DESIGN_TRAVEL = 0.5
DISTURBED_SUPPLY = 80.0
DISTURBED_PRESSURE = 50.0
HOLDING_TRAVEL = 1.0 / math.sqrt(2.0)
STROKE_RATE = 0.05


def config(mode="AUTO"):
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 100.0},
            {"id": "N-02", "boundary": False, "pressure": SETPOINT},
            {"id": "N-03", "boundary": True, "pressure": 20.0},
        ],
        "equipment": [
            valve("PV-101", "N-01", "N-02"),
            valve("HV-101", "N-02", "N-03"),
        ],
        "controllers": [
            {
                "tag": "PIC-101",
                "pv": "N-02",
                "sp": SETPOINT,
                "out": "PV-101",
                "mode": mode,
                "kp": KP,
                "ki": KI,
                "kd": 0.0,
            },
        ],
    }


def valve(tag, node_in, node_out):
    return {
        "tag": tag,
        "type": "control_valve",
        "node_in": node_in,
        "node_out": node_out,
        "design": {
            "capacity": 20.0,
            "flow_characteristic": "linear",
            "stroke_rate": STROKE_RATE,
            "position": DESIGN_TRAVEL,
            "position_target": DESIGN_TRAVEL,
        },
    }


def running(cfg, **kwargs):
    plant = load_plant(cfg)
    engine = Engine.from_plant(plant, **kwargs)
    engine.start()

    return plant, engine


def run(engine, steps):
    snapshot = engine.snapshot()

    for _ in range(steps):
        snapshot = engine.step(DT)

    return snapshot


def disturb(plant):
    plant.nodes["N-01"].set_boundary_pressure(DISTURBED_SUPPLY)


def pressure(snapshot):
    return snapshot.nodes["N-02"]["pressure"]


# --------------------------------------------------------------------------
# Holding setpoint
# --------------------------------------------------------------------------

def test_a_loop_holds_setpoint_through_an_upstream_disturbance():
    plant, engine = running(config("AUTO"))
    run(engine, 10)

    disturb(plant)
    snapshot = run(engine, 300)

    assert pressure(snapshot) == pytest.approx(SETPOINT, abs=1e-6)
    assert snapshot.equipment["PV-101"]["position"] == pytest.approx(
        HOLDING_TRAVEL, abs=1e-6
    )


def test_the_same_disturbance_with_the_loop_in_manual_is_not_corrected():
    """The control case: the disturbance is real, and only the loop answers it."""
    plant, engine = running(config("MANUAL"))
    run(engine, 10)

    disturb(plant)
    snapshot = run(engine, 300)

    assert pressure(snapshot) == pytest.approx(DISTURBED_PRESSURE, abs=1e-6)
    assert snapshot.equipment["PV-101"]["position"] == pytest.approx(DESIGN_TRAVEL)


def test_a_loop_bound_in_auto_starts_without_a_bump():
    """Primed on its first execution: at zero error, an unprimed PID would
    compute an output of zero and slam PV-101 to its floor."""
    _, engine = running(config("AUTO"))

    # Every step, not the last one: an unprimed loop recovers to the design
    # point within a few dozen steps, so only the transient shows the bump.
    for _ in range(50):
        snapshot = engine.step(DT)

        assert snapshot.controllers["PIC-101"]["out"] == pytest.approx(DESIGN_TRAVEL)
        assert snapshot.equipment["PV-101"]["position"] == pytest.approx(
            DESIGN_TRAVEL
        )
        assert pressure(snapshot) == pytest.approx(SETPOINT)


# --------------------------------------------------------------------------
# What a loop reads, and when its answer lands
# --------------------------------------------------------------------------

def test_a_loop_answers_the_measurement_the_last_step_published_with_no_lag():
    """The step that publishes the disturbance ran its loop on the snapshot
    before it, so its output has not moved. The very next step answers the
    published 50.0 psia: its output is the closed form of one PI step from a
    primed steady state, PV-101 strokes toward it inside that same step, and
    the pressure it raises is published by that same step."""
    plant, engine = running(config("AUTO"))
    run(engine, 10)

    disturb(plant)
    published = engine.step(DT)

    assert pressure(published) == pytest.approx(DISTURBED_PRESSURE)
    assert published.controllers["PIC-101"]["out"] == pytest.approx(DESIGN_TRAVEL)

    answered = engine.step(DT)
    error = SETPOINT - DISTURBED_PRESSURE

    assert answered.controllers["PIC-101"]["out"] == pytest.approx(
        DESIGN_TRAVEL + KP * error + KI * error * DT
    )
    assert answered.equipment["PV-101"]["position_target"] == pytest.approx(
        answered.controllers["PIC-101"]["out"]
    )
    assert answered.equipment["PV-101"]["position"] == pytest.approx(
        DESIGN_TRAVEL + STROKE_RATE * DT
    )
    assert pressure(answered) > DISTURBED_PRESSURE


def test_no_loop_reads_a_value_from_inside_the_step_it_runs_in():
    """A loop that could see its own step's solve would have answered the
    disturbance in the step that published it. It never does, however the
    tuning is set: the output published with the disturbed pressure is always
    the one computed before it."""
    cfg = config("AUTO")
    cfg["controllers"][0]["kp"] = 1.0
    plant, engine = running(cfg)
    run(engine, 3)

    disturb(plant)
    published = engine.step(DT)

    assert published.controllers["PIC-101"]["out"] == pytest.approx(DESIGN_TRAVEL)
    assert published.equipment["PV-101"]["position_target"] == pytest.approx(
        DESIGN_TRAVEL
    )


def test_a_loop_controls_what_its_transmitter_indicates_not_the_truth():
    """Control reads the indicated view. A +5 psi transmitter bias makes the
    loop hold the *indicated* pressure at setpoint, so the true pressure
    settles 5 psi low - the hidden cause an operator has to find."""
    transmitter = Instrument("PT-101", "nodes", "N-02", "pressure")
    _, engine = running(config("AUTO"), instruments=[transmitter])
    run(engine, 10)

    transmitter.bias = 5.0
    snapshot = run(engine, 300)

    assert pressure(snapshot) == pytest.approx(SETPOINT, abs=1e-6)
    assert snapshot.controllers["PIC-101"]["pv"] == pytest.approx(SETPOINT, abs=1e-6)
    assert snapshot.truth.nodes["N-02"]["pressure"] == pytest.approx(
        SETPOINT - 5.0, abs=1e-6
    )


def test_a_bias_written_between_steps_reaches_the_loop_on_the_next_step():
    transmitter = Instrument("PT-101", "nodes", "N-02", "pressure")
    _, engine = running(config("AUTO"), instruments=[transmitter])
    run(engine, 10)

    transmitter.bias = 5.0
    snapshot = engine.step(DT)
    error = -5.0

    assert snapshot.controllers["PIC-101"]["out"] == pytest.approx(
        DESIGN_TRAVEL + KP * error + KI * error * DT
    )


# --------------------------------------------------------------------------
# Order and determinism
# --------------------------------------------------------------------------

def two_loop_config(order):
    """Two copies of the reference loop on one shared supply, each draining to
    a header of its own. A boundary node holds its pressure whatever flows
    through it, so the loops share no solved value and each designs to the
    midpoint of its own supply-to-header drop."""
    headers = {"A": 20.0, "B": 30.0}
    cfg = {
        "nodes": [{"id": "N-01", "boundary": True, "pressure": 100.0}],
        "equipment": [],
    }
    loops = {}

    for suffix, header in headers.items():
        design = (100.0 + header) / 2.0
        cfg["nodes"] += [
            {"id": f"N-02{suffix}", "boundary": False, "pressure": design},
            {"id": f"N-03{suffix}", "boundary": True, "pressure": header},
        ]
        cfg["equipment"] += [
            valve(f"PV-10{suffix}", "N-01", f"N-02{suffix}"),
            valve(f"HV-10{suffix}", f"N-02{suffix}", f"N-03{suffix}"),
        ]
        loops[suffix] = {
            "tag": f"PIC-10{suffix}",
            "pv": f"N-02{suffix}",
            "sp": design,
            "out": f"PV-10{suffix}",
            "mode": "AUTO",
            "kp": KP,
            "ki": KI,
            "kd": 0.0,
        }

    cfg["controllers"] = [loops[suffix] for suffix in order]

    return cfg


def disturbed_trace(order):
    plant, engine = running(two_loop_config(order))
    run(engine, 5)

    disturb(plant)

    return [engine.step(DT).as_dict() for _ in range(100)]


def test_loops_run_in_configuration_order():
    _, engine = running(two_loop_config("BA"))

    assert list(engine.loops) == ["PIC-10B", "PIC-10A"]
    assert list(engine.snapshot().controllers) == ["PIC-10B", "PIC-10A"]


def test_execution_order_does_not_change_the_answer():
    forward = disturbed_trace("AB")
    reverse = disturbed_trace("BA")

    for a, b in zip(forward, reverse):
        a["controllers"] = dict(sorted(a["controllers"].items()))
        b["controllers"] = dict(sorted(b["controllers"].items()))

    assert forward == reverse


def test_the_same_steps_give_bit_identical_controlled_traces():
    assert disturbed_trace("AB") == disturbed_trace("AB")


# --------------------------------------------------------------------------
# Stopped engine, modes and arbitration
# --------------------------------------------------------------------------

def test_a_stopped_engine_runs_no_loop():
    plant, engine = running(config("AUTO"))
    run(engine, 10)
    disturb(plant)
    engine.step(DT)
    before = engine.snapshot()

    engine.stop()
    after = run(engine, 20)

    assert after.controllers == before.controllers
    assert after.equipment["PV-101"] == before.equipment["PV-101"]


def test_a_manual_loop_drives_its_output_to_the_manual_command():
    _, engine = running(config("MANUAL"))

    engine.loops["PIC-101"].loop.manual_output = 0.8
    snapshot = run(engine, 20)

    assert snapshot.controllers["PIC-101"]["out"] == pytest.approx(0.8)
    assert snapshot.equipment["PV-101"]["position"] == pytest.approx(0.8)


def test_switching_manual_to_auto_is_bumpless_through_the_engine():
    _, engine = running(config("MANUAL"))
    engine.loops["PIC-101"].loop.manual_output = 0.6
    run(engine, 20)

    engine.loops["PIC-101"].loop.mode = Mode.AUTO
    transferred = engine.step(DT)

    # The first AUTO step reads the same settled pressure the loop tracked
    # in MANUAL, so it reproduces 0.6 exactly; only then does the error that
    # 0.6 leaves (N-02 above setpoint) start walking it back.
    assert transferred.controllers["PIC-101"]["out"] == pytest.approx(0.6, abs=1e-12)
    assert pressure(transferred) > SETPOINT
    assert engine.step(DT).controllers["PIC-101"]["out"] < 0.6


def test_an_operator_demand_outranks_the_loop_through_the_arbiter():
    _, engine = running(config("AUTO"))

    engine.arbiter.demand("PV-101", Source.OPERATOR, "console", 0.2)
    snapshot = run(engine, 20)

    assert snapshot.equipment["PV-101"]["position_target"] == pytest.approx(0.2)
    assert snapshot.equipment["PV-101"]["position"] == pytest.approx(0.2)


# --------------------------------------------------------------------------
# Snapshot section and registration
# --------------------------------------------------------------------------

def test_the_controllers_section_carries_pv_sp_out_and_mode():
    _, engine = running(config("AUTO"))

    row = engine.step(DT).as_dict()["controllers"]["PIC-101"]

    assert set(row) == {"pv", "sp", "out", "mode"}
    assert row["pv"] == pytest.approx(SETPOINT)
    assert row["sp"] == SETPOINT
    assert row["out"] == pytest.approx(DESIGN_TRAVEL)
    assert row["mode"] == "AUTO"


def test_a_plant_with_no_controllers_publishes_an_empty_section():
    cfg = config()
    del cfg["controllers"]
    _, engine = running(cfg)

    assert engine.step(DT).controllers == {}


def test_a_loop_tag_already_in_use_is_refused():
    plant = load_plant(config())
    binding = load_loops(plant)["PIC-101"]
    engine = Engine.from_plant(plant)

    with pytest.raises(ValueError, match="already in use"):
        engine.add_loop(binding)


def test_an_instrument_cannot_take_a_loop_tag():
    _, engine = running(config())

    with pytest.raises(ValueError, match="already in use"):
        engine.add_instrument(Instrument("PIC-101", "nodes", "N-02", "pressure"))


def test_a_second_loop_on_a_driven_output_is_refused():
    plant = load_plant(config())
    engine = Engine.from_plant(plant)
    cfg = config()
    cfg["controllers"][0]["tag"] = "PIC-102"
    second = load_loops(load_plant(cfg))["PIC-102"]

    with pytest.raises(ValueError, match="already bound"):
        engine.add_loop(second)


def test_a_loop_on_a_device_outside_the_engine_is_refused():
    plant = load_plant(config())
    binding = load_loops(plant)["PIC-101"]
    engine = Engine(
        [plant.devices["HV-101"]],
        topologies=plant.topologies,
    )

    with pytest.raises(ValueError, match="not equipment of this engine"):
        engine.add_loop(binding)


def test_a_loop_measuring_an_unpublished_point_is_refused():
    plant = load_plant(config())
    binding = load_loops(plant)["PIC-101"]
    engine = Engine(plant.devices.values())

    with pytest.raises(ValueError, match="does not publish"):
        engine.add_loop(binding)



def test_a_measurement_that_is_not_a_number_is_refused_by_name():
    """The same refusal add_instrument gives, raised as a ValueError rather
    than an assert that python -O would strip."""
    view = {"equipment": {"PV-101": {"signal_ok": True}}}

    with pytest.raises(ValueError, match="loop PIC-101 .* not a number"):
        _reading(view, ("equipment", "PV-101", "signal_ok"), reader="loop PIC-101")
