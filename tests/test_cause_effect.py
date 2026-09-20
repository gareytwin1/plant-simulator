"""
Cause and effect — the executable statement of what the plant must do (T4-5).

The project's goal is believable cause and effect, and this file is where that
claim is pinned down: change one thing an operator can change, and the right
things move in the right direction. Every other test here checks a part
(a device curve, a solver, a snapshot); this one checks that turning a knob
propagates through all of them to a number a trainee would see.

Everything is asserted through public surfaces only — plants come from
`load_plant()`, they run through `Engine.from_plant()`, and results are read
from the snapshot's `nodes` and `streams`, never off a device. That was done so
T5-2's multi-domain Engine and T7-1's valve would not need this file edited.
T5-2 has since landed — one solver per domain, an aggregated C4 solver row,
inventory coupled through boundary conditions — and none of it did: every plant
here is single-domain and holds no coupling device, so it wires one solver as
before and every number below is unchanged. Multi-domain cause and effect is
covered by T5-2's own `tests/test_inventory_coupling.py`, deliberately not
duplicated here.

Directions and closed forms, not recorded numbers. Where a closed form exists
it is written out from the device's own constants, so a test fails if the
algebra stops describing the model rather than if a printed value drifts.

Three things this file is deliberately careful about:

**Forward flow is asserted in every compared state.** There is no check valve
(ADR 0001 section 2.9), so a machine that cannot meet the boundary difference
runs backwards rather than sitting at zero. On the reference gas plant
(60 -> 480 psia), raising K-101's load 0.8 -> 0.9 -> 1.0 gives -121.7, -73.8,
+70.7 SCFM: flow rises at every step while the machine backflows through two
of the three. A bare "flow rises" assertion passes that. The boundaries here
are sized so the machines clear them.

**Only settled states are compared.** Load ramps at 0.05/s and speed at 0.10/s,
so a mid-ramp machine can still be in that reverse-flow regime; `settled()`
runs the plant out and checks that slow state has stopped moving.

**No cold-start operating point is asserted**, and nothing here depends on the
network's only resistance being the machine's own. Both are T7-1's, and this
file should survive it unchanged.
"""

import math

import pytest

from app.engine.engine import Engine
from app.engine.network import DEFAULT_PRESSURE_TOLERANCE
from app.equipment.pump import CentrifugalPump
from app.plant.loader import load_plant

LIQUID = "liquid"
GAS = "gas"

STEP = 1.0
SETTLE_SECONDS = 120.0

# Boosters into a shared transfer pump. Sized so every flow stays under the
# 1200 GPM pump rating: 816.50 GPM with one booster, 1032.80 with two.
SUPPLY = 50.0
HEADER = 180.0

# Two compressors in series. The combined shutoff rise 220*(l1^2 + l2^2) has to
# clear the boundary difference or the train backflows, which is why this is
# 240 psi rather than the reference plant's 420: it leaves forward flow from
# about 0.75 load on both machines, so a load can be lowered and still compared.
SUCTION = 60.0
DISCHARGE = 300.0

# One machine between equal battery limits, as the live pages are wired. The
# branch equation loses its boundary term entirely, which is what isolates the
# affinity law — and what makes an idle machine sit at zero.
BALANCED_LIQUID = 50.0
BALANCED_GAS = 750.0


def node(node_id, pressure, boundary=True, domain=LIQUID):
    return {
        "id": node_id,
        "boundary": boundary,
        "pressure": pressure,
        "domain": domain,
    }


def pump(tag, node_in, node_out, speed=1.0):
    return {
        "tag": tag,
        "type": "pump",
        "node_in": node_in,
        "node_out": node_out,
        "design": {"speed": speed},
    }


def compressor(tag, node_in, node_out, load=1.0):
    return {
        "tag": tag,
        "type": "compressor",
        "node_in": node_in,
        "node_out": node_out,
        "design": {"load": load},
    }


def running(nodes, equipment):
    """A loaded plant with every machine started and commanded to the design
    point it was loaded at.

    The loader writes `design` straight onto the device, but leaves it stopped
    with a zero target, so integrating a freshly loaded plant ramps it back
    down. Commanding it to where it already is makes the design point the
    settled point.
    """
    plant = load_plant({"nodes": nodes, "equipment": equipment})

    for device in plant.devices.values():
        device.start()

        if isinstance(device, CentrifugalPump):
            device.set_speed_target(device.speed)
        else:
            device.set_load_target(device.load)

    return plant


