"""
Control valve — T7-1.

Three kinds of claim live here, kept apart because they fail for different
reasons:

  * the device — its two characteristics, its stroke limit, its fail position
    and its travel floor, all against the closed form and with no plant;
  * the plant — a valve is the network's first pure resistance, so a plant
    that once ran with nothing between two fixed boundaries now has a system
    curve, and cold start, throttling and added restriction can be asserted;
  * the coupling — a valve carries no flow unit of its own, so a vessel beside
    one only resolves GPM or SCFM through the *domain* the valve sits in.

The reference plant is two pumps in series through a valve between equal
battery limits (config/plants/liquid_valve_train.yaml). Following the pressure
from supply to header, with R the pump resistance and Cv the valve's effective
capacity:

    0 = 2 * shutoff - 2 * R * q^2 - q^2 / Cv^2
    q^2 = 2 * shutoff / (2 * R + 1 / Cv^2)

Criteria 6 and 7 were moved here from T4-5, which could not express them
without a second resistance in the network. They are deliberately different
kinds of change: 6 strokes one valve on one plant, 7 compares two designs.

Two things worth knowing before editing:

**Forward flow is asserted in every compared state.** There is no check valve
(ADR 0001 section 2.9), so "flow fell" is satisfiable by a plant that reversed.
A resistance cannot prevent that against an adverse pressure gradient — see
`test_a_valve_cannot_stop_reverse_flow_against_an_adverse_boundary`.

**Only settled states are compared**, for the reason test_cause_effect.py gives.
"""

import math
from pathlib import Path

import pytest

from app.engine.coupling import (
    FLOW_UNITS,
    GPM,
    SCFM,
    UNIT_NEUTRAL,
    build_couplings,
)
from app.engine.engine import Engine
from app.engine.network import DEFAULT_PRESSURE_TOLERANCE
from app.equipment.base import signed_square
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import (
    EQUAL_PERCENTAGE,
    FAIL_CLOSED,
    FAIL_OPEN,
    LINEAR,
    ControlValve,
)
from app.plant.loader import PlantConfigError, load_plant, load_plant_file

PLANTS = Path(__file__).resolve().parent.parent / "config" / "plants"

STEP = 1.0
SETTLE_SECONDS = 240.0

SHUTOFF = 75.0
PUMP_RESISTANCE = 0.000015

SWEEP = (-400.0, -100.0, -1.0, 0.0, 1.0, 100.0, 400.0)


def valve_with(**design):
    valve = ControlValve()

    for key, value in design.items():
        setattr(valve, key, value)

    return valve


def stroke(valve, seconds, dt=STEP):
    for _ in range(int(round(seconds / dt))):
        valve.integrate(dt)


# --------------------------------------------------------------------------
# 1. Characteristics
# --------------------------------------------------------------------------


@pytest.mark.parametrize("position", [0.1, 0.25, 0.5, 0.75, 1.0])
def test_linear_capacity_is_proportional_to_travel(position):
    valve = valve_with(
        flow_characteristic=LINEAR,
        capacity=80.0,
        position=position,
    )

    assert valve.effective_capacity == pytest.approx(80.0 * position)


@pytest.mark.parametrize("position", [0.1, 0.25, 0.5, 0.75, 1.0])
def test_equal_percentage_capacity_follows_the_rangeability_power_law(position):
    valve = valve_with(
        flow_characteristic=EQUAL_PERCENTAGE,
        capacity=80.0,
        rangeability=50.0,
        position=position,
    )

    assert valve.effective_capacity == pytest.approx(
        80.0 * 50.0 ** (position - 1.0),
    )


def test_equal_percentage_gives_equal_capacity_ratios_for_equal_travel():
    """The property the name states: each equal step of travel changes
    capacity by the same percentage. A linear valve does not.
    """
    def capacity(characteristic, position):
        return valve_with(
            flow_characteristic=characteristic,
            position=position,
        ).effective_capacity

    equal = [capacity(EQUAL_PERCENTAGE, x) for x in (0.3, 0.5, 0.7, 0.9)]
    linear = [capacity(LINEAR, x) for x in (0.3, 0.5, 0.7, 0.9)]

    equal_ratios = [b / a for a, b in zip(equal, equal[1:])]
    linear_ratios = [b / a for a, b in zip(linear, linear[1:])]

    assert equal_ratios == pytest.approx([equal_ratios[0]] * 3)
    assert linear_ratios != pytest.approx([linear_ratios[0]] * 3)


