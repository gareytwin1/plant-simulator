import pytest

from app.equipment.pump import CentrifugalPump


def test_initial_pump_state():
    pump = CentrifugalPump()

    assert pump.running is False
    assert pump.speed == pytest.approx(0.0)
    assert pump.speed_target == pytest.approx(0.0)
    assert pump.flow == pytest.approx(0.0)
    assert pump.suction_pressure == pytest.approx(50.0)
    assert pump.discharge_pressure == pytest.approx(50.0)


def test_pump_reaches_full_speed_operating_point():
    pump = CentrifugalPump()

    pump.set_speed_target(1.0)
    pump.start()

    for _ in range(10):
        pump.step()

    assert pump.speed == pytest.approx(1.0)
    assert pump.flow == pytest.approx(1000.0)
    assert pump.suction_pressure == pytest.approx(40.0)
    assert pump.discharge_pressure == pytest.approx(100.0)
    assert pump.spread == pytest.approx(60.0)
    assert pump.pump_pressure_rise == pytest.approx(60.0)


def test_pump_half_speed_operating_point():
    pump = CentrifugalPump()

    pump.set_speed_target(0.50)
    pump.start()

    for _ in range(5):
        pump.step()

    assert pump.speed == pytest.approx(0.50)
    assert pump.flow == pytest.approx(500.0)
    assert pump.suction_pressure == pytest.approx(47.5)
    assert pump.discharge_pressure == pytest.approx(62.5)
    assert pump.spread == pytest.approx(15.0)


def test_pump_stop_reduces_speed():
    pump = CentrifugalPump()

    pump.set_speed_target(1.0)
    pump.start()

    for _ in range(10):
        pump.step()

    pump.stop()
    pump.step()

    assert pump.running is False
    assert pump.speed_target == pytest.approx(0.0)
    assert pump.speed == pytest.approx(0.90)
