import pytest

from app.equipment.base import LIQUID, MIXED, VAPOR
from app.plant.thermo import (
    COMPONENTS,
    DEFAULT_COMPONENT,
    REFERENCE_TEMPERATURE,
    StreamState,
    enthalpy_flow,
    heat_capacity,
    heat_capacity_rate,
    mix_streams,
)


def liquid(flow, temperature, composition=None):
    return StreamState(
        flow=flow,
        temperature=temperature,
        phase=LIQUID,
        composition=composition or {},
    )


def vapor(flow, temperature, composition=None):
    return StreamState(
        flow=flow,
        temperature=temperature,
        phase=VAPOR,
        composition=composition or {},
    )


def test_unspecified_fluid_takes_its_phase_default():
    assert heat_capacity({}, LIQUID) == pytest.approx(
        COMPONENTS[DEFAULT_COMPONENT[LIQUID]].liquid_heat_capacity,
    )
    assert heat_capacity({}, VAPOR) == pytest.approx(
        COMPONENTS[DEFAULT_COMPONENT[VAPOR]].vapor_heat_capacity,
    )


def test_mixture_capacity_is_mole_fraction_weighted():
    half = heat_capacity({"methane": 0.5, "ethane": 0.5}, VAPOR)

    pure = (
        COMPONENTS["methane"].vapor_heat_capacity
        + COMPONENTS["ethane"].vapor_heat_capacity
    )

    assert half == pytest.approx(pure / 2.0)


def test_capacity_refuses_a_phase_the_component_is_not_modelled_in():
    with pytest.raises(ValueError, match="no liquid heat capacity"):
        heat_capacity({"methane": 1.0}, LIQUID)


def test_capacity_refuses_an_unknown_component():
    with pytest.raises(ValueError, match="unknown component 'argon'"):
        heat_capacity({"argon": 1.0}, VAPOR)


def test_stream_refuses_a_phase_outside_the_frozen_vocabulary():
    with pytest.raises(ValueError, match="is not one of"):
        StreamState(flow=10.0, temperature=100.0, phase=MIXED)


def test_a_stream_at_the_datum_carries_no_enthalpy():
    assert enthalpy_flow(liquid(50.0, REFERENCE_TEMPERATURE)) == pytest.approx(0.0)


def test_enthalpy_flow_is_a_duty_in_btu_per_hour():
    stream = liquid(50.0, REFERENCE_TEMPERATURE + 10.0)

    # 50 gal/min * 60 min/hr * cp BTU/(gal·°F) * 10 °F
    expected = 50.0 * 60.0 * heat_capacity({}, LIQUID) * 10.0

    assert enthalpy_flow(stream) == pytest.approx(expected)
    assert heat_capacity_rate(stream) == pytest.approx(expected / 10.0)


def test_reversed_flow_carries_enthalpy_backwards():
    forward = liquid(50.0, 200.0)
    reverse = liquid(-50.0, 200.0)

    assert enthalpy_flow(reverse) == pytest.approx(-enthalpy_flow(forward))


def test_mixing_equal_flows_lands_halfway():
    mixed = mix_streams([liquid(50.0, 100.0), liquid(50.0, 200.0)])

    assert mixed.temperature == pytest.approx(150.0)
    assert mixed.flow == pytest.approx(100.0)
    assert mixed.phase == LIQUID


def test_mixing_weights_by_flow():
    mixed = mix_streams([liquid(75.0, 100.0), liquid(25.0, 200.0)])

    assert mixed.temperature == pytest.approx(125.0)


def test_the_hotter_stream_cannot_push_the_mixture_past_itself():
    mixed = mix_streams([vapor(10.0, 80.0), vapor(1.0, 400.0)])

    assert 80.0 < mixed.temperature < 400.0


def test_a_lone_stream_mixes_to_itself():
    alone = vapor(12.5, 173.0, {"ethylene": 1.0})
    mixed = mix_streams([alone])

    assert mixed.temperature == pytest.approx(alone.temperature)
    assert mixed.flow == pytest.approx(alone.flow)
    assert mixed.composition == pytest.approx(dict(alone.composition))


def test_energy_closes_across_a_mixing_node():
    arrivals = [
        vapor(35.0, 120.0, {"methane": 0.8, "ethane": 0.2}),
        vapor(12.5, 310.0, {"methane": 0.4, "ethylene": 0.6}),
        vapor(2.75, 55.0, {"ethane": 1.0}),
    ]

    mixed = mix_streams(arrivals)

    assert enthalpy_flow(mixed) == pytest.approx(
        sum(enthalpy_flow(stream) for stream in arrivals),
        rel=1e-12,
    )


def test_mixing_conserves_flow_and_composition():
    mixed = mix_streams(
        [
            vapor(30.0, 100.0, {"methane": 1.0}),
            vapor(10.0, 300.0, {"ethane": 1.0}),
        ],
    )

    assert mixed.flow == pytest.approx(40.0)
    assert mixed.composition["methane"] == pytest.approx(0.75)
    assert mixed.composition["ethane"] == pytest.approx(0.25)
    assert sum(mixed.composition.values()) == pytest.approx(1.0)


def test_mixing_is_associative_because_energy_closes_exactly():
    first = vapor(35.0, 120.0, {"methane": 0.8, "ethane": 0.2})
    second = vapor(12.5, 310.0, {"methane": 0.4, "ethylene": 0.6})
    third = vapor(2.75, 55.0, {"ethane": 1.0})

    at_once = mix_streams([first, second, third])
    in_pairs = mix_streams([mix_streams([first, second]), third])

    assert in_pairs.temperature == pytest.approx(at_once.temperature, rel=1e-12)
    assert in_pairs.flow == pytest.approx(at_once.flow)


def test_a_dead_node_averages_instead_of_dividing_by_zero():
    mixed = mix_streams([liquid(0.0, 100.0), liquid(0.0, 200.0)])

    assert mixed.temperature == pytest.approx(150.0)
    assert mixed.flow == pytest.approx(0.0)


def test_a_residual_flow_still_weights_normally():
    # A stopped machine settles just off zero rather than at it, and that
    # residual is a real flow, not the dead-node case.
    mixed = mix_streams([liquid(0.055, 100.0), liquid(0.055, 200.0)])

    assert mixed.temperature == pytest.approx(150.0)
    assert mixed.flow == pytest.approx(0.11)


def test_mixing_refuses_two_phases_at_one_node():
    with pytest.raises(ValueError, match="flash calculation"):
        mix_streams([liquid(50.0, 100.0), vapor(50.0, 100.0)])


def test_mixing_refuses_a_flow_that_is_leaving():
    with pytest.raises(ValueError, match="arriving flows"):
        mix_streams([liquid(50.0, 100.0), liquid(-50.0, 100.0)])


def test_mixing_refuses_a_named_composition_against_an_unspecified_fluid():
    with pytest.raises(ValueError, match="unspecified fluid"):
        mix_streams([vapor(50.0, 100.0, {"methane": 1.0}), vapor(50.0, 100.0)])


def test_mixing_refuses_an_empty_node():
    with pytest.raises(ValueError, match="at least one stream"):
        mix_streams([])