@pytest.mark.parametrize("characteristic", [LINEAR, EQUAL_PERCENTAGE])
@pytest.mark.parametrize("position", [0.1, 0.5, 1.0])
@pytest.mark.parametrize("flow", SWEEP)
def test_the_drop_is_the_signed_quadratic_over_capacity_squared(
    characteristic,
    position,
    flow,
):
    valve = valve_with(flow_characteristic=characteristic, position=position)

    assert valve.characteristic(flow) == pytest.approx(
        -signed_square(flow) / valve.effective_capacity ** 2,
        abs=1e-12,
    )


def test_a_valve_at_full_travel_reports_the_textbook_drop():
    """K = 1 / Cv^2: 100 gpm through Cv = 50 is 4 psi."""
    valve = valve_with(capacity=50.0, position=1.0)

    assert valve.characteristic(100.0) == pytest.approx(-4.0)


@pytest.mark.parametrize("characteristic", [LINEAR, EQUAL_PERCENTAGE])
def test_the_drop_opposes_flow_in_both_directions_and_is_zero_at_rest(
    characteristic,
):
    valve = valve_with(flow_characteristic=characteristic, position=0.5)

    assert valve.characteristic(0.0) == pytest.approx(0.0)
    assert valve.characteristic(100.0) < 0.0
    assert valve.characteristic(-100.0) > 0.0
    assert valve.characteristic(-100.0) == pytest.approx(
        -valve.characteristic(100.0),
    )


@pytest.mark.parametrize("characteristic", [LINEAR, EQUAL_PERCENTAGE])
def test_closing_the_valve_never_reduces_the_drop(characteristic):
    valve = valve_with(flow_characteristic=characteristic)

    drops = []

    for position in (1.0, 0.8, 0.6, 0.4, 0.2, 0.1):
        valve.position = position
        drops.append(valve.characteristic(100.0))

    assert drops == sorted(drops, reverse=True)
    assert drops[-1] < drops[0]


def test_the_curve_is_non_increasing_across_zero_flow():
    valve = valve_with(position=0.3)

    flows = [f / 10.0 for f in range(-1000, 1001, 5)]
    values = [valve.characteristic(f) for f in flows]

    assert all(b <= a for a, b in zip(values, values[1:]))


def test_characteristic_is_a_pure_query():
    valve = valve_with(position=0.4, position_target=0.9)
    before = dict(valve.__dict__)

    for flow in SWEEP:
        valve.characteristic(flow)

    assert valve.__dict__ == before


# --------------------------------------------------------------------------
# 2. Stroke rate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dt", [0.01, 0.1, 1.0, 7.0, 100.0])
def test_a_step_never_moves_the_valve_faster_than_the_stroke_rate(dt):
    valve = valve_with(stroke_rate=0.05, position=1.0)
    valve.set_position_target(0.1)

    for _ in range(30):
        before = valve.position
        valve.integrate(dt)

        assert abs(valve.position - before) <= 0.05 * dt + 1e-12


def test_a_valve_strokes_at_exactly_the_stroke_rate_until_it_arrives():
    valve = valve_with(stroke_rate=0.05, position=1.0)
    valve.set_position_target(0.5)

    stroke(valve, 4.0)
    assert valve.position == pytest.approx(0.8)

    stroke(valve, 20.0)
    assert valve.position == pytest.approx(0.5)


def test_a_valve_strokes_open_at_the_same_limited_rate():
    valve = valve_with(stroke_rate=0.05, position=0.2, position_target=0.2)
    valve.set_position_target(1.0)

    stroke(valve, 10.0)

    assert valve.position == pytest.approx(0.7)


def test_stroke_rate_is_independent_of_how_time_is_sliced():
    coarse = valve_with(position=1.0)
    fine = valve_with(position=1.0)

    coarse.set_position_target(0.4)
    fine.set_position_target(0.4)

    stroke(coarse, 8.0, dt=4.0)
    stroke(fine, 8.0, dt=0.25)

    assert coarse.position == pytest.approx(fine.position)