def settled(engine, seconds=SETTLE_SECONDS):
    """Run the plant out and return the snapshot, once nothing is moving.

    The check that slow state stopped is the point: every comparison in this
    file is between two settled plants, and a ramp still in progress is the
    one state where the directions asserted here need not hold.
    """
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


def device(plant, tag):
    return plant.devices[tag]


def idle_bound(resistance):
    """How far off zero a stopped machine is allowed to settle.

    Convergence is measured in psia, and at shutoff the branch curve is flat —
    the root is a double root — so the tolerance's slack maps to
    sqrt(tolerance / resistance) of flow. Which value inside that bound a run
    lands on is path-dependent, so the bound is the invariant and the value is
    not, which is why nothing here asserts a residual-flow number.

    T5-2 settled what becomes of that residual downstream, and the answer is
    that nothing special does: a solved flow is written onto a coupling device
    and integrated normally, with no clamp and no deadband, so a stopped
    machine's residual does reach vessel level. That is the decided behaviour
    rather than an oversight; the residual itself stays recorded as technical
    debt. See "A stopped machine keeps a small residual flow" in
    docs/PROJECT_STATE.md.
    """
    return math.sqrt(DEFAULT_PRESSURE_TOLERANCE / resistance)


# --------------------------------------------------------------------------
# Parallel boosters
# --------------------------------------------------------------------------


def booster_plant(boosters, supply=SUPPLY, header=HEADER):
    """`boosters` identical pumps from the supply into a shared internal node,
    and one transfer pump from there into the header.
    """
    return running(
        nodes=[
            node("N-S", supply),
            node("N-M", supply, boundary=False),
            node("N-D", header),
        ],
        equipment=[
            pump(f"P-10{i + 1}", "N-S", "N-M")
            for i in range(boosters)
        ] + [
            pump("P-201", "N-M", "N-D"),
        ],
    )


def booster_total(boosters, shutoff, resistance, supply=SUPPLY, header=HEADER):
    """Closed form for what the transfer pump carries.

    The boosters are identical and see the same two nodes, so they split the
    flow evenly. With n boosters each carrying q the transfer pump carries nq,
    and following the pressure from supply to header:

        header - supply = 2*shutoff - R*q^2 - R*(nq)^2

    so q^2 = (2*shutoff - delta) / (R * (1 + n^2)), and the total is n*q. The
    n^2 is why the second booster does not double anything: it raises the flow
    the shared transfer pump must pass, and that pump's own resistance is what
    the pair is pushing against.
    """
    per_booster = math.sqrt(
        (2.0 * shutoff - (header - supply))
        / (resistance * (1.0 + boosters ** 2)),
    )

    return boosters * per_booster


def test_a_second_booster_raises_flow_by_less_than_twice():
    one = settled(Engine.from_plant(booster_plant(1)))
    two = settled(Engine.from_plant(booster_plant(2)))

    assert flow(one, "P-201") > 0.0
    assert flow(two, "P-201") > 0.0

    assert flow(two, "P-201") > flow(one, "P-201")
    assert flow(two, "P-201") < 2.0 * flow(one, "P-201")


@pytest.mark.parametrize("boosters", [1, 2, 3])
def test_parallel_boosters_land_on_the_closed_form(boosters):
    plant = booster_plant(boosters)
    transfer = device(plant, "P-201")

    snapshot = settled(Engine.from_plant(plant))

    assert flow(snapshot, "P-201") == pytest.approx(
        booster_total(
            boosters,
            transfer.shutoff_pressure_rise,
            transfer.pump_resistance,
        ),
    )


@pytest.mark.parametrize("header", [180.0, 150.0, 120.0])
def test_the_gain_from_a_second_booster_does_not_depend_on_the_header(header):
    """2*sqrt(2/5) falls out of the closed form with the boundary difference
    cancelling, so the gain is a property of the arrangement and not of how
    hard the pair is being pushed.

    The lower headers here drive flows past the 1200 GPM rating. That is
    deliberate and is not a design point: nothing in the model clamps flow
    (envelopes and alarms own that later), and the ratio is what is under test.
    """
    one = settled(Engine.from_plant(booster_plant(1, header=header)))
    two = settled(Engine.from_plant(booster_plant(2, header=header)))

    assert flow(one, "P-201") > 0.0
    assert flow(two, "P-201") > 0.0

    assert flow(two, "P-201") / flow(one, "P-201") == pytest.approx(
        2.0 * math.sqrt(2.0 / 5.0),
    )


