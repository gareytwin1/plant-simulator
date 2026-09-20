import pytest

from app.equipment.pump import CentrifugalPump


def test_initial_pump_state():
    pump = CentrifugalPump()

    assert pump.running is False
    assert pump.speed == pytest.approx(0.0)
    assert pump.speed_target == pytest.approx(0.0)


def test_the_pump_owns_no_flow_or_pressure():
    """T4-4 moved the operating point to the solver — see the matching
    compressor test.
    """
    pump = CentrifugalPump()

    for attribute in (
        "flow",
        "suction_pressure",
        "discharge_pressure",
        "upstream_boundary_pressure",
        "downstream_boundary_pressure",
        "step",
    ):
        assert not hasattr(pump, attribute)


def test_pump_speed_reaches_its_target_and_holds():
    pump = CentrifugalPump()

    pump.set_speed_target(1.0)
    pump.start()

    for _ in range(10):
        pump.integrate(1.0)

    assert pump.speed == pytest.approx(1.0)
    assert pump.characteristic(0.0) == pytest.approx(75.0)


def test_pump_half_speed_curve_follows_the_affinity_law():
    pump = CentrifugalPump()

    pump.set_speed_target(0.50)
    pump.start()

    for _ in range(5):
        pump.integrate(1.0)

    assert pump.speed == pytest.approx(0.50)
    assert pump.characteristic(0.0) == pytest.approx(75.0 * 0.25)


def test_pump_stop_reduces_speed():
    pump = CentrifugalPump()

    pump.set_speed_target(1.0)
    pump.start()

    for _ in range(10):
        pump.integrate(1.0)

    pump.stop()
    pump.integrate(1.0)

    assert pump.running is False
    assert pump.speed_target == pytest.approx(0.0)
    assert pump.speed == pytest.approx(0.90)


def test_pump_characteristic_never_rises_with_flow():
    device = CentrifugalPump()
    device.set_speed_target(1.0)
    device.start()
    device.integrate(60.0)

    curve = [
        device.characteristic(flow)
        for flow in (-400.0, -100.0, -1.0, 0.0, 1.0, 100.0, 400.0)
    ]

    for lower, higher in zip(curve, curve[1:]):
        assert higher < lower


def test_pump_holds_shutoff_rise_at_zero_flow():
    device = CentrifugalPump()
    device.set_speed_target(1.0)
    device.start()
    device.integrate(60.0)

    assert device.speed == pytest.approx(1.0)
    assert device.characteristic(0.0) == pytest.approx(75.0)


def test_pump_rises_above_shutoff_when_flow_reverses():
    device = CentrifugalPump()
    device.set_speed_target(1.0)
    device.start()
    device.integrate(60.0)

    forward = device.characteristic(100.0)
    reverse = device.characteristic(-100.0)

    assert forward < 75.0 < reverse
    assert reverse - 75.0 == pytest.approx(75.0 - forward)


def test_pump_curve_is_not_clamped_past_runout():
    device = CentrifugalPump()
    device.set_speed_target(0.1)
    device.start()
    device.integrate(60.0)

    overrun = device.max_flow

    assert device.characteristic(overrun) < 0.0
    assert device.characteristic(overrun) == pytest.approx(
        75.0 * device.speed ** 2 - 1.5e-05 * overrun ** 2,
    )
