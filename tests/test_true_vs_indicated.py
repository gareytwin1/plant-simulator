"""
True and indicated values - T13-2.

The build-plan tests come first: an instrument bias moves the indicated value
while the true one stays exactly where an unbiased twin plant has it, and a
controller closing its loop on a biased transmitter holds the indication at
setpoint and walks the true value off it by the bias. The third - no consumer
accidentally reads true - is structural and lives in
tests/test_truth_isolation.py. The rest pin the rules app/engine/instruments.py
and app/engine/snapshot.py state.
"""

import json
import math
from dataclasses import dataclass

import pytest

from app.controls.modes import Loop, Mode
from app.controls.pid import PID
from app.disturbances.malfunction import Malfunction, MalfunctionRegistry, NotWritable
from app.engine.engine import Engine
from app.engine.instruments import Instrument, indicate
from app.engine.snapshot import build_snapshot
from app.equipment.registry import EquipmentRegistry

from tests.test_engine_solver import reference


FLOW = ("streams", "B-FV-101", "flow")


def running_valve_train():
    plant = reference("liquid_valve_train")

    for tag in ("P-101", "P-102"):
        plant.devices[tag].set_speed_target(1.0)
        plant.devices[tag].start()

    return plant


def engine_with(*instruments):
    return Engine.from_plant(running_valve_train(), instruments=instruments)


def transmitter(bias=0.0):
    return Instrument("FT-101", *FLOW, bias=bias)


def settle(engine, steps=5):
    for _ in range(steps):
        snapshot = engine.step(1.0)

    return snapshot


def flow_controller():
    return Loop(
        PID(kp=0.0003, ki=0.0006, kd=0.0, output_min=0.0, output_max=1.0, setpoint=600.0),
        Mode.AUTO,
    )


def run_flow_loop(engine, steps=300):
    loop = flow_controller()
    snapshot = engine.snapshot()

    for _ in range(steps):
        output = loop.compute(snapshot.streams["B-FV-101"]["flow"], 1.0)
        engine.equipment["FV-101"].set_position_target(output)
        snapshot = engine.step(1.0)

    return snapshot


# ---- instrument bias makes indicated diverge while true stays correct -----


def test_a_biased_transmitter_indicates_high_while_the_plant_is_untouched():
    biased = engine_with(transmitter(bias=40.0))
    twin = engine_with()

    shown = settle(biased)
    actual = settle(twin)

    assert actual.streams["B-FV-101"]["flow"] > 0.0
    assert shown.streams["B-FV-101"]["flow"] == pytest.approx(
        actual.streams["B-FV-101"]["flow"] + 40.0,
    )
    assert shown.truth.as_dict() == actual.truth.as_dict()


def test_only_the_instrumented_point_moves():
    shown = settle(engine_with(transmitter(bias=40.0)))
    actual = settle(engine_with())

    moved = shown.as_dict()
    moved["streams"]["B-FV-101"]["flow"] = actual.streams["B-FV-101"]["flow"]

    assert moved == actual.as_dict()


def test_moving_a_bias_mid_run_moves_nothing_in_the_plant():
    instrument = transmitter()
    biased = engine_with(instrument)
    twin = engine_with()

    for step in range(20):
        if step == 5:
            instrument.bias = -75.0

        shown = biased.step(1.0)
        actual = twin.step(1.0)

        assert shown.truth.as_dict() == actual.truth.as_dict()

    assert shown.streams["B-FV-101"]["flow"] == pytest.approx(
        actual.streams["B-FV-101"]["flow"] - 75.0,
    )


def test_a_healthy_instrument_indicates_the_truth_bit_for_bit():
    snapshot = settle(engine_with(transmitter()))
    published = snapshot.as_dict()

    for section, rows in snapshot.truth.as_dict().items():
        assert published[section] == rows


# ---- the controller reacts to indicated -----------------------------------


def test_a_flow_loop_holds_setpoint_on_a_healthy_transmitter():
    snapshot = run_flow_loop(engine_with(transmitter()))

    assert snapshot.streams["B-FV-101"]["flow"] == pytest.approx(600.0, abs=0.01)
    assert snapshot.truth.streams["B-FV-101"]["flow"] == pytest.approx(600.0, abs=0.01)