def test_flow_arriving_at_an_internal_node_equals_flow_leaving_it():
    snapshot = settled(Engine.from_plant(booster_plant(2)))

    arriving = flow(snapshot, "P-101") + flow(snapshot, "P-102")

    assert arriving == pytest.approx(flow(snapshot, "P-201"))


def test_identical_boosters_share_the_flow_evenly():
    snapshot = settled(Engine.from_plant(booster_plant(2)))

    assert flow(snapshot, "P-101") == pytest.approx(flow(snapshot, "P-102"))


# --------------------------------------------------------------------------
# Machines in series
# --------------------------------------------------------------------------


def series_plant(upstream, downstream):
    return running(
        nodes=[
            node("N-201", SUCTION, domain=GAS),
            node("N-202", SUCTION, boundary=False, domain=GAS),
            node("N-203", DISCHARGE, domain=GAS),
        ],
        equipment=[
            compressor("K-101", "N-201", "N-202", load=upstream),
            compressor("K-102", "N-202", "N-203", load=downstream),
        ],
    )


def series_solution(upstream, downstream, shutoff, resistance):
    """Closed form for the series train: the flow, and the internal node.

    One flow passes both machines, so

        discharge - suction = S*(l1^2 + l2^2) - 2*R*Q^2

    and substituting R*Q^2 back into the first machine's rise gives

        P_mid = suction + S*(l1^2 - l2^2)/2 + (discharge - suction)/2

    which is the whole cause-and-effect story of this plant in one line: the
    internal node follows the *difference* of the two loads. Push harder at the
    front and the middle rises; push harder at the back and it falls. Flow
    depends on the sum, so it rises either way.
    """
    squared = (
        shutoff * (upstream ** 2 + downstream ** 2)
        - (DISCHARGE - SUCTION)
    ) / (2.0 * resistance)

    return (
        math.sqrt(squared),
        SUCTION + shutoff * upstream ** 2 - resistance * squared,
    )


def test_more_load_on_the_upstream_machine_raises_flow():
    low = settled(Engine.from_plant(series_plant(0.8, 1.0)))
    high = settled(Engine.from_plant(series_plant(1.0, 1.0)))

    # Both forward: "flow rose" would otherwise be satisfied by backflowing
    # less hard, which is what this plant does at lower loads.
    assert flow(low, "K-101") > 0.0
    assert flow(high, "K-101") > 0.0

    assert flow(high, "K-101") > flow(low, "K-101")


def test_more_load_on_the_upstream_machine_raises_the_internal_node():
    low = settled(Engine.from_plant(series_plant(0.8, 1.0)))
    high = settled(Engine.from_plant(series_plant(1.0, 1.0)))

    assert flow(low, "K-101") > 0.0
    assert flow(high, "K-101") > 0.0

    assert pressure(high, "N-202") > pressure(low, "N-202")


def test_more_load_on_the_downstream_machine_raises_flow():
    low = settled(Engine.from_plant(series_plant(1.0, 0.8)))
    high = settled(Engine.from_plant(series_plant(1.0, 1.0)))

    assert flow(low, "K-101") > 0.0
    assert flow(high, "K-101") > 0.0

    assert flow(high, "K-101") > flow(low, "K-101")


def test_more_load_on_the_downstream_machine_lowers_the_internal_node():
    """The half of the load response a single machine between two boundaries
    cannot show: the downstream machine pulls its suction down as it works
    harder, so the same action that raises flow lowers the interstage.
    """
    low = settled(Engine.from_plant(series_plant(1.0, 0.8)))
    high = settled(Engine.from_plant(series_plant(1.0, 1.0)))

    assert flow(low, "K-101") > 0.0
    assert flow(high, "K-101") > 0.0

    assert pressure(high, "N-202") < pressure(low, "N-202")


@pytest.mark.parametrize(
    "upstream, downstream",
    [(1.0, 1.0), (0.8, 1.0), (1.0, 0.8), (0.9, 0.9)],
)
def test_the_series_train_lands_on_the_closed_form(upstream, downstream):
    plant = series_plant(upstream, downstream)
    machine = device(plant, "K-101")

    snapshot = settled(Engine.from_plant(plant))

    expected_flow, expected_pressure = series_solution(
        upstream,
        downstream,
        machine.shutoff_pressure_rise,
        machine.compressor_resistance,
    )

    assert flow(snapshot, "K-101") == pytest.approx(expected_flow)
    assert pressure(snapshot, "N-202") == pytest.approx(expected_pressure)


