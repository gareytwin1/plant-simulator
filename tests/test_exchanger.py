import pytest

from app.equipment.base import LIQUID
from app.equipment.exchanger import HeatExchanger
from app.plant.thermo import StreamState, heat_capacity_rate


def process_stream(flow, temperature):
    return StreamState(flow=flow, temperature=temperature, phase=LIQUID)


def test_initial_state():
    exchanger = HeatExchanger()

    assert exchanger.fouling == 0.0
    assert exchanger.metal_temperature == pytest.approx(exchanger.cold_temperature)
    assert exchanger.duty == pytest.approx(0.0)


def test_duty_at_steady_state_is_ua_times_approach_temperature():
    exchanger = HeatExchanger()
    exchanger.inlet_temperature = 300.0

    for _ in range(10_000):
        exchanger.integrate(1.0)

    assert exchanger.metal_temperature == pytest.approx(300.0)
    assert exchanger.duty == pytest.approx(
        exchanger.ua * (300.0 - exchanger.cold_temperature),
    )


def test_fouling_reduces_ua_and_reduces_duty():
    clean = HeatExchanger()
    clean.metal_temperature = 200.0

    fouled = HeatExchanger()
    fouled.fouling = 0.5
    fouled.metal_temperature = 200.0

    assert fouled.effective_ua == pytest.approx(clean.effective_ua * 0.5)
    assert fouled.duty == pytest.approx(clean.duty * 0.5)
    assert fouled.duty < clean.duty


def test_fouling_to_one_removes_all_duty():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 200.0
    exchanger.fouling = 1.0

    assert exchanger.effective_ua == pytest.approx(0.0)
    assert exchanger.duty == pytest.approx(0.0)


def test_energy_balance_closes_across_the_exchanger():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 250.0

    inlet = process_stream(flow=500.0, temperature=300.0)
    outlet_temperature = exchanger.outlet_temperature(inlet)

    process_duty = heat_capacity_rate(inlet) * (inlet.temperature - outlet_temperature)

    assert process_duty == pytest.approx(exchanger.duty)


def test_zero_flow_leaves_the_inlet_temperature_unchanged():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 250.0

    inlet = process_stream(flow=0.0, temperature=300.0)

    assert exchanger.outlet_temperature(inlet) == pytest.approx(300.0)


def test_setting_the_inlet_temperature_does_not_move_duty_before_integrate():
    exchanger = HeatExchanger()
    exchanger.metal_temperature = 200.0
    before = exchanger.duty

    exchanger.inlet_temperature = 400.0

    assert exchanger.duty == pytest.approx(before)


def test_cutting_the_inlet_temperature_raises_downstream_temperature_gradually():
    exchanger = HeatExchanger()
    exchanger.inlet_temperature = 300.0

    for _ in range(10_000):
        exchanger.integrate(1.0)

    # Cut the process heat load: a run of process fluid now arrives cooler.
    # Probe with the same arriving stream at every check, so the only thing
    # that can move the reading is the exchanger's own state.
    exchanger.inlet_temperature = 150.0
    probe = process_stream(flow=500.0, temperature=150.0)

    exchanger.integrate(1.0)
    outlet_after_one_step = exchanger.outlet_temperature(probe)

    for _ in range(10_000):
        exchanger.integrate(1.0)

    outlet_at_new_steady_state = exchanger.outlet_temperature(probe)

    # If duty stepped instantly to its new steady value, one second in
    # would already read the fully-settled outlet temperature for this
    # stream. It does not: at the default response rate the metal has
    # covered only 2 of the 150-degree gap after one second, so the first
    # reading sits well short of where it ends up settling.
    assert abs(outlet_after_one_step - outlet_at_new_steady_state) > 1.0

    # And it settles at the textbook Q = UA * approach steady state.
    assert exchanger.duty == pytest.approx(
        exchanger.ua * (150.0 - exchanger.cold_temperature),
    )


def test_metal_temperature_does_not_overshoot_its_target():
    exchanger = HeatExchanger()
    exchanger.inlet_temperature = 500.0

    exchanger.integrate(1.0)

    assert exchanger.cold_temperature < exchanger.metal_temperature < 500.0
