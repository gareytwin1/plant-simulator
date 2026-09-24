import pytest
from app.equipment.compressor import GasCompressor
from app.plant.loader import load_plant

def test_initial_state():
    simulator = GasCompressor()

    assert simulator.running is False
    assert simulator.load == 0.0
    assert simulator.load_target == 0.0


def test_the_compressor_owns_no_flow_or_pressure():
    """T4-4 moved the operating point to the solver. Flow and the two
    pressures live on the branch and its nodes, and a device that still
    carried them would be a second answer nothing reconciles.
    """
    simulator = GasCompressor()

    for attribute in (
        "flow",
        "suction_pressure",
        "discharge_pressure",
        "upstream_boundary_pressure",
        "downstream_boundary_pressure",
        "step",
    ):
        assert not hasattr(simulator, attribute)

def test_start():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()

    assert simulator.running is True
    assert simulator.load == 0.0
    assert simulator.load_target == pytest.approx(0.50)

def test_stop():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.integrate(1.0)
    simulator.stop()

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.05)
    assert simulator.load_target == 0.0

def test_integrate_while_stopped_moves_nothing():
    simulator = GasCompressor()
    simulator.integrate(1.0)

    assert simulator.running is False
    assert simulator.load == 0.0

def test_integrate_while_running_ramps_load():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()
    simulator.integrate(1.0)

    assert simulator.load == pytest.approx(0.05)

def test_multiple_integrations_accumulate():
    simulator = GasCompressor()
    simulator.set_load_target(0.50)
    simulator.start()

    simulator.integrate(1.0)
    simulator.integrate(1.0)

    assert simulator.load == pytest.approx(0.10)

def test_load_reaches_its_target_and_holds():
    simulator = GasCompressor()
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.integrate(1.0)

    assert simulator.load == pytest.approx(1.0)
    assert simulator.characteristic(0.0) == pytest.approx(220.0)


def test_temperature_at_reads_the_pressures_the_solver_found():
    """Temperature is a function of the actual solved pressures, not a
    device-owned target — so the device is asked for them rather than
    holding them.
    """
    simulator = GasCompressor()

    assert simulator.temperature_at(675.0, 675.0) == pytest.approx(75.0)
    assert simulator.temperature_at(675.0, 875.0) == pytest.approx(
        119.44399037483498,
    )


def test_temperature_at_equal_pressures_gives_zero_rise():
    """A ratio of 1.0 is the identity case of the polytropic relation —
    discharge temperature equals suction temperature, at any pressure
    level."""
    simulator = GasCompressor()

    assert simulator.temperature_at(675.0, 675.0) == pytest.approx(
        simulator.base_temperature,
    )
    assert simulator.temperature_at(300.0, 300.0) == pytest.approx(
        simulator.base_temperature,
    )


def test_temperature_rises_monotonically_with_ratio():
    simulator = GasCompressor()

    discharges = (675.0, 700.0, 775.0, 875.0, 1075.0, 1675.0)
    temperatures = [
        simulator.temperature_at(675.0, discharge)
        for discharge in discharges
    ]

    for lower, higher in zip(temperatures, temperatures[1:]):
        assert higher > lower


def test_temperature_responds_to_absolute_pressure_level_not_just_spread():
    """The same 25 psia spread at two different suction levels is two
    different pressure ratios, and only the ratio determines temperature
    rise — a table keyed on spread alone could not tell these apart."""
    simulator = GasCompressor()

    elevated_suction = simulator.temperature_at(725.0, 750.0)
    elevated_discharge = simulator.temperature_at(750.0, 775.0)

    assert elevated_suction != pytest.approx(elevated_discharge)


def test_temperature_is_not_clamped():
    """The piecewise table clamped to max_temperature; the polytropic
    relation does not — equipment does not clamp its own output."""
    simulator = GasCompressor()

    assert simulator.temperature_at(675.0, 6750.0) > simulator.max_temperature


def test_polytropic_efficiency_is_a_live_parameter_not_a_literal():
    simulator = GasCompressor()

    baseline = simulator.temperature_at(675.0, 875.0)
    simulator.polytropic_efficiency = 0.5
    lower_efficiency = simulator.temperature_at(675.0, 875.0)

    assert lower_efficiency > baseline

def test_shutdown_reduces_load():
    simulator = GasCompressor()
    simulator.set_load_target(1.0)
    simulator.start()

    for _ in range(20):
        simulator.integrate(1.0)

    simulator.stop()
    simulator.integrate(1.0)

    assert simulator.running is False
    assert simulator.load == pytest.approx(0.95)
    assert simulator.load_target == 0.0
    assert simulator.characteristic(0.0) < 220.0

def test_get_state():
    simulator = GasCompressor()
    state = simulator.get_state()

    assert state["running"] is False
    assert state["load"] == 0.0
    assert state["load_target"] == 0.0


def test_get_state_reports_no_flow_or_pressure():
    state = GasCompressor().get_state()

    for key in ("flow", "suction_pressure", "discharge_pressure", "spread"):
        assert key not in state

def test_set_load_target():
    simulator = GasCompressor()

    simulator.set_load_target(0.60)
    assert simulator.load_target == pytest.approx(0.60)

    simulator.set_load_target(1.50)
    assert simulator.load_target == 1.0

    simulator.set_load_target(-0.50)
    assert simulator.load_target == 0.0


