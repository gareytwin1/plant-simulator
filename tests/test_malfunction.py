"""
Malfunction model and registry - T13-1, contract C8.

The build-plan tests come first: a malfunction applies and reverts cleanly
(checked end to end, through the solver, against an untouched twin plant),
writing a solver output or slow state raises, and every device type the
plant can load resolves to an allowlist. The rest pin the lifecycle rules
app/disturbances/malfunction.py states.
"""

import math
from dataclasses import dataclass

import pytest

from app.disturbances.malfunction import (
    WRITABLE,
    AtTime,
    Malfunction,
    MalfunctionRegistry,
    NotWritable,
    Step,
    writable,
)
from app.engine.engine import Engine
from app.engine.instruments import Instrument
from app.engine.snapshot import build_snapshot
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.exchanger import HeatExchanger
from app.equipment.registry import EquipmentRegistry
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.plant.loader import DEVICE_TYPES

from tests.test_engine_solver import commanded


def running_plant():
    engine = Engine.from_plant(
        commanded("gas_compression"),
        boundary_temperatures={"N-201": 80.0},
    )
    equipment = EquipmentRegistry()

    for device in engine.equipment.values():
        equipment.register(device)

    return engine, MalfunctionRegistry(equipment)


def settle(engine, steps=5):
    for _ in range(steps):
        snapshot = engine.step(1.0)

    return snapshot


def flat(section):
    return {
        (name, key): value
        for name, row in section.items()
        for key, value in row.items()
        if isinstance(value, float)
    }


def at(sim_time):
    return build_snapshot(sim_time=sim_time, speed=1.0, running=True, equipment={})


def devices(*items):
    equipment = EquipmentRegistry()

    for device in items:
        equipment.register(device)

    return MalfunctionRegistry(equipment)


@dataclass(frozen=True)
class Linear:
    duration: float

    def fraction(self, elapsed):
        return min(elapsed / self.duration, 1.0)


# ---- applies and reverts cleanly ------------------------------------------


def test_a_worn_compressor_loses_flow_through_the_solver():
    engine, malfunctions = running_plant()
    healthy = settle(engine)
    design = engine.equipment["K-101"].shutoff_pressure_rise

    malfunctions.add(Malfunction("K-101", "shutoff_pressure_rise", design * 0.95))
    malfunctions.update(engine.snapshot())
    worn = settle(engine)

    assert healthy.streams["B-K-101"]["flow"] > 0.0
    assert worn.streams["B-K-101"]["flow"] > 0.0
    assert worn.streams["B-K-101"]["flow"] < healthy.streams["B-K-101"]["flow"]


def test_lost_efficiency_heats_the_interstage():
    engine, malfunctions = running_plant()
    healthy = settle(engine)

    malfunctions.add(Malfunction("K-101", "polytropic_efficiency", 0.55))
    malfunctions.update(engine.snapshot())
    degraded = settle(engine)

    assert degraded.streams["B-K-101"]["flow"] > 0.0
    assert degraded.nodes["N-202"]["temperature"] > healthy.nodes["N-202"]["temperature"]


def test_a_reverted_plant_returns_to_the_undisturbed_operating_point():
    engine, malfunctions = running_plant()
    twin, _ = running_plant()
    settle(engine)
    settle(twin)

    malfunction = Malfunction("K-101", "shutoff_pressure_rise", 150.0)
    malfunctions.add(malfunction)
    malfunctions.update(engine.snapshot())
    settle(engine)
    settle(twin)

    malfunctions.revert(malfunction)

    after = settle(engine, 20).as_dict()
    untouched = settle(twin, 20).as_dict()

    # Device state comes back exactly. The solved field comes back to within
    # float rounding only: the solver warm-starts from the last solution, so
    # the disturbed path reaches the same root by a different last few bits.
    assert after["equipment"] == untouched["equipment"]
    for section in ("nodes", "streams"):
        assert flat(after[section]) == pytest.approx(flat(untouched[section]), rel=1e-12)
    assert malfunctions.active == ()
    assert malfunctions.pending == ()


