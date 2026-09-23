import pytest

from app.equipment.pump import CentrifugalPump
from app.plant.loader import load_plant


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


def test_shutoff_pressure_rise_must_be_non_negative():
    pump = CentrifugalPump()

    with pytest.raises(ValueError, match="shutoff_pressure_rise"):
        pump.shutoff_pressure_rise = -1.0

    pump.shutoff_pressure_rise = 0.0


def test_pump_resistance_must_be_positive():
    pump = CentrifugalPump()

    for bad in (0.0, -0.000015):
        with pytest.raises(ValueError, match="pump_resistance"):
            pump.pump_resistance = bad


def pump_plant_config(design):
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 50.0},
            {"id": "N-02", "boundary": True, "pressure": 110.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": design,
            },
        ],
    }


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("shutoff_pressure_rise", -1.0),
        ("pump_resistance", 0.0),
    ],
)
def test_a_design_value_out_of_range_is_a_config_error_naming_the_path(
    key,
    bad_value,
):
    from app.plant.loader import PlantConfigError

    with pytest.raises(PlantConfigError) as raised:
        load_plant(pump_plant_config({key: bad_value}))

    assert any(
        error.startswith(f"$.equipment[0].design.{key}:")
        for error in raised.value.errors
    )


def test_get_state_keys_are_unchanged():
    state = CentrifugalPump().get_state()

    assert set(state) == {
        "running",
        "speed",
        "speed_target",
        "max_flow",
    }
