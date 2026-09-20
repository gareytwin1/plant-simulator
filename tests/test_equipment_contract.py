import copy
import importlib
import json
import math

import pytest

from app.equipment.base import (
    INLET,
    OUTLET,
    PRESERVED_ON_RESET,
    Equipment,
    Port,
    signed_square,
)


# Spans both directions and both sides of zero, because the solver reaches
# flows the plant never will and reaches them while it is still wrong.
CHARACTERISTIC_SWEEP = (
    -400.0,
    -200.0,
    -100.0,
    -25.0,
    -1.0,
    0.0,
    1.0,
    25.0,
    100.0,
    200.0,
    400.0,
)


DEVICE_MODULES = (
    "app.equipment.compressor",
    "app.equipment.pump",
    "app.equipment.valve",
)

NODE_PRESSURE_ATTRIBUTES = (
    "supply_pressure",
    "discharge_header_pressure",
    "header_pressure",
    "node_pressure",
)

PRIMITIVES = (
    bool,
    int,
    float,
    str,
    type(None),
)


class Machine(Equipment):
    def __init__(self, tag="K-901"):
        super().__init__(
            tag,
            ports={
                "suction": INLET,
                "discharge": OUTLET,
            },
        )

        self.running = False

        self.load = 0.0
        self.load_target = 0.0
        self.load_rate = 0.05

        self.shutoff_pressure_rise = 220.0
        self.machine_resistance = 0.002

    def integrate(self, dt):
        self.load = self._move_toward(
            self.load,
            self.load_target,
            self.load_rate,
            dt,
        )

    def characteristic(self, flow):
        return (
            self.shutoff_pressure_rise * self.load ** 2
            - self.machine_resistance * signed_square(flow)
        )

    def get_state(self):
        return {
            "tag": self.tag,
            "running": self.running,
            "load": self.load,
            "load_target": self.load_target,
        }


class Valve(Equipment):
    def __init__(self, tag="FV-901"):
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.position = 1.0
        self.position_target = 1.0
        self.stroke_rate = 0.05

        self.valve_resistance_scale = 0.0025

    def integrate(self, dt):
        self.position = self._move_toward(
            self.position,
            self.position_target,
            self.stroke_rate,
            dt,
        )

    def characteristic(self, flow):
        resistance = self.valve_resistance_scale * (
            1.0 / self.position ** 2
            - 1.0
        )

        return -resistance * signed_square(flow)

    def get_state(self):
        return {
            "tag": self.tag,
            "position": self.position,
            "position_target": self.position_target,
        }


def _registered_classes():
    for name in DEVICE_MODULES:
        importlib.import_module(name)

    return sorted(
        Equipment.registered().values(),
        key=lambda device_class: device_class.__name__,
    )


def _device_ids(device_class):
    return device_class.__name__


def _slow_state(device):
    return copy.deepcopy(
        {
            name: value
            for name, value in device.__dict__.items()
            if name not in PRESERVED_ON_RESET
        },
    )


REGISTERED = _registered_classes()


def test_contract_test_covers_at_least_the_reference_devices():
    assert Machine in REGISTERED
    assert Valve in REGISTERED


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_registered_class_implements_the_contract(device_class):
    device = device_class()

    assert isinstance(device.tag, str)
    assert device.tag

    assert isinstance(device.ports, dict)
    assert device.ports

    for name, port in device.ports.items():
        assert isinstance(port, Port)
        assert port.name == name
        assert port.direction in (INLET, OUTLET)

    for method in ("integrate", "characteristic", "get_state"):
        assert getattr(type(device), method) is not getattr(Equipment, method)


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_integrate_zero_is_a_no_op(device_class):
    device = device_class()

    before = _slow_state(device)
    device.integrate(0.0)

    assert _slow_state(device) == before
    assert device.get_state() == device_class().get_state()

    device.integrate(10.0)

    after_moving = _slow_state(device)
    device.integrate(0.0)

    assert _slow_state(device) == after_moving


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_reset_restores_construction_state_exactly(device_class):
    device = device_class()

    at_construction = _slow_state(device)

    for port in device.ports.values():
        port.connect("N-01")

    for attribute, value in list(device.__dict__.items()):
        if isinstance(value, float):
            setattr(device, attribute, value + 1.0)

    device.integrate(30.0)
    device.stray_attribute = "left over from a scenario"

    device.reset()

    assert _slow_state(device) == at_construction
    assert not hasattr(device, "stray_attribute")
    assert all(port.node == "N-01" for port in device.ports.values())


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_get_state_is_json_serialisable(device_class):
    device = device_class()
    device.integrate(10.0)

    state = device.get_state()

    assert isinstance(state, dict)

    for key, value in state.items():
        assert isinstance(key, str)
        assert isinstance(value, PRIMITIVES)

    assert json.loads(json.dumps(state)) == state


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_no_device_reads_a_node_pressure(device_class):
    device = device_class()

    for attribute in NODE_PRESSURE_ATTRIBUTES:
        assert not hasattr(device, attribute)

    for port in device.ports.values():
        assert not hasattr(port, "pressure")
        assert not hasattr(port, "flow")


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_characteristic_is_pure(device_class):
    device = device_class()
    device.integrate(10.0)

    before = _slow_state(device)

    for flow in CHARACTERISTIC_SWEEP:
        assert isinstance(device.characteristic(flow), float)

    assert _slow_state(device) == before


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_characteristic_is_finite_at_every_flow(device_class):
    device = device_class()
    device.integrate(10.0)

    for flow in CHARACTERISTIC_SWEEP:
        assert math.isfinite(device.characteristic(flow))


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_characteristic_never_rises_with_flow(device_class):
    device = device_class()
    device.integrate(10.0)

    curve = [
        device.characteristic(flow)
        for flow in CHARACTERISTIC_SWEEP
    ]

    for lower, higher in zip(curve, curve[1:]):
        assert higher <= lower


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_reverse_flow_never_reads_as_forward_flow(device_class):
    device = device_class()
    device.integrate(10.0)

    for flow in (1.0, 25.0, 100.0, 400.0):
        assert device.characteristic(-flow) >= device.characteristic(flow)