def test_integrating_zero_time_moves_nothing():
    valve = valve_with(position=1.0)
    valve.set_position_target(0.2)

    valve.integrate(0.0)

    assert valve.position == 1.0


# --------------------------------------------------------------------------
# 3. Fail position
# --------------------------------------------------------------------------


def test_a_fail_closed_valve_goes_to_minimum_travel_on_signal_loss():
    valve = valve_with(fail_action=FAIL_CLOSED, min_position=0.1, position=1.0)

    valve.lose_signal()
    stroke(valve, 100.0)

    assert valve.position == pytest.approx(0.1)


def test_a_fail_open_valve_goes_to_full_travel_on_signal_loss():
    valve = valve_with(fail_action=FAIL_OPEN, position=0.3)
    valve.set_position_target(0.3)

    valve.lose_signal()
    stroke(valve, 100.0)

    assert valve.position == pytest.approx(1.0)


@pytest.mark.parametrize("action", [FAIL_CLOSED, FAIL_OPEN])
def test_the_fail_stroke_obeys_the_rate_limit_and_ignores_the_command(action):
    valve = valve_with(fail_action=action, position=0.5, stroke_rate=0.05)
    valve.set_position_target(0.5)

    valve.lose_signal()
    valve.set_position_target(0.9 if action == FAIL_CLOSED else 0.2)

    before = valve.position
    valve.integrate(2.0)

    assert abs(valve.position - before) == pytest.approx(0.1)
    assert (valve.position < before) == (action == FAIL_CLOSED)


def test_restoring_the_signal_returns_the_valve_to_its_command():
    valve = valve_with(fail_action=FAIL_CLOSED, position=0.6)
    valve.set_position_target(0.6)

    valve.lose_signal()
    stroke(valve, 100.0)
    assert valve.position == pytest.approx(0.1)

    valve.restore_signal()
    stroke(valve, 100.0)

    assert valve.position == pytest.approx(0.6)


def test_the_fail_position_is_reached_from_any_starting_point():
    for start in (0.1, 0.4, 1.0):
        valve = valve_with(fail_action=FAIL_OPEN, position=start)
        valve.set_position_target(start)

        valve.lose_signal()
        stroke(valve, 100.0)

        assert valve.position == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 4. Minimum position
# --------------------------------------------------------------------------


@pytest.mark.parametrize("characteristic", [LINEAR, EQUAL_PERCENTAGE])
def test_a_zero_command_is_held_at_minimum_travel(characteristic):
    valve = valve_with(flow_characteristic=characteristic, min_position=0.1)

    valve.set_position_target(0.0)
    stroke(valve, 100.0)

    assert valve.position == pytest.approx(0.1)
    assert math.isfinite(valve.characteristic(100.0))


@pytest.mark.parametrize("characteristic", [LINEAR, EQUAL_PERCENTAGE])
@pytest.mark.parametrize("position", [0.0, -0.5])
def test_characteristic_stays_finite_even_if_position_is_forced_below_the_floor(
    characteristic,
    position,
):
    valve = valve_with(flow_characteristic=characteristic, min_position=0.1)
    valve.position = position

    for flow in SWEEP:
        assert math.isfinite(valve.characteristic(flow))

    assert valve.effective_capacity > 0.0


def test_a_target_is_bounded_above_by_full_travel():
    valve = valve_with()

    valve.set_position_target(3.0)

    assert valve.position_target == 1.0


@pytest.mark.parametrize("floor", [0.0, -0.1, 1.5])
def test_a_minimum_position_that_would_divide_by_zero_is_refused(floor):
    with pytest.raises(ValueError, match="min_position"):
        valve_with(min_position=floor)


def test_the_loader_reports_a_bad_valve_design_with_its_config_path():
    config = valve_train_config()
    config["equipment"][2]["design"]["min_position"] = 0.0
    config["equipment"][2]["design"]["flow_characteristic"] = "quick_opening"

    with pytest.raises(PlantConfigError) as error:
        load_plant(config)

    text = str(error.value)

    assert "$.equipment[2].design.min_position" in text
    assert "$.equipment[2].design.flow_characteristic" in text