def test_revert_restores_the_original_value_exactly():
    exchanger = HeatExchanger("E-101")
    exchanger.fouling = 0.1
    malfunctions = devices(exchanger)
    malfunction = Malfunction("E-101", "fouling", 0.3, profile=Linear(10.0))

    malfunctions.add(malfunction)
    malfunctions.update(at(0.0))
    malfunctions.update(at(3.0))
    malfunctions.revert(malfunction)

    assert exchanger.fouling == 0.1


def test_a_step_lands_exactly_on_its_value():
    exchanger = HeatExchanger("E-101")
    exchanger.fouling = 0.1
    malfunctions = devices(exchanger)

    malfunctions.add(Malfunction("E-101", "fouling", 0.3))
    malfunctions.update(at(0.0))

    assert exchanger.fouling == 0.3


def test_reverting_a_pending_malfunction_writes_nothing():
    valve = ControlValve("FV-101")
    malfunctions = devices(valve)
    malfunction = Malfunction("FV-101", "capacity", 40.0, start_condition=AtTime(60.0))

    malfunctions.add(malfunction)
    malfunctions.update(at(10.0))
    malfunctions.revert(malfunction)

    assert valve.capacity == 100.0
    assert malfunctions.pending == ()


def test_revert_all_restores_every_parameter():
    valve = ControlValve("FV-101")
    exchanger = HeatExchanger("E-101")
    malfunctions = devices(valve, exchanger)

    malfunctions.add(Malfunction("FV-101", "capacity", 40.0))
    malfunctions.add(Malfunction("E-101", "cold_temperature", 120.0))
    malfunctions.add(Malfunction("E-101", "fouling", 0.5, start_condition=AtTime(99.0)))
    malfunctions.update(at(0.0))
    malfunctions.revert_all()

    assert valve.capacity == 100.0
    assert exchanger.cold_temperature == 90.0
    assert exchanger.fouling == 0.0
    assert malfunctions.active == ()
    assert malfunctions.pending == ()


# ---- writing a solver output raises ---------------------------------------


@pytest.mark.parametrize(
    ("target", "parameter"),
    [
        ("FV-101", "flow"),
        ("FV-101", "pressure"),
        ("V-101", "pressure"),
        ("V-101", "level"),
        ("E-101", "temperature"),
        ("E-101", "inlet_temperature"),
    ],
)
def test_writing_a_solver_output_raises(target, parameter):
    malfunctions = devices(ControlValve("FV-101"), Vessel("V-101"), HeatExchanger("E-101"))

    with pytest.raises(NotWritable, match=parameter):
        malfunctions.add(Malfunction(target, parameter, 1.0))


@pytest.mark.parametrize(
    ("target", "parameter"),
    [
        ("FV-101", "position"),
        ("FV-101", "position_target"),
        ("E-101", "metal_temperature"),
        ("K-101", "load"),
    ],
)
def test_writing_slow_state_raises(target, parameter):
    malfunctions = devices(ControlValve("FV-101"), HeatExchanger("E-101"), GasCompressor("K-101"))

    with pytest.raises(NotWritable, match=parameter):
        malfunctions.add(Malfunction(target, parameter, 0.5))


def test_a_refused_malfunction_leaves_the_device_untouched():
    vessel = Vessel("V-101")
    before = vessel.get_state()
    malfunctions = devices(vessel)

    with pytest.raises(NotWritable):
        malfunctions.add(Malfunction("V-101", "level", 0.9))

    malfunctions.update(at(0.0))

    assert vessel.get_state() == before
    assert malfunctions.pending == ()


def test_a_subclass_does_not_inherit_its_parents_allowlist():
    class SpecialValve(ControlValve):
        pass

    malfunctions = devices(SpecialValve("FV-102"))

    with pytest.raises(NotWritable, match="no malfunction allowlist"):
        malfunctions.add(Malfunction("FV-102", "capacity", 40.0))


def test_an_out_of_range_value_is_refused_before_onset():
    exchanger = HeatExchanger("E-101")
    malfunctions = devices(exchanger)

    with pytest.raises(ValueError, match="fouling"):
        malfunctions.add(Malfunction("E-101", "fouling", 1.5))

    assert exchanger.fouling == 0.0
    assert malfunctions.pending == ()


@pytest.mark.parametrize("value", [math.nan, math.inf, True, "0.5"])
def test_a_malfunction_value_must_be_a_finite_number(value):
    with pytest.raises(ValueError, match="value must be"):
        Malfunction("E-101", "fouling", value)