def test_machine_characteristic_rises_and_valve_characteristic_drops():
    machine = Machine()
    machine.load_target = 1.0
    machine.integrate(30.0)

    assert machine.characteristic(0.0) == pytest.approx(220.0)
    assert machine.characteristic(100.0) == pytest.approx(200.0)

    valve = Valve()
    valve.position_target = 0.50
    valve.integrate(30.0)

    assert valve.characteristic(0.0) == pytest.approx(0.0)
    assert valve.characteristic(100.0) == pytest.approx(-75.0)


def test_machine_rises_above_shutoff_when_flow_reverses():
    machine = Machine()
    machine.load_target = 1.0
    machine.integrate(30.0)

    assert machine.characteristic(-100.0) == pytest.approx(240.0)


def test_resistance_drop_follows_the_direction_of_flow():
    valve = Valve()
    valve.position_target = 0.50
    valve.integrate(30.0)

    assert valve.characteristic(-100.0) == pytest.approx(75.0)
    assert valve.characteristic(-100.0) == pytest.approx(
        -valve.characteristic(100.0),
    )


def test_signed_square_is_odd_through_zero():
    assert signed_square(0.0) == pytest.approx(0.0)
    assert signed_square(12.0) == pytest.approx(144.0)
    assert signed_square(-12.0) == pytest.approx(-144.0)


def test_signed_square_increases_everywhere():
    values = [
        signed_square(flow)
        for flow in CHARACTERISTIC_SWEEP
    ]

    for lower, higher in zip(values, values[1:]):
        assert higher > lower


def test_integrate_moves_slow_state_at_its_rate():
    machine = Machine()
    machine.load_target = 1.0

    machine.integrate(0.0)

    assert machine.load == pytest.approx(0.0)

    machine.integrate(1.0)

    assert machine.load == pytest.approx(0.05)

    machine.integrate(100.0)

    assert machine.load == pytest.approx(1.0)


def test_reset_returns_a_ramped_device_to_construction_state():
    machine = Machine()
    machine.load_target = 1.0
    machine.integrate(30.0)

    assert machine.load == pytest.approx(1.0)

    machine.reset()

    assert machine.load == pytest.approx(0.0)
    assert machine.load_target == pytest.approx(0.0)
    assert machine.get_state() == Machine().get_state()


def test_ports_are_named_and_directional():
    machine = Machine()

    assert sorted(machine.ports) == ["discharge", "suction"]
    assert machine.port("suction").direction == INLET
    assert machine.port("discharge").direction == OUTLET

    with pytest.raises(KeyError):
        machine.port("vent")


def test_port_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        Port("suction", "sideways")


def test_port_refuses_to_carry_a_solver_output():
    port = Port("suction", INLET)

    for attribute in ("pressure", "flow", "temperature"):
        with pytest.raises(AttributeError):
            setattr(port, attribute, 800.0)


def test_port_connects_and_disconnects():
    port = Port("suction", INLET)

    assert port.connected is False
    assert port.node is None

    port.connect("N-01")

    assert port.connected is True
    assert port.node == "N-01"

    port.disconnect()

    assert port.connected is False


def test_registered_returns_a_copy_of_the_registry():
    registry = Equipment.registered()
    registry["Bogus"] = Machine

    assert "Bogus" not in Equipment.registered()
    assert Equipment.registered()["Machine"] is Machine