def test_reset_restores_construction_state_and_keeps_the_ports():
    valve = ControlValve()
    ports = valve.ports

    valve.capacity = 12.0
    valve.flow_characteristic = EQUAL_PERCENTAGE
    valve.set_position_target(0.2)
    valve.lose_signal()
    stroke(valve, 10.0)

    valve.reset()

    assert valve.capacity == 100.0
    assert valve.flow_characteristic == LINEAR
    assert valve.position == 1.0
    assert valve.position_target == 1.0
    assert valve.signal_ok is True
    assert valve.ports is ports


# --------------------------------------------------------------------------
# The reference plant
# --------------------------------------------------------------------------


def valve_train_config(
    capacity=71.61,
    speed=1.0,
    supply=50.0,
    header=50.0,
    series_valves=1,
    position=1.0,
):
    """The reference plant as a config, so a test can vary one thing.

    `series_valves` builds that many identical valves in a row between the
    second pump and the header.
    """
    nodes = [
        {"id": "N-101", "boundary": True, "pressure": supply, "domain": "liquid"},
        {"id": "N-102", "boundary": False, "pressure": supply, "domain": "liquid"},
        {"id": "N-103", "boundary": False, "pressure": supply, "domain": "liquid"},
    ]

    equipment = [
        {
            "tag": "P-101",
            "type": "pump",
            "node_in": "N-101",
            "node_out": "N-102",
            "design": {"speed": speed},
        },
        {
            "tag": "P-102",
            "type": "pump",
            "node_in": "N-102",
            "node_out": "N-103",
            "design": {"speed": speed},
        },
    ]

    previous = "N-103"

    for i in range(series_valves):
        last = i == series_valves - 1
        outlet = "N-104" if last else f"N-2{i:02d}"

        nodes.append(
            {
                "id": outlet,
                "boundary": last,
                "pressure": header if last else supply,
                "domain": "liquid",
            },
        )
        equipment.append(
            {
                "tag": f"FV-10{i + 1}",
                "type": "control_valve",
                "node_in": previous,
                "node_out": outlet,
                "design": {
                    "capacity": capacity,
                    "position": position,
                    "position_target": position,
                },
            },
        )

        previous = outlet

    return {"nodes": nodes, "equipment": equipment}


def start(plant, speed=1.0):
    for device in plant.devices.values():
        if isinstance(device, CentrifugalPump):
            device.start()
            device.set_speed_target(speed)


def settled(engine, seconds=SETTLE_SECONDS):
    snapshot = engine.snapshot()
    previous = snapshot

    for _ in range(int(seconds / STEP)):
        previous = snapshot
        snapshot = engine.step(STEP)

    assert snapshot.equipment == previous.equipment, "slow state is still moving"
    assert snapshot.solver["converged"] is True

    return snapshot


def flow(snapshot, tag):
    return snapshot.streams[f"B-{tag}"]["flow"]


def pressure(snapshot, node_id):
    return snapshot.nodes[node_id]["pressure"]


def running_flow(equivalent_capacity, header=50.0, supply=50.0):
    """q^2 = (2*shutoff - dP) / (2*R + 1/Cv^2) with Cv the series-equivalent."""
    return math.sqrt(
        (2.0 * SHUTOFF - (header - supply))
        / (2.0 * PUMP_RESISTANCE + 1.0 / equivalent_capacity ** 2),
    )


def idle_bound(resistance):
    return math.sqrt(DEFAULT_PRESSURE_TOLERANCE / resistance)


# --------------------------------------------------------------------------
# 5. Cold start
# --------------------------------------------------------------------------


