import math

import pytest

from app.engine.engine import Engine
from app.equipment.base import LIQUID
from app.equipment.exchanger import HeatExchanger
from app.plant.thermo import StreamState, heat_capacity_rate
from app.plant.topology import Branch, Node, Topology


def process_stream(flow, temperature):
    return StreamState(flow=flow, temperature=temperature, phase=LIQUID)


def leaving(exchanger, stream):
    return exchanger.leaving_temperature(stream, 0.0, 0.0)


def test_initial_state():
    exchanger = HeatExchanger()

    assert exchanger.fouling == 0.0
    # A fresh metal wall starts at the coolant's own temperature: zero
    # capability until integrate() has warmed it up (T6-3's documented
    # interim behaviour).
    assert exchanger.metal_temperature == pytest.approx(exchanger.cold_temperature)

    stream = process_stream(flow=500.0, temperature=300.0)
    assert leaving(exchanger, stream) == pytest.approx(300.0)
    assert exchanger.duty(stream) == pytest.approx(0.0)


def test_fully_settled_matches_the_textbook_effectiveness_formula():
    exchanger = HeatExchanger()
    exchanger.inlet_temperature = 300.0

    for _ in range(10_000):
        exchanger.integrate(1.0)

    assert exchanger.metal_temperature == pytest.approx(300.0)

    stream = process_stream(flow=500.0, temperature=300.0)
    rate = abs(heat_capacity_rate(stream))
    expected = exchanger.cold_temperature + (
        stream.temperature - exchanger.cold_temperature
    ) * math.exp(-exchanger.effective_ua / rate)

    assert leaving(exchanger, stream) == pytest.approx(expected)


def test_fouling_reduces_duty():
    stream = process_stream(flow=500.0, temperature=300.0)

    clean = HeatExchanger()
    clean.metal_temperature = 300.0

    fouled = HeatExchanger()
    fouled.fouling = 0.5
    fouled.metal_temperature = 300.0

    assert fouled.effective_ua == pytest.approx(clean.effective_ua * 0.5)
    assert fouled.duty(stream) < clean.duty(stream)


def test_fouling_to_one_removes_all_duty():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 300.0
    exchanger.fouling = 1.0

    stream = process_stream(flow=500.0, temperature=300.0)

    assert exchanger.effective_ua == pytest.approx(0.0)
    assert leaving(exchanger, stream) == pytest.approx(300.0)
    assert exchanger.duty(stream) == pytest.approx(0.0)


def test_energy_balance_closes_across_the_exchanger():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 250.0

    inlet = process_stream(flow=500.0, temperature=300.0)
    outlet_temperature = leaving(exchanger, inlet)

    process_duty = abs(heat_capacity_rate(inlet)) * (
        inlet.temperature - outlet_temperature
    )

    assert process_duty == pytest.approx(exchanger.duty(inlet))


def test_zero_flow_leaves_the_inlet_temperature_unchanged():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 250.0

    inlet = process_stream(flow=0.0, temperature=300.0)

    assert leaving(exchanger, inlet) == pytest.approx(300.0)
    assert exchanger.duty(inlet) == pytest.approx(0.0)


@pytest.mark.parametrize("flow", (200.0, 50.0, 10.0, 0.1))
def test_outlet_never_drops_below_the_coolant_at_any_flow(flow):
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 300.0

    stream = process_stream(flow=flow, temperature=300.0)
    outlet = leaving(exchanger, stream)

    assert outlet >= exchanger.cold_temperature
    assert outlet <= stream.temperature


@pytest.mark.parametrize("flow", (200.0, 50.0, 10.0, 0.1))
def test_reversed_flow_is_cooled_the_same_as_forward_flow(flow):
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 300.0

    forward = process_stream(flow=flow, temperature=300.0)
    reversed_flow = process_stream(flow=-flow, temperature=300.0)

    assert leaving(exchanger, reversed_flow) == pytest.approx(
        leaving(exchanger, forward),
    )
    assert exchanger.duty(reversed_flow) == pytest.approx(exchanger.duty(forward))
    assert exchanger.duty(forward) > 0.0


def test_metal_colder_than_the_coolant_clamps_to_no_cooling_rather_than_negative():
    exchanger = HeatExchanger()
    # Reachable in a running plant: metal_temperature chases inlet_temperature,
    # and nothing stops that target from sitting below cold_temperature.
    exchanger.metal_temperature = exchanger.cold_temperature - 20.0

    stream = process_stream(flow=500.0, temperature=300.0)

    assert leaving(exchanger, stream) == pytest.approx(300.0)
    assert exchanger.duty(stream) == pytest.approx(0.0)


def test_setting_the_inlet_temperature_does_not_move_duty_before_integrate():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 200.0

    stream = process_stream(flow=500.0, temperature=300.0)
    before = exchanger.duty(stream)

    exchanger.inlet_temperature = 400.0

    assert exchanger.duty(stream) == pytest.approx(before)


def test_cutting_the_metal_temperature_raises_downstream_temperature_gradually():
    exchanger = HeatExchanger()
    exchanger.inlet_temperature = 300.0

    for _ in range(10_000):
        exchanger.integrate(1.0)

    # What is actually arriving stays at 300 throughout; only the metal's
    # own target is cut, so any movement in the reading below can only come
    # from the metal's lag.
    probe = process_stream(flow=500.0, temperature=300.0)
    settled_outlet = leaving(exchanger, probe)

    exchanger.inlet_temperature = 150.0

    exchanger.integrate(1.0)
    outlet_after_one_step = leaving(exchanger, probe)

    for _ in range(10_000):
        exchanger.integrate(1.0)

    outlet_at_new_steady_state = leaving(exchanger, probe)

    # Cutting the metal's capability raises the downstream temperature -
    # monotonically, and gradually rather than in one step: one second in,
    # the reading has covered only a small fraction of the eventual rise.
    assert settled_outlet < outlet_after_one_step < outlet_at_new_steady_state

    total_rise = outlet_at_new_steady_state - settled_outlet
    first_step_rise = outlet_after_one_step - settled_outlet

    assert first_step_rise < total_rise * 0.2


def test_metal_temperature_does_not_overshoot_its_target():
    exchanger = HeatExchanger()
    exchanger.inlet_temperature = 500.0

    exchanger.integrate(1.0)

    assert exchanger.cold_temperature < exchanger.metal_temperature < 500.0


def test_engine_transport_cools_the_stream_through_the_exchanger():
    """A minimal plant with the exchanger as the sole branch: the engine's
    transport (T6-5) should call `leaving_temperature` and publish a colder
    stream, proving the ThermalDevice hookup actually works end to end."""
    exchanger = HeatExchanger()
    exchanger.port("inlet").declare(phase=LIQUID, purpose="process")
    exchanger.port("outlet").declare(phase=LIQUID, purpose="process")
    exchanger.metal_temperature = 300.0

    nodes = {
        "HOT": Node("HOT", 100.0, is_boundary=True),
        "COLD": Node("COLD", 50.0, is_boundary=True),
    }
    branch = Branch("B-1", nodes["HOT"], nodes["COLD"], exchanger)

    engine = Engine(
        [exchanger],
        topology=Topology(nodes.values(), [branch]),
        boundary_temperatures={"HOT": 300.0, "COLD": exchanger.cold_temperature},
    )

    snapshot = engine.step(1.0)

    assert snapshot.streams["B-1"]["flow"] > 0.0
    assert snapshot.streams["B-1"]["temperature"] < 300.0
    assert snapshot.streams["B-1"]["temperature"] >= exchanger.cold_temperature