def test_the_compressor_owns_no_valve_state():
    """T7-2 moved the discharge valve out of the machine and onto its own
    branch, where the solver can see it. A valve position left on the
    compressor would be a second, inert answer to a question the
    ControlValve on config/plants/olefins_lite.yaml now answers for real.
    """
    simulator = GasCompressor()

    for attribute in (
        "discharge_valve_position",
        "discharge_valve_target",
        "discharge_valve_rate",
        "valve_resistance",
        "set_discharge_valve_position",
    ):
        assert not hasattr(simulator, attribute)

    for key in ("discharge_valve_position", "discharge_valve_target"):
        assert key not in simulator.get_state()


def test_compressor_characteristic_never_rises_with_flow():
    device = GasCompressor()
    device.set_load_target(1.0)
    device.start()
    device.integrate(60.0)

    curve = [
        device.characteristic(flow)
        for flow in (-400.0, -100.0, -1.0, 0.0, 1.0, 100.0, 400.0)
    ]

    for lower, higher in zip(curve, curve[1:]):
        assert higher < lower


def test_compressor_holds_shutoff_rise_at_zero_flow():
    device = GasCompressor()
    device.set_load_target(1.0)
    device.start()
    device.integrate(60.0)

    assert device.load == pytest.approx(1.0)
    assert device.characteristic(0.0) == pytest.approx(220.0)


def test_compressor_rises_above_shutoff_when_flow_reverses():
    device = GasCompressor()
    device.set_load_target(1.0)
    device.start()
    device.integrate(60.0)

    forward = device.characteristic(100.0)
    reverse = device.characteristic(-100.0)

    assert forward < 220.0 < reverse
    assert reverse - 220.0 == pytest.approx(220.0 - forward)


def test_compressor_curve_is_not_clamped_past_runout():
    device = GasCompressor()
    device.set_load_target(0.1)
    device.start()
    device.integrate(60.0)

    overrun = device.max_flow

    assert device.characteristic(overrun) < 0.0
    assert device.characteristic(overrun) == pytest.approx(
        220.0 * device.load ** 2 - 0.002 * overrun ** 2,
    )


def test_shutoff_pressure_rise_must_be_non_negative():
    simulator = GasCompressor()

    with pytest.raises(ValueError, match="shutoff_pressure_rise"):
        simulator.shutoff_pressure_rise = -1.0

    simulator.shutoff_pressure_rise = 0.0


def test_compressor_resistance_must_be_positive():
    simulator = GasCompressor()

    for bad in (0.0, -0.002):
        with pytest.raises(ValueError, match="compressor_resistance"):
            simulator.compressor_resistance = bad


def test_base_temperature_must_exceed_absolute_zero():
    simulator = GasCompressor()

    for bad in (-459.67, -500.0, float("-inf")):
        with pytest.raises(ValueError, match="base_temperature"):
            simulator.base_temperature = bad


def test_isentropic_exponent_must_be_in_the_ideal_gas_range():
    simulator = GasCompressor()

    for bad in (1.0, 0.5, 5.0 / 3.0 + 0.01):
        with pytest.raises(ValueError, match="isentropic_exponent"):
            simulator.isentropic_exponent = bad

    simulator.isentropic_exponent = 5.0 / 3.0


def test_polytropic_efficiency_must_be_a_fraction():
    simulator = GasCompressor()

    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="polytropic_efficiency"):
            simulator.polytropic_efficiency = bad

    simulator.polytropic_efficiency = 1.0


def test_temperature_at_rejects_a_non_positive_suction_pressure():
    simulator = GasCompressor()

    with pytest.raises(ValueError, match="suction_pressure"):
        simulator.temperature_at(0.0, 100.0)

    with pytest.raises(ValueError, match="suction_pressure"):
        simulator.temperature_at(-1.0, 100.0)


def test_temperature_at_rejects_a_non_positive_discharge_pressure():
    simulator = GasCompressor()

    with pytest.raises(ValueError, match="discharge_pressure"):
        simulator.temperature_at(675.0, 0.0)

    with pytest.raises(ValueError, match="discharge_pressure"):
        simulator.temperature_at(675.0, -100.0)


def test_temperature_at_rejects_non_finite_pressures():
    simulator = GasCompressor()

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="suction_pressure"):
            simulator.temperature_at(bad, 875.0)

        with pytest.raises(ValueError, match="discharge_pressure"):
            simulator.temperature_at(675.0, bad)


def compressor_plant_config(design):
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 675.0},
            {"id": "N-02", "boundary": True, "pressure": 875.0},
        ],
        "equipment": [
            {
                "tag": "K-101",
                "type": "compressor",
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
        ("compressor_resistance", 0.0),
        ("base_temperature", -459.67),
        ("isentropic_exponent", 1.0),
        ("isentropic_exponent", 2.0),
        ("polytropic_efficiency", 0.0),
        ("polytropic_efficiency", 1.5),
    ],
)
def test_a_design_value_out_of_range_is_a_config_error_naming_the_path(
    key,
    bad_value,
):
    from app.plant.loader import PlantConfigError

    with pytest.raises(PlantConfigError) as raised:
        load_plant(compressor_plant_config({key: bad_value}))

    assert any(
        error.startswith(f"$.equipment[0].design.{key}:")
        for error in raised.value.errors
    )


def test_get_state_keys_are_unchanged():
    state = GasCompressor().get_state()

    assert set(state) == {
        "running",
        "load",
        "load_target",
        "suction_pressure_target",
        "discharge_pressure_target",
        "flow_target",
        "max_spread",
        "max_flow",
        "max_temperature",
    }