def test_a_cold_plant_sits_at_zero_and_runs_to_the_valve_limited_flow():
    """Cold is a freshly constructed plant whose branch flows start at zero,
    between equal boundaries, so the exact cold solution is zero. Started, it
    converges to a bounded flow *because* the valve supplies the system curve
    — with nothing between the boundaries the same pumps run to 2236 GPM.
    """
    plant = load_plant(valve_train_config(speed=0.0))
    engine = Engine.from_plant(plant)

    cold = engine.step(STEP)

    for tag in ("P-101", "P-102", "FV-101"):
        assert flow(cold, tag) == pytest.approx(0.0, abs=idle_bound(PUMP_RESISTANCE))

    start(plant)
    hot = settled(engine)

    expected = running_flow(plant.devices["FV-101"].effective_capacity)

    assert flow(hot, "FV-101") == pytest.approx(expected)
    assert flow(hot, "P-101") == pytest.approx(expected)

    # Bounded by the pumps' own rating, which the valve-less plant exceeds.
    assert 0.0 < flow(hot, "FV-101") < plant.devices["P-101"].max_flow


def test_the_reference_plant_lands_on_the_liquid_transfer_headline_flow():
    plant = load_plant_file(PLANTS / "liquid_valve_train.yaml")
    start(plant)

    hot = settled(Engine.from_plant(plant))

    # liquid_transfer.yaml runs 816.4966 GPM; capacity 71.61 was sized to match.
    assert flow(hot, "FV-101") == pytest.approx(816.4966, rel=1e-4)


def test_the_valve_takes_most_of_the_drop_and_the_pressure_profile_adds_up():
    plant = load_plant(valve_train_config(speed=0.0))
    engine = Engine.from_plant(plant)
    start(plant)

    hot = settled(engine)
    q = flow(hot, "FV-101")

    lift = SHUTOFF - PUMP_RESISTANCE * q ** 2

    assert pressure(hot, "N-102") == pytest.approx(50.0 + lift)
    assert pressure(hot, "N-103") == pytest.approx(50.0 + 2.0 * lift)
    assert pressure(hot, "N-103") - pressure(hot, "N-104") == pytest.approx(
        q ** 2 / plant.devices["FV-101"].effective_capacity ** 2,
    )


def test_a_valve_cannot_stop_reverse_flow_against_an_adverse_boundary():
    """Recorded, not fixed. With stopped pumps and the header 130 psi above
    the supply, a valve is only a resistance: flow runs backwards through the
    plant at sqrt(130 / (2R + 1/Cv^2)) however wide open or throttled it is.
    Closing it trims the reverse flow by the square root of the extra
    resistance and never to zero — only a check valve stops it, and no such
    device exists yet.
    """
    capacity = 100.0
    plant = load_plant(
        valve_train_config(capacity=capacity, speed=0.0, header=180.0),
    )

    cold = Engine.from_plant(plant).step(STEP)

    expected = -math.sqrt(
        130.0 / (2.0 * PUMP_RESISTANCE + 1.0 / capacity ** 2),
    )

    assert flow(cold, "FV-101") == pytest.approx(expected)
    assert flow(cold, "FV-101") < 0.0


# --------------------------------------------------------------------------
# 6. Operating change: one valve stroked toward closed
# --------------------------------------------------------------------------


def test_stroking_the_valve_toward_closed_lowers_flow_and_raises_upstream_pressure():
    plant = load_plant(valve_train_config(speed=0.0))
    engine = Engine.from_plant(plant)
    valve = plant.devices["FV-101"]
    start(plant)

    open_state = settled(engine)

    valve.set_position_target(0.5)
    throttled = settled(engine)

    assert valve.position == pytest.approx(0.5)

    assert flow(open_state, "FV-101") > 0.0
    assert flow(throttled, "FV-101") > 0.0

    assert flow(throttled, "FV-101") < flow(open_state, "FV-101")
    assert pressure(throttled, "N-103") > pressure(open_state, "N-103")

    # The pumps sit lower on their curves at the lower flow, which is where
    # the extra upstream pressure comes from.
    assert flow(throttled, "FV-101") == pytest.approx(
        running_flow(valve.effective_capacity),
    )


def test_each_further_stroke_toward_closed_keeps_lowering_flow():
    plant = load_plant(valve_train_config(speed=0.0))
    engine = Engine.from_plant(plant)
    valve = plant.devices["FV-101"]
    start(plant)

    flows = []
    upstream = []

    for target in (1.0, 0.7, 0.4, 0.2):
        valve.set_position_target(target)
        snapshot = settled(engine)

        flows.append(flow(snapshot, "FV-101"))
        upstream.append(pressure(snapshot, "N-103"))

    assert min(flows) > 0.0
    assert flows == sorted(flows, reverse=True)
    assert upstream == sorted(upstream)