def test_a_series_node_passes_on_everything_it_receives():
    snapshot = settled(Engine.from_plant(series_plant(1.0, 1.0)))

    assert flow(snapshot, "K-101") == pytest.approx(flow(snapshot, "K-102"))


# --------------------------------------------------------------------------
# One machine between two boundaries
# --------------------------------------------------------------------------


def pump_plant(speed, suction=BALANCED_LIQUID, discharge=BALANCED_LIQUID):
    return running(
        nodes=[
            node("N-101", suction),
            node("N-102", discharge),
        ],
        equipment=[pump("P-101", "N-101", "N-102", speed=speed)],
    )


def compressor_plant(load, suction=BALANCED_GAS, discharge=BALANCED_GAS):
    return running(
        nodes=[
            node("N-201", suction, domain=GAS),
            node("N-202", discharge, domain=GAS),
        ],
        equipment=[compressor("K-101", "N-201", "N-202", load=load)],
    )


@pytest.mark.parametrize("speed", [0.25, 0.5, 0.75])
def test_pump_flow_is_proportional_to_speed(speed):
    """Equal boundaries cancel the boundary term, leaving
    shutoff*v^2 = R*q^2, so flow is exactly linear in speed. That is the
    affinity law with no system curve in the way to bend it — which is also
    why the absolute numbers here are a runaway operating point rather than a
    design point, and why only the ratio is asserted.
    """
    full = settled(Engine.from_plant(pump_plant(1.0)))
    part = settled(Engine.from_plant(pump_plant(speed)))

    assert flow(part, "P-101") == pytest.approx(speed * flow(full, "P-101"))


def test_a_higher_header_lowers_flow():
    low = settled(Engine.from_plant(pump_plant(1.0, discharge=100.0)))
    high = settled(Engine.from_plant(pump_plant(1.0, discharge=120.0)))

    assert flow(high, "P-101") > 0.0
    assert flow(high, "P-101") < flow(low, "P-101")


def test_a_lower_supply_lowers_flow():
    full = settled(Engine.from_plant(pump_plant(1.0, discharge=100.0)))
    starved = settled(
        Engine.from_plant(pump_plant(1.0, suction=30.0, discharge=100.0)),
    )

    assert flow(starved, "P-101") > 0.0
    assert flow(starved, "P-101") < flow(full, "P-101")


def test_stopping_a_pump_lowers_its_flow():
    engine = Engine.from_plant(pump_plant(1.0))
    before = settled(engine)

    engine.equipment["P-101"].stop()
    after = settled(engine)

    assert flow(before, "P-101") > 0.0
    assert after.equipment["P-101"]["speed"] == pytest.approx(0.0)
    assert flow(after, "P-101") < flow(before, "P-101")
    assert flow(after, "P-101") == pytest.approx(
        0.0,
        abs=idle_bound(engine.equipment["P-101"].pump_resistance),
    )


def test_stopping_a_compressor_lowers_its_flow():
    engine = Engine.from_plant(compressor_plant(1.0))
    before = settled(engine)

    engine.equipment["K-101"].stop()
    after = settled(engine)

    assert flow(before, "K-101") > 0.0
    assert after.equipment["K-101"]["load"] == pytest.approx(0.0)
    assert flow(after, "K-101") < flow(before, "K-101")
    assert flow(after, "K-101") == pytest.approx(
        0.0,
        abs=idle_bound(engine.equipment["K-101"].compressor_resistance),
    )


# Stopping one of two boosters is deliberately not tested here. With no check
# valve the stopped machine is a low-resistance path back to the supply, so the
# plant reverses outright — P-201 carries -553 GPM — and a "stopping lowers
# flow" assertion would pass for that reason rather than the one in its name.
# It is also behaviour T7-1 changes. The criterion is covered above, on a
# machine that is alone on its branch.


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_two_identical_runs_are_bit_identical():
    """Cause and effect is worth nothing if the same cause gives a different
    effect twice. Same config, same sequence of step(dt) calls, same operator
    action at the same step: identical snapshots, field for field.
    """
    def run():
        engine = Engine.from_plant(series_plant(1.0, 1.0))
        states = []

        for step_number in range(20):
            if step_number == 10:
                engine.equipment["K-101"].set_load_target(0.8)

            states.append(engine.step(STEP).as_dict())

        return states

    assert run() == run()
