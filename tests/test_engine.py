import json

import pytest

from app.engine.engine import Engine
from app.equipment.base import Equipment, INLET, OUTLET
from app.equipment.compressor import GasCompressor
from tests.golden_regression import (
    compressor_ramp_load,
    load_traces,
)


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


class Widget(Equipment):
    """Minimal test double: a slow-state ramp with no physics, so engine
    behaviour can be tested without depending on any real device's curve."""

    def __init__(self, tag="W-1"):
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.value = 0.0
        self.target = 0.0
        self.rate = 1.0

    def integrate(self, dt):
        self.value = self._move_toward(
            self.value,
            self.target,
            self.rate,
            dt,
        )

    def characteristic(self, flow):
        return 0.0

    def get_state(self):
        return {
            "tag": self.tag,
            "value": self.value,
        }


def test_step_integrates_every_device():
    widget = Widget()
    widget.target = 10.0

    engine = Engine({widget.tag: widget})
    engine.step(1.0)

    assert widget.value == pytest.approx(1.0)


def test_step_scales_by_clock_speed():
    widget = Widget()
    widget.target = 100.0

    engine = Engine({widget.tag: widget})
    engine.clock.set_speed(10.0)
    engine.step(1.0)

    assert widget.value == pytest.approx(10.0)


def test_step_is_a_no_op_while_paused():
    widget = Widget()
    widget.target = 10.0

    engine = Engine({widget.tag: widget})
    engine.clock.pause()
    engine.step(1.0)

    assert widget.value == pytest.approx(0.0)
    assert engine.clock.sim_time == pytest.approx(0.0)


def test_add_equipment_after_construction():
    engine = Engine()
    widget = Widget()
    widget.target = 4.0

    engine.add_equipment(widget)
    engine.step(1.0)

    assert widget.value == pytest.approx(1.0)
    assert "W-1" in engine.snapshot().equipment


def test_step_publishes_a_snapshot_matching_c4_shape():
    engine = Engine({"W-1": Widget()})

    snapshot = engine.step(1.0)

    assert set(snapshot.as_dict()) == C4_KEYS
    assert json.loads(json.dumps(snapshot.as_dict())) == snapshot.as_dict()


def test_snapshot_reports_sim_time_and_equipment_state():
    widget = Widget()
    widget.target = 5.0

    engine = Engine({widget.tag: widget})
    snapshot = engine.step(1.0)

    assert snapshot.sim_time == pytest.approx(1.0)
    assert snapshot.equipment["W-1"]["value"] == pytest.approx(1.0)


def test_determinism_same_steps_give_bit_identical_snapshots():
    def run():
        widget = Widget()
        widget.target = 1.0
        widget.rate = 0.037

        engine = Engine({widget.tag: widget})

        for step_num in range(50):
            if step_num == 20:
                widget.target = 0.3

            engine.step(0.1)

        return engine.snapshot().as_dict()

    assert run() == run()


def _assert_field_matches(step_num, field, actual, expected):
    if isinstance(expected, bool):
        assert actual == expected, (
            f"step {step_num}: {field} {actual} != {expected}"
        )
    else:
        assert actual == pytest.approx(expected), (
            f"step {step_num}: {field} {actual} != {expected}"
        )


def _replay_slow_state_through_engine(factory, scenario_fn, steps, fields):
    device = factory()
    engine = Engine({device.tag: device})

    observed = []

    for step_num in range(steps):
        observed.append({field: device.get_state()[field] for field in fields})
        scenario_fn(device, step_num)
        engine.step(1.0)

    observed.append({field: device.get_state()[field] for field in fields})

    return observed


COMPRESSOR_SLOW_FIELDS = (
    "load",
    "load_target",
    "running",
    "discharge_valve_position",
    "discharge_valve_target",
)

def test_compressor_golden_slow_state_reproduces_through_the_engine():
    """The engine only calls integrate(), never the device's own step(), so
    this checks the slow-state fields integrate() owns against the golden
    trace — not flow or pressure, which the engine does not compute until
    the network solver exists.

    Only the compressor is checked here: it is the one device on the
    Equipment contract on main as of this branch. Pump coverage belongs
    here once T1-4 (refactor/pump-onto-base) merges."""
    trace = load_traces("compressor")["ramp_load"]["trace"]

    observed = _replay_slow_state_through_engine(
        GasCompressor,
        compressor_ramp_load,
        20,
        COMPRESSOR_SLOW_FIELDS,
    )

    for step_num, (obs, expected) in enumerate(zip(observed, trace)):
        for field in COMPRESSOR_SLOW_FIELDS:
            _assert_field_matches(step_num, field, obs[field], expected[field])