def test_a_flow_loop_on_a_biased_transmitter_drives_the_true_flow_off_setpoint():
    snapshot = run_flow_loop(engine_with(transmitter(bias=50.0)))

    assert snapshot.streams["B-FV-101"]["flow"] == pytest.approx(600.0, abs=0.01)
    assert snapshot.truth.streams["B-FV-101"]["flow"] == pytest.approx(550.0, abs=0.01)


def test_the_loop_moves_the_valve_to_compensate_for_the_bias():
    healthy = run_flow_loop(engine_with(transmitter()))
    biased = run_flow_loop(engine_with(transmitter(bias=50.0)))

    assert (
        biased.equipment["FV-101"]["position"]
        < healthy.equipment["FV-101"]["position"]
    )


# ---- instrument faults as malfunctions (C8) -------------------------------


@dataclass(frozen=True)
class Linear:
    duration: float

    def fraction(self, elapsed):
        return min(elapsed / self.duration, 1.0)


def malfunctions_for(engine):
    equipment = EquipmentRegistry()

    for device in engine.equipment.values():
        equipment.register(device)

    return MalfunctionRegistry(equipment, engine.instruments.values())


def test_a_drifting_transmitter_is_a_ramped_bias_malfunction():
    engine = engine_with(transmitter())
    twin = engine_with()
    malfunctions = malfunctions_for(engine)
    malfunctions.add(Malfunction("FT-101", "bias", 40.0, profile=Linear(10.0)))

    errors = []

    for _ in range(12):
        malfunctions.update(engine.snapshot())
        shown = engine.step(1.0)
        actual = twin.step(1.0)

        assert shown.truth.as_dict() == actual.truth.as_dict()

        errors.append(shown.streams["B-FV-101"]["flow"] - actual.streams["B-FV-101"]["flow"])

    assert errors[0] == pytest.approx(0.0, abs=1e-9)
    assert errors[5] == pytest.approx(20.0)
    assert errors[-1] == pytest.approx(40.0)
    assert all(later >= earlier for earlier, later in zip(errors, errors[1:]))


def test_reverting_an_instrument_fault_restores_a_true_reading():
    instrument = transmitter()
    engine = engine_with(instrument)
    malfunctions = malfunctions_for(engine)
    malfunction = Malfunction("FT-101", "bias", 40.0)
    malfunctions.add(malfunction)
    malfunctions.update(engine.snapshot())

    malfunctions.revert(malfunction)
    snapshot = engine.step(1.0)

    assert instrument.bias == 0.0
    assert snapshot.streams["B-FV-101"]["flow"] == snapshot.truth.streams["B-FV-101"]["flow"]


@pytest.mark.parametrize("parameter", ["section", "source", "variable", "tag"])
def test_a_malfunction_moves_nothing_on_an_instrument_but_its_bias(parameter):
    malfunctions = malfunctions_for(engine_with(transmitter()))

    with pytest.raises(NotWritable, match="allows only"):
        malfunctions.add(Malfunction("FT-101", parameter, 1.0))


def test_an_instrument_tag_may_not_shadow_a_device():
    engine = engine_with()
    equipment = EquipmentRegistry()

    for device in engine.equipment.values():
        equipment.register(device)

    with pytest.raises(ValueError, match="already in use"):
        MalfunctionRegistry(equipment, [Instrument("FV-101", *FLOW)])


def test_a_device_registered_after_an_instrument_cannot_be_shadowed_by_it():
    engine = engine_with(transmitter())
    equipment = EquipmentRegistry()
    malfunctions = MalfunctionRegistry(equipment, engine.instruments.values())
    malfunctions.add(Malfunction("FT-101", "bias", 5.0))

    shadowed = engine.equipment["FV-101"]
    shadowed.tag = "FT-101"
    equipment.register(shadowed)

    with pytest.raises(ValueError, match="both a device and an instrument"):
        malfunctions.update(engine.snapshot())


# ---- the wire carries the indicated view only -----------------------------


def test_as_dict_publishes_the_indication_and_never_the_truth():
    snapshot = settle(engine_with(transmitter(bias=40.0)))
    published = json.loads(json.dumps(snapshot.as_dict()))

    assert "truth" not in published
    assert published["streams"]["B-FV-101"]["flow"] == pytest.approx(
        snapshot.truth.streams["B-FV-101"]["flow"] + 40.0,
    )