def test_an_unknown_target_raises():
    malfunctions = devices(ControlValve("FV-101"))

    with pytest.raises(KeyError, match="FV-999"):
        malfunctions.add(Malfunction("FV-999", "capacity", 40.0))


# ---- the registry resolves every catalogued type --------------------------


def app_device_classes():
    return [
        cls
        for cls in Equipment.registered().values()
        if cls.__module__.startswith("app.")
    ]


def test_every_loadable_device_type_has_an_allowlist():
    for name, cls in DEVICE_TYPES.items():
        assert cls in WRITABLE, f"C3 type {name!r} ({cls.__name__}) has no allowlist"


def test_every_production_device_class_has_an_allowlist():
    missing = [cls.__name__ for cls in app_device_classes() if cls not in WRITABLE]

    assert missing == []


def sample(cls):
    if cls is Instrument:
        return Instrument("PT-101", "nodes", "N-101", "pressure")

    return cls()


@pytest.mark.parametrize("cls", list(WRITABLE), ids=lambda cls: cls.__name__)
def test_every_allowlisted_parameter_is_a_validating_numeric_property(cls):
    device = sample(cls)

    assert writable(device) == WRITABLE[cls]

    for parameter in WRITABLE[cls]:
        attribute = getattr(cls, parameter, None)

        assert isinstance(attribute, property), f"{cls.__name__}.{parameter}"
        assert attribute.fset is not None, f"{cls.__name__}.{parameter}"
        assert isinstance(getattr(device, parameter), float)


# ---- lifecycle ------------------------------------------------------------


def test_a_malfunction_waits_for_its_start_condition():
    valve = ControlValve("FV-101")
    malfunctions = devices(valve)
    malfunction = Malfunction("FV-101", "capacity", 40.0, start_condition=AtTime(30.0))
    malfunctions.add(malfunction)

    malfunctions.update(at(29.0))

    assert valve.capacity == 100.0
    assert malfunctions.pending == (malfunction,)

    malfunctions.update(at(30.0))

    assert valve.capacity == 40.0
    assert malfunctions.active == (malfunction,)


def test_a_profile_is_timed_from_onset_not_from_zero():
    valve = ControlValve("FV-101")
    malfunctions = devices(valve)
    malfunctions.add(
        Malfunction(
            "FV-101",
            "capacity",
            50.0,
            profile=Linear(10.0),
            start_condition=AtTime(100.0),
        ),
    )

    malfunctions.update(at(100.0))

    assert valve.capacity == 100.0

    malfunctions.update(at(104.0))

    assert valve.capacity == pytest.approx(80.0)

    malfunctions.update(at(110.0))

    assert valve.capacity == 50.0


def test_the_original_value_is_captured_at_onset():
    valve = ControlValve("FV-101")
    malfunctions = devices(valve)
    malfunction = Malfunction("FV-101", "capacity", 40.0, start_condition=AtTime(10.0))
    malfunctions.add(malfunction)

    valve.capacity = 80.0
    malfunctions.update(at(10.0))
    malfunctions.revert(malfunction)

    assert valve.capacity == 80.0


def test_a_second_malfunction_on_the_same_parameter_is_refused():
    malfunctions = devices(ControlValve("FV-101"))
    malfunctions.add(Malfunction("FV-101", "capacity", 40.0))

    with pytest.raises(ValueError, match="already has a malfunction"):
        malfunctions.add(Malfunction("FV-101", "capacity", 20.0))


def test_a_profile_outside_zero_to_one_raises():
    @dataclass(frozen=True)
    class Overshoot:
        def fraction(self, elapsed):
            return 1.5

    malfunctions = devices(ControlValve("FV-101"))
    malfunctions.add(Malfunction("FV-101", "capacity", 40.0, profile=Overshoot()))

    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        malfunctions.update(at(0.0))


def test_reverting_an_unregistered_malfunction_raises():
    malfunctions = devices(ControlValve("FV-101"))

    with pytest.raises(KeyError):
        malfunctions.revert(Malfunction("FV-101", "capacity", 40.0))


def test_step_and_at_time_are_the_defaults():
    malfunction = Malfunction("FV-101", "capacity", 40.0)

    assert malfunction.profile == Step()
    assert malfunction.start_condition == AtTime(0.0)