# --------------------------------------------------------------------------
# 7. Design change: added restriction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "restricted",
    [
        {"capacity": 50.0},
        {"series_valves": 2},
    ],
    ids=["smaller-capacity", "second-valve-in-series"],
)
def test_added_restriction_raises_upstream_pressure(restricted):
    """Two otherwise-identical plants. Upstream of the restriction is the
    pumps' discharge, N-103, and it reads higher in the more restricted one
    even though that plant moves less.
    """
    base = load_plant(valve_train_config(speed=0.0))
    more = load_plant(valve_train_config(speed=0.0, **restricted))

    start(base)
    start(more)

    base_state = settled(Engine.from_plant(base))
    more_state = settled(Engine.from_plant(more))

    valves = [d for d in more.devices.values() if isinstance(d, ControlValve)]
    series_capacity = 1.0 / math.sqrt(sum(1.0 / v.effective_capacity ** 2 for v in valves))

    assert flow(base_state, "FV-101") > 0.0
    assert flow(more_state, "FV-101") > 0.0

    assert flow(more_state, "FV-101") < flow(base_state, "FV-101")
    assert pressure(more_state, "N-103") > pressure(base_state, "N-103")

    assert flow(more_state, "FV-101") == pytest.approx(running_flow(series_capacity))


def test_a_series_valve_carries_the_same_flow_as_the_first():
    plant = load_plant(valve_train_config(speed=0.0, series_valves=2))
    start(plant)

    hot = settled(Engine.from_plant(plant))

    assert flow(hot, "FV-102") == pytest.approx(flow(hot, "FV-101"))
    assert flow(hot, "FV-101") == pytest.approx(flow(hot, "P-102"))


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_two_identical_runs_are_bit_identical():
    def run():
        plant = load_plant(valve_train_config(speed=0.0))
        engine = Engine.from_plant(plant)
        valve = plant.devices["FV-101"]
        start(plant)

        trace = []

        for i in range(120):
            if i == 40:
                valve.set_position_target(0.3)

            if i == 80:
                valve.lose_signal()

            snapshot = engine.step(STEP)
            trace.append((dict(snapshot.nodes), dict(snapshot.streams), dict(snapshot.equipment)))

        return trace

    assert run() == run()


# --------------------------------------------------------------------------
# Coupling: the flow unit comes from the domain
# Coupling: a valve confirms no unit, and phase is declared (T5-6)
#
# Before T5-6 the valve took its unit from the *name* of the domain it sat
# in, and `DOMAIN_UNITS` knew only 'liquid' and 'gas'. ADR 0002 Amendment 2
# retires that: the valve is unit-neutral, the vessel port declares its
# phase, and what the domain is called means nothing.
# --------------------------------------------------------------------------


def vessel_beside_valve(valve_domain=None, phase=None):
    """A valve between two boundaries with a vessel attached across it.

    `valve_domain=None` declares no domain at all, which is DEFAULT_DOMAIN.
    `phase=None` leaves both vessel ports untyped, which is the legacy form.
    """
    def node(node_id, pressure):
        item = {"id": node_id, "boundary": True, "pressure": pressure}

        if valve_domain is not None:
            item["domain"] = valve_domain

        return item

    def port(node_id):
        if phase is None:
            return node_id

        return {"node": node_id, "phase": phase, "purpose": "process"}

    return {
        "nodes": [node("N-101", 60.0), node("N-102", 50.0)],
        "equipment": [
            {
                "tag": "FV-101",
                "type": "control_valve",
                "node_in": "N-101",
                "node_out": "N-102",
                "design": {"capacity": 100.0},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "inlet": port("N-102"),
                    "outlet": port("N-101"),
                },
                "paths": [],
                "design": {},
            },
        ],
    }


def couplings_for(config):
    plant = load_plant(config)

    return plant, build_couplings(plant.devices.values(), plant.topologies)