def test_truth_is_immutable():
    snapshot = settle(engine_with())

    with pytest.raises(TypeError):
        snapshot.truth.streams["B-FV-101"]["flow"] = 0.0


# ---- registering an instrument --------------------------------------------


def test_an_instrument_on_a_point_the_plant_does_not_publish_is_refused():
    with pytest.raises(ValueError, match="does not publish"):
        engine_with(Instrument("PT-101", "nodes", "N-999", "pressure"))

    with pytest.raises(ValueError, match="does not publish"):
        engine_with(Instrument("PT-101", "nodes", "N-103", "presure"))


def test_an_instrument_on_a_flag_is_refused():
    with pytest.raises(ValueError, match="not a number"):
        engine_with(Instrument("ZT-101", "equipment", "FV-101", "signal_ok"))


def test_an_instrument_tag_may_not_collide_with_a_device_or_another_instrument():
    with pytest.raises(ValueError, match="already in use"):
        engine_with(Instrument("FV-101", *FLOW))

    with pytest.raises(ValueError, match="already in use"):
        engine_with(transmitter(), Instrument("FT-101", "nodes", "N-103", "pressure"))


def test_one_point_has_one_instrument():
    with pytest.raises(ValueError, match="already reads"):
        engine_with(transmitter(), Instrument("FT-102", *FLOW))


def test_an_instrument_reads_only_a_measured_section():
    with pytest.raises(ValueError, match="reads one of"):
        Instrument("PT-101", "controllers", "PIC-101", "pv")


@pytest.mark.parametrize("bias", [math.nan, math.inf, True, "1.0"])
def test_a_bias_must_be_a_finite_number(bias):
    with pytest.raises(ValueError, match="finite number"):
        Instrument("PT-101", "nodes", "N-103", "pressure", bias=bias)


def test_indicate_leaves_the_truth_it_was_given_alone():
    truth = {"equipment": {}, "nodes": {"N-1": {"pressure": 50.0}}, "streams": {}}

    indicated = indicate(truth, [Instrument("PT-1", "nodes", "N-1", "pressure", bias=2.0)])

    assert indicated["nodes"]["N-1"]["pressure"] == 52.0
    assert truth["nodes"]["N-1"]["pressure"] == 50.0


# ---- build_snapshot -------------------------------------------------------


def snapshot_with(**overrides):
    kwargs = dict(
        sim_time=0.0,
        speed=1.0,
        running=True,
        equipment={"FV-101": {"position": 0.5}},
        nodes={"N-1": {"pressure": 52.0}},
    )
    kwargs.update(overrides)

    return build_snapshot(**kwargs)


def test_a_snapshot_built_without_truth_is_its_own_truth():
    snapshot = snapshot_with()

    assert snapshot.truth.as_dict() == {
        "equipment": snapshot.as_dict()["equipment"],
        "nodes": snapshot.as_dict()["nodes"],
        "streams": snapshot.as_dict()["streams"],
    }


def test_a_snapshot_carries_the_truth_it_was_given():
    truth = {
        "equipment": {"FV-101": {"position": 0.5}},
        "nodes": {"N-1": {"pressure": 50.0}},
        "streams": {},
    }

    snapshot = snapshot_with(truth=truth)
    truth["nodes"]["N-1"]["pressure"] = 0.0

    assert snapshot.nodes["N-1"]["pressure"] == 52.0
    assert snapshot.truth.nodes["N-1"]["pressure"] == 50.0


def test_truth_missing_a_measured_section_is_refused():
    with pytest.raises(ValueError, match="exactly the measured sections"):
        snapshot_with(truth={"equipment": {"FV-101": {"position": 0.5}}, "nodes": {}})


@pytest.mark.parametrize(
    "nodes",
    [{}, {"N-2": {"pressure": 50.0}}, {"N-1": {"pressure": 50.0, "temperature": 60.0}}],
    ids=["missing row", "different row", "extra field"],
)
def test_truth_shaped_unlike_the_indication_is_refused(nodes):
    truth = {"equipment": {"FV-101": {"position": 0.5}}, "nodes": nodes, "streams": {}}

    with pytest.raises(ValueError, match="differ in shape"):
        snapshot_with(truth=truth)
