import pytest

from app.engine.engine import Engine
from app.equipment.base import VAPOR
from app.equipment.furnace import Furnace
from app.plant.thermo import StreamState, heat_capacity_rate
from app.plant.topology import Branch, Node, Topology


def process_stream(flow, temperature):
    return StreamState(flow=flow, temperature=temperature, phase=VAPOR)


def leaving(furnace, stream):
    return furnace.leaving_temperature(stream, 0.0, 0.0)


def test_initial_state():
    furnace = Furnace()

    assert furnace.duty_setpoint == 0.0
    assert furnace.firing_rate == 0.0

    stream = process_stream(flow=500.0, temperature=300.0)
    assert leaving(furnace, stream) == pytest.approx(300.0)
    assert furnace.duty(stream) == pytest.approx(0.0)


def test_step_in_duty_ramps_the_firing_rate_rather_than_jumping():
    furnace = Furnace()
    furnace.set_duty_setpoint(furnace.max_duty)

    furnace.integrate(1.0)

    assert 0.0 < furnace.firing_rate < furnace.max_duty


def test_a_settled_step_in_duty_matches_the_closed_form_energy_balance():
    furnace = Furnace()
    furnace.set_duty_setpoint(furnace.max_duty)

    for _ in range(1000):
        furnace.integrate(1.0)

    assert furnace.firing_rate == pytest.approx(furnace.max_duty)

    stream = process_stream(flow=500.0, temperature=300.0)
    rate = abs(heat_capacity_rate(stream))
    expected = stream.temperature + furnace.firing_rate / rate

    assert leaving(furnace, stream) == pytest.approx(expected)


def test_outlet_temperature_rises_gradually_rather_than_in_one_step():
    furnace = Furnace()
    stream = process_stream(flow=500.0, temperature=300.0)

    before = leaving(furnace, stream)

    furnace.set_duty_setpoint(furnace.max_duty)
    furnace.integrate(1.0)
    outlet_after_one_step = leaving(furnace, stream)

    for _ in range(1000):
        furnace.integrate(1.0)

    outlet_at_new_steady_state = leaving(furnace, stream)

    assert before < outlet_after_one_step < outlet_at_new_steady_state

    total_rise = outlet_at_new_steady_state - before
    first_step_rise = outlet_after_one_step - before

    assert first_step_rise < total_rise * 0.2


def test_trip_to_zero_firing_cools_at_the_expected_rate():
    furnace = Furnace()
    furnace.set_duty_setpoint(furnace.max_duty)

    for _ in range(1000):
        furnace.integrate(1.0)

    assert furnace.firing_rate == pytest.approx(furnace.max_duty)

    furnace.trip()
    furnace.integrate(1.0)

    assert furnace.duty_setpoint == pytest.approx(0.0)
    assert furnace.firing_rate == pytest.approx(
        furnace.max_duty - furnace.firing_ramp_rate,
    )

    for _ in range(1000):
        furnace.integrate(1.0)

    assert furnace.firing_rate == pytest.approx(0.0)

    stream = process_stream(flow=500.0, temperature=300.0)
    assert leaving(furnace, stream) == pytest.approx(300.0)


def test_duty_setpoint_clamps_to_the_design_limit():
    furnace = Furnace()

    furnace.set_duty_setpoint(furnace.max_duty * 10.0)

    assert furnace.duty_setpoint == pytest.approx(furnace.max_duty)


def test_duty_setpoint_clamps_negative_targets_to_zero():
    furnace = Furnace()

    furnace.set_duty_setpoint(-100.0)

    assert furnace.duty_setpoint == pytest.approx(0.0)


def test_firing_rate_never_exceeds_the_clamped_setpoint():
    furnace = Furnace()
    furnace.set_duty_setpoint(furnace.max_duty * 10.0)

    for _ in range(10_000):
        furnace.integrate(1.0)

    assert furnace.firing_rate == pytest.approx(furnace.max_duty)


def test_energy_balance_closes_across_the_furnace():
    furnace = Furnace()
    furnace.firing_rate = 500_000.0

    inlet = process_stream(flow=500.0, temperature=300.0)
    outlet_temperature = leaving(furnace, inlet)

    process_duty = abs(heat_capacity_rate(inlet)) * (
        outlet_temperature - inlet.temperature
    )

    assert process_duty == pytest.approx(furnace.duty(inlet))


def test_zero_flow_leaves_the_inlet_temperature_unchanged():
    furnace = Furnace()
    furnace.firing_rate = 500_000.0

    inlet = process_stream(flow=0.0, temperature=300.0)

    assert leaving(furnace, inlet) == pytest.approx(300.0)
    assert furnace.duty(inlet) == pytest.approx(0.0)


def test_no_firing_leaves_the_inlet_temperature_unchanged():
    furnace = Furnace()

    stream = process_stream(flow=500.0, temperature=300.0)

    assert leaving(furnace, stream) == pytest.approx(300.0)
    assert furnace.duty(stream) == pytest.approx(0.0)


@pytest.mark.parametrize("flow", (200.0, 50.0, 10.0, 0.1))
def test_reversed_flow_is_heated_the_same_as_forward_flow(flow):
    furnace = Furnace()
    furnace.firing_rate = 500_000.0

    forward = process_stream(flow=flow, temperature=300.0)
    reversed_flow = process_stream(flow=-flow, temperature=300.0)

    assert leaving(furnace, reversed_flow) == pytest.approx(
        leaving(furnace, forward),
    )
    assert furnace.duty(reversed_flow) == pytest.approx(furnace.duty(forward))
    assert furnace.duty(forward) > 0.0


def test_setting_the_duty_setpoint_does_not_move_duty_before_integrate():
    furnace = Furnace()
    furnace.firing_rate = 200_000.0

    stream = process_stream(flow=500.0, temperature=300.0)
    before = furnace.duty(stream)

    furnace.set_duty_setpoint(furnace.max_duty)

    assert furnace.duty(stream) == pytest.approx(before)


def test_engine_transport_heats_the_stream_through_the_furnace():
    """A minimal plant with the furnace as the sole branch: the engine's
    transport (T6-5) should call `leaving_temperature` and publish a hotter
    stream, proving the ThermalDevice hookup actually works end to end."""
    furnace = Furnace()
    furnace.port("inlet").declare(phase=VAPOR, purpose="process")
    furnace.port("outlet").declare(phase=VAPOR, purpose="process")
    furnace.firing_rate = 5_000_000.0

    nodes = {
        "COLD": Node("COLD", 100.0, is_boundary=True),
        "HOT": Node("HOT", 50.0, is_boundary=True),
    }
    branch = Branch("B-1", nodes["COLD"], nodes["HOT"], furnace)

    engine = Engine(
        [furnace],
        topology=Topology(nodes.values(), [branch]),
        boundary_temperatures={"COLD": 300.0, "HOT": 300.0},
    )

    snapshot = engine.step(1.0)

    assert snapshot.streams["B-1"]["flow"] > 0.0
    assert snapshot.streams["B-1"]["temperature"] > 300.0