def test_a_valve_is_unit_neutral_rather_than_unknown():
    """Known, and known to carry no unit of its own. The distinction is the
    whole of T5-6's valve rule: a neutral device abstains, an unrecognised
    one raises.
    """
    assert FLOW_UNITS[ControlValve] == UNIT_NEUTRAL
    assert FLOW_UNITS[CentrifugalPump] == GPM

    assert UNIT_NEUTRAL not in (GPM, SCFM)


def test_a_typed_liquid_vessel_beside_a_valve_couples_in_gpm():
    plant, couplings = couplings_for(vessel_beside_valve("liquid", phase="liquid"))

    assert len(couplings) == 1
    assert [(a.port.name, a.unit) for a in couplings[0].attachments] == [
        ("inlet", GPM),
        ("outlet", GPM),
    ]

    engine = Engine.from_plant(plant)
    snapshot = engine.step(STEP)
    vessel = plant.devices["V-101"]

    assert flow(snapshot, "FV-101") > 0.0
    assert vessel.inlet_flow == pytest.approx(flow(snapshot, "FV-101"))
    assert vessel.outlet_flow == pytest.approx(flow(snapshot, "FV-101"))


def test_a_typed_vapor_vessel_beside_a_valve_couples_in_scfm():
    plant, couplings = couplings_for(vessel_beside_valve("gas", phase="vapor"))

    assert [(a.port.name, a.unit) for a in couplings[0].attachments] == [
        ("inlet", SCFM),
        ("outlet", SCFM),
    ]

    engine = Engine.from_plant(plant)
    engine.step(STEP)
    vessel = plant.devices["V-101"]

    # SCFM lands in the gas attributes and never in the GPM ones.
    assert vessel.inlet_flow == pytest.approx(0.0)
    assert vessel.outlet_flow == pytest.approx(0.0)
    # Both valve nodes now carry the vessel's one pressure, so there is no
    # drop across the valve and nothing flows.
    assert plant.nodes["N-101"].pressure == vessel.pressure
    assert plant.nodes["N-102"].pressure == vessel.pressure
    assert vessel.gas_inlet_flow == pytest.approx(0.0)
    assert vessel.gas_outlet_flow == pytest.approx(0.0)


def test_the_domain_name_no_longer_selects_a_flow_unit():
    """`DOMAIN_UNITS` is retired. A declared phase couples the same plant
    whatever the domain is called — including the default domain, which the
    old rule refused outright.
    """
    runs = []

    for domain in (None, "liquid", "gas", "process_water", "flare_header"):
        plant, couplings = couplings_for(vessel_beside_valve(domain, phase="liquid"))

        engine = Engine.from_plant(plant)
        engine.step(STEP)
        vessel = plant.devices["V-101"]

        assert [a.unit for a in couplings[0].attachments] == [GPM, GPM]

        runs.append((vessel.inlet_flow, vessel.outlet_flow, vessel.level))

    assert len(set(runs)) == 1


def test_an_untyped_vessel_port_on_a_valve_only_node_is_refused():
    """The case the old domain-name rule silently answered. A valve confirms
    nothing, so an untyped port has no phase from anywhere.
    """
    plant = load_plant(vessel_beside_valve("liquid"))

    with pytest.raises(ValueError, match="declares no phase"):
        build_couplings(plant.devices.values(), plant.topologies)

    with pytest.raises(ValueError, match=r"V-101\.inlet"):
        Engine.from_plant(plant)


def test_the_refusal_of_an_untyped_valve_side_port_asks_for_a_phase():
    plant = load_plant(vessel_beside_valve("process_water"))

    with pytest.raises(ValueError, match="declare phase: 'liquid' or phase: 'vapor'"):
        Engine.from_plant(plant)


def test_a_valve_with_no_vessel_beside_it_never_needs_a_declared_unit():
    """Resolution only runs for a coupling device's attachment, so a plant of
    valves and machines is unaffected by what its domains are called.
    """
    config = valve_train_config()

    for node in config["nodes"]:
        node["domain"] = "process_water"

    engine = Engine.from_plant(load_plant(config))

    assert engine.step(STEP).solver["converged"] is True
