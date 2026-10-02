"""
Injection profiles and condition onset - T13-3.

The build-plan tests: a ramp is a gradual trend rather than a step, a step
lands within one tick, and a condition onset fires once. The rest pin the
config decoding and the refusals it owes by name.
"""

import pytest

from app.disturbances.malfunction import Malfunction, MalfunctionRegistry, Step
from app.disturbances.profiles import (
    Ramp,
    WhenCondition,
    profile_from_config,
    start_condition_from_config,
)
from app.engine.snapshot import build_snapshot
from app.equipment.registry import EquipmentRegistry
from app.equipment.valve import ControlValve
from app.scenarios.triggers import ConditionEvaluationError, ConditionSyntaxError


def at(sim_time, pressure=0.0):
    return build_snapshot(
        sim_time=sim_time,
        speed=1.0,
        running=True,
        equipment={"V-101": {"pressure": pressure}},
    )


def fouled(profile=None, start_condition=None):
    valve = ControlValve("FV-101")
    equipment = EquipmentRegistry()
    equipment.register(valve)
    registry = MalfunctionRegistry(equipment)
    original = valve.capacity
    kwargs = {}

    if profile is not None:
        kwargs["profile"] = profile

    if start_condition is not None:
        kwargs["start_condition"] = start_condition

    registry.add(Malfunction("FV-101", "capacity", original * 0.5, **kwargs))

    return valve, registry, original


def test_a_ramp_produces_a_gradual_trend_not_a_step():
    valve, registry, original = fouled(profile=Ramp(duration_s=100.0))
    target = original * 0.5
    seen = []

    for t in range(0, 121, 10):
        registry.update(at(float(t)))
        seen.append(valve.capacity)

    assert seen[0] == original
    assert seen[-1] == target
    assert all(a > b for a, b in zip(seen[:10], seen[1:11]))
    assert seen[5] == pytest.approx(original + (target - original) * 0.5)


def test_a_ramp_runs_from_onset_not_from_time_zero():
    valve, registry, original = fouled(
        profile=Ramp(duration_s=100.0),
        start_condition=WhenCondition("V-101.pressure > 5"),
    )

    registry.update(at(500.0, pressure=1.0))
    assert valve.capacity == original

    registry.update(at(510.0, pressure=9.0))
    assert valve.capacity == original

    registry.update(at(560.0, pressure=9.0))
    assert valve.capacity == pytest.approx(original * 0.75)


def test_a_step_applies_within_one_tick():
    valve, registry, original = fouled(profile=Step())

    registry.update(at(0.0))

    assert valve.capacity == original * 0.5


def test_a_ramp_reverts_exactly_mid_flight():
    valve, registry, original = fouled(profile=Ramp(duration_s=100.0))

    registry.update(at(0.0))
    registry.update(at(40.0))
    registry.revert_all()

    assert valve.capacity == original


def test_a_condition_onset_waits_and_fires_once():
    valve, registry, original = fouled(start_condition=WhenCondition("V-101.pressure > 5"))

    registry.update(at(0.0, pressure=1.0))
    assert valve.capacity == original
    assert len(registry.pending) == 1

    registry.update(at(1.0, pressure=6.0))
    assert valve.capacity == original * 0.5
    assert len(registry.active) == 1

    # The condition clearing does not un-fire it, and a later crossing does not
    # re-capture the (already faulted) value as the original.
    registry.update(at(2.0, pressure=1.0))
    registry.update(at(3.0, pressure=9.0))
    assert valve.capacity == original * 0.5

    registry.revert_all()
    assert valve.capacity == original


@pytest.mark.parametrize("duration", [0, -5, float("inf"), float("nan"), True, "10"])
def test_a_ramp_needs_a_finite_positive_duration(duration):
    with pytest.raises(ValueError, match="duration_s"):
        Ramp(duration_s=duration)


def test_a_condition_onset_refuses_a_malformed_condition():
    with pytest.raises(ConditionSyntaxError):
        WhenCondition("not a condition")


def test_a_condition_onset_names_what_the_snapshot_lacks():
    with pytest.raises(ValueError, match="X-999"):
        WhenCondition("X-999.pressure > 1").is_met(at(0.0))


def test_config_decodes_every_supported_shape():
    assert profile_from_config({"type": "step"}, "m") == Step()
    assert profile_from_config({"type": "ramp", "duration_s": 30}, "m") == Ramp(30)
    assert start_condition_from_config({"type": "at_time"}, "m").sim_time == 0
    assert start_condition_from_config({"type": "at_time", "sim_time": 4}, "m").sim_time == 4

    onset = start_condition_from_config({"type": "condition", "condition": "V-101.pressure > 5"}, "m")
    assert onset == WhenCondition("V-101.pressure > 5")


@pytest.mark.parametrize(
    "decode, config, mention",
    [
        (profile_from_config, {"type": "sine"}, "supported are"),
        (profile_from_config, {"type": "step", "duration_s": 5}, "supported are"),
        (profile_from_config, {"type": "ramp"}, "supported are"),
        (profile_from_config, {"type": "ramp", "duration_s": 0}, "duration_s"),
        (start_condition_from_config, {"type": "later"}, "supported are"),
        (start_condition_from_config, {"type": "condition"}, "supported are"),
        (start_condition_from_config, {"type": "condition", "condition": "nonsense"}, "not a valid condition"),
        (start_condition_from_config, {"type": "condition", "condition": 5}, "must be a string"),
    ],
)
def test_config_refuses_what_it_does_not_know_by_name(decode, config, mention):
    with pytest.raises(ValueError, match=mention):
        decode(config, "malfunction FV-101.capacity")


def test_registry_validate_names_a_condition_the_snapshot_cannot_answer():
    _, registry, _ = fouled(start_condition=WhenCondition("X-999.pressure > 1"))

    with pytest.raises(ConditionEvaluationError, match="X-999"):
        registry.validate(at(0.0))


def test_registry_validate_passes_a_well_formed_registry_without_starting_anything():
    valve, registry, original = fouled(start_condition=WhenCondition("V-101.pressure > 5"))

    registry.validate(at(0.0, pressure=9.0))

    assert valve.capacity == original
    assert len(registry.pending) == 1
