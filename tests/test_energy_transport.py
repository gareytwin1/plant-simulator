"""
Energy propagation through the network - T6-5, Checkpoint C.

The engine carries temperature with the solved flow: upwind through every
branch, mixed at every internal node, supplied at every boundary. The three
build-plan tests are the first three below; the rest pin the rules
app/engine/transport.py states - reversal, loops, stagnation, a stopped
engine, and the refusals.
"""

import pytest

from app.engine.engine import Engine
from app.engine.transport import DomainTransport
from app.equipment.base import INLET, LIQUID, OUTLET, VAPOR, Equipment, signed_square
from app.plant.loader import load_plant_file
from app.plant.thermo import StreamState, enthalpy_flow, heat_capacity_rate
from app.plant.topology import STANDARD_TEMPERATURE, Branch, Node, Topology

from tests.test_engine_solver import PLANTS, commanded


class Line(Equipment):
    """A liquid resistance with declared ports, so its domain has a phase."""

    def __init__(self, tag="L-1", resistance=0.01, phase=LIQUID):
        super().__init__(tag)

        self.add_port("inlet", INLET, phase=phase, purpose="process")
        self.add_port("outlet", OUTLET, phase=phase, purpose="process")

        self.resistance = resistance
        self.rise = 0.0

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return self.rise - self.resistance * signed_square(flow)

    def get_state(self):
        return {"resistance": self.resistance}


class Cooler(Line):
    """Removes a fixed duty, in BTU/hr, from whatever flows through it."""

    def __init__(self, tag="E-1", resistance=0.01, duty=0.0):
        super().__init__(tag, resistance)

        self.duty = duty

    def leaving_temperature(self, arriving, inlet_pressure, outlet_pressure):
        rate = abs(heat_capacity_rate(arriving))

        if rate == 0.0:
            return arriving.temperature

        return arriving.temperature - self.duty / rate


class Heater(Line):
    """Adds a fixed temperature rise to whatever flows through it, either way."""

    def __init__(self, tag="H-1", resistance=0.01, temperature_rise=0.0):
        super().__init__(tag, resistance)

        self.temperature_rise = temperature_rise

    def leaving_temperature(self, arriving, inlet_pressure, outlet_pressure):
        return arriving.temperature + self.temperature_rise


def cooled_train():
    """A hot feed through a cooler, joined by a cold feed, then two lines to
    the product header:

        HOT (200 °F) - L-1 - N-1 - E-1 - N-2 - L-3 - N-3 - L-4 - PRODUCT
        COLD (60 °F) - L-2 -------------'
    """
    nodes = {
        node_id: Node(node_id, pressure, is_boundary=boundary)
        for node_id, pressure, boundary in (
            ("HOT", 150.0, True),
            ("COLD", 150.0, True),
            ("PRODUCT", 20.0, True),
            ("N-1", 120.0, False),
            ("N-2", 90.0, False),
            ("N-3", 50.0, False),
        )
    }
    cooler = Cooler(duty=2.0e6)
    branches = [
        Branch("B-1", nodes["HOT"], nodes["N-1"], Line("L-1")),
        Branch("B-2", nodes["N-1"], nodes["N-2"], cooler),
        Branch("B-3", nodes["COLD"], nodes["N-2"], Line("L-2")),
        Branch("B-4", nodes["N-2"], nodes["N-3"], Line("L-3")),
        Branch("B-5", nodes["N-3"], nodes["PRODUCT"], Line("L-4")),
    ]
    engine = Engine(
        [branch.device for branch in branches],
        topology=Topology(nodes.values(), branches),
        boundary_temperatures={"HOT": 200.0, "COLD": 60.0},
    )

    return engine, cooler


def temperatures(snapshot):
    return {
        **{
            node_id: row["temperature"]
            for node_id, row in snapshot.nodes.items()
        },
        **{
            branch_id: row["temperature"]
            for branch_id, row in snapshot.streams.items()
        },
    }


def test_cooling_loss_raises_temperature_at_every_downstream_point():
    engine, cooler = cooled_train()
    cooled = engine.step(1.0)

    assert all(row["flow"] > 0.0 for row in cooled.streams.values())

    cooler.duty = 0.0
    lost = engine.step(1.0)

    before, after = temperatures(cooled), temperatures(lost)

    for point in ("B-2", "N-2", "B-4", "N-3", "B-5"):
        assert after[point] > before[point] + 1.0, point

    for point in ("HOT", "COLD", "PRODUCT", "B-1", "N-1", "B-3"):
        assert after[point] == pytest.approx(before[point]), point

    assert after["N-1"] == pytest.approx(200.0)
    assert after["B-2"] == pytest.approx(200.0)


def test_plant_wide_energy_balance_closes():
    engine, cooler = cooled_train()
    snapshot = engine.step(1.0)
    topology = engine.topology

    entering = 0.0
    leaving = 0.0

    for branch_id, branch in topology.branches.items():
        stream = snapshot.streams[branch_id]

        if branch.from_node.is_boundary:
            supplied = snapshot.nodes[branch.from_node.id]["temperature"]
            entering += enthalpy_flow(
                StreamState(stream["flow"], supplied, LIQUID),
            )

        if branch.to_node.is_boundary:
            leaving += enthalpy_flow(
                StreamState(stream["flow"], stream["temperature"], LIQUID),
            )

    assert entering - leaving == pytest.approx(cooler.duty, rel=1e-9)


def test_energy_closes_across_every_mixing_node():
    engine, _ = cooled_train()
    snapshot = engine.step(1.0)
    topology = engine.topology

    for node_id in topology.internal_nodes:
        arriving = sum(
            enthalpy_flow(
                StreamState(
                    snapshot.streams[branch.id]["flow"],
                    snapshot.streams[branch.id]["temperature"],
                    LIQUID,
                ),
            )
            for branch in topology.branches_to(node_id)
        )
        departing = sum(
            enthalpy_flow(
                StreamState(
                    snapshot.streams[branch.id]["flow"],
                    snapshot.nodes[node_id]["temperature"],
                    LIQUID,
                ),
            )
            for branch in topology.branches_from(node_id)
        )

        assert arriving == pytest.approx(departing, rel=1e-12), node_id


def reversible_line():
    """WEST (200 °F) - L-1 - N-1 - H-1 (+30 °F) - EAST (60 °F)."""
    nodes = {
        "WEST": Node("WEST", 100.0, is_boundary=True),
        "EAST": Node("EAST", 50.0, is_boundary=True),
        "N-1": Node("N-1", 75.0),
    }
    branches = [
        Branch("B-1", nodes["WEST"], nodes["N-1"], Line("L-1")),
        Branch("B-2", nodes["N-1"], nodes["EAST"], Heater(temperature_rise=30.0)),
    ]
    engine = Engine(
        [branch.device for branch in branches],
        topology=Topology(nodes.values(), branches),
        boundary_temperatures={"WEST": 200.0, "EAST": 60.0},
    )

    return engine, nodes


def test_flow_reversal_does_not_invert_the_temperature_field():
    engine, nodes = reversible_line()
    forward = engine.step(1.0)

    assert forward.streams["B-1"]["flow"] > 0.0
    assert forward.nodes["N-1"]["temperature"] == pytest.approx(200.0)
    assert forward.streams["B-1"]["temperature"] == pytest.approx(200.0)
    assert forward.streams["B-2"]["temperature"] == pytest.approx(230.0)

    nodes["WEST"].set_boundary_pressure(10.0)
    reverse = engine.step(1.0)

    assert reverse.streams["B-1"]["flow"] < 0.0
    assert reverse.streams["B-2"]["flow"] < 0.0
    assert reverse.streams["B-2"]["temperature"] == pytest.approx(90.0)
    assert reverse.nodes["N-1"]["temperature"] == pytest.approx(90.0)
    assert reverse.streams["B-1"]["temperature"] == pytest.approx(90.0)


def test_a_reversed_compressor_throttles_rather_than_compressing():
    engine = Engine.from_plant(
        load_plant_file(PLANTS / "olefins_lite.yaml"),
        boundary_temperatures={"N-202": 140.0},
    )
    snapshot = engine.step(1.0)

    assert snapshot.streams["B-K-101"]["flow"] < 0.0
    assert snapshot.nodes["N-204"]["temperature"] == pytest.approx(140.0)
    assert snapshot.streams["B-K-101"]["temperature"] == pytest.approx(140.0)
    assert snapshot.streams["B-PV-101"]["temperature"] == pytest.approx(
        STANDARD_TEMPERATURE,
    )


def test_compression_heat_propagates_down_a_series_train():
    engine = Engine.from_plant(
        commanded("gas_compression"),
        boundary_temperatures={"N-201": 80.0},
    )
    snapshot = engine.step(1.0)
    first, second = (
        engine.equipment[tag]
        for tag in ("K-101", "K-102")
    )
    suction, middle, discharge = (
        snapshot.nodes[node_id]["pressure"]
        for node_id in ("N-201", "N-202", "N-203")
    )

    assert all(row["flow"] > 0.0 for row in snapshot.streams.values())

    rise = first._polytropic(80.0, middle / suction)

    assert snapshot.nodes["N-202"]["temperature"] == pytest.approx(rise)
    assert snapshot.streams["B-K-102"]["temperature"] == pytest.approx(
        second._polytropic(rise, discharge / middle),
    )
    assert 80.0 < rise < snapshot.streams["B-K-102"]["temperature"]


def test_stepping_a_stopped_engine_leaves_every_temperature_unchanged():
    engine, _ = cooled_train()
    engine.step(1.0)
    engine.stop()

    before = temperatures(engine.snapshot())
    after = temperatures(engine.step(1.0))

    assert after == before


def test_a_boundary_supplies_its_own_temperature_whatever_arrives():
    engine, _ = reversible_line()
    snapshot = engine.step(1.0)

    assert snapshot.nodes["EAST"]["temperature"] == pytest.approx(60.0)
    assert snapshot.streams["B-2"]["temperature"] == pytest.approx(230.0)


def test_unconfigured_boundaries_supply_at_standard_temperature():
    engine = Engine.from_plant(commanded("liquid_transfer"))
    snapshot = engine.step(1.0)

    for row in (*snapshot.nodes.values(), *snapshot.streams.values()):
        assert row["temperature"] == pytest.approx(STANDARD_TEMPERATURE)


def manual(branches, nodes, **kwargs):
    """A topology with flows set by hand, so transport can be exercised on
    flow patterns without designing pressures that produce them."""
    topology = Topology(nodes, [branch for branch, _ in branches])

    for branch, flow in branches:
        branch.set_flow(flow)

    return DomainTransport("liquid", topology, kwargs)


def test_a_recirculation_loop_with_fresh_feed_settles_on_the_steady_mix():
    feed = Node("FEED", is_boundary=True)
    out = Node("OUT", is_boundary=True)
    first, second = Node("N-1"), Node("N-2")
    transport = manual(
        [
            (Branch("B-1", feed, first, Line("L-1")), 10.0),
            (Branch("B-2", first, second, Line("L-2")), 40.0),
            (Branch("B-3", second, first, Heater(temperature_rise=6.0)), 30.0),
            (Branch("B-4", second, out, Line("L-3")), 10.0),
        ],
        [feed, out, first, second],
        FEED=100.0,
    )

    transport.propagate()

    # N-1 * 10 = 100 * 10 + 30 * 6, the recycle's added heat purged by feed.
    assert transport.temperatures["N-1"] == pytest.approx(118.0)
    assert transport.temperatures["N-2"] == pytest.approx(118.0)


class Compressing(Line):
    """Scales absolute temperature, as a polytropic rise does."""

    def __init__(self, tag="K-1", resistance=0.01, factor=1.0):
        super().__init__(tag, resistance)

        self.factor = factor

    def leaving_temperature(self, arriving, inlet_pressure, outlet_pressure):
        return (arriving.temperature + 459.67) * self.factor - 459.67


def recycle_loop(recycle_device, feed=1.0, recycle=100.0):
    """FEED -> N-1 -> N-2 -> OUT, with N-2 recycled to N-1 through a device."""
    source = Node("FEED", is_boundary=True)
    out = Node("OUT", is_boundary=True)
    first, second = Node("N-1"), Node("N-2")

    return manual(
        [
            (Branch("B-1", source, first, Line("L-1")), feed),
            (Branch("B-2", first, second, Line("L-2")), feed + recycle),
            (Branch("B-3", second, first, recycle_device), recycle),
            (Branch("B-4", second, out, Line("L-3")), feed),
        ],
        [source, out, first, second],
        FEED=100.0,
    )


def test_a_loop_recycling_a_hundred_times_its_feed_settles_exactly():
    transport = recycle_loop(Heater(temperature_rise=0.5))
    transport.propagate()

    # N-1 * 1 = 100 * 1 + 100 * 0.5
    assert transport.settled
    assert transport.temperatures["N-1"] == pytest.approx(150.0, rel=1e-12)


def test_a_loop_returning_more_heat_than_its_feed_removes_holds_and_reports():
    transport = recycle_loop(Compressing(factor=1.1))
    transport.propagate()

    assert not transport.settled
    assert "no steady temperature" in transport.failure
    assert transport.temperatures["N-1"] == STANDARD_TEMPERATURE


class Refusing(Heater):
    """A heater that refuses the solved state once told to."""

    refuse = False

    def leaving_temperature(self, arriving, inlet_pressure, outlet_pressure):
        if self.refuse:
            raise ValueError("inlet_pressure must be finite and positive")

        return super().leaving_temperature(
            arriving, inlet_pressure, outlet_pressure,
        )


def test_a_device_refusing_the_solved_state_holds_the_domain_not_the_step():
    nodes = {
        "WEST": Node("WEST", 100.0, is_boundary=True),
        "EAST": Node("EAST", 50.0, is_boundary=True),
        "N-1": Node("N-1", 75.0),
    }
    heater = Refusing(temperature_rise=30.0)
    branches = [
        Branch("B-1", nodes["WEST"], nodes["N-1"], Line("L-1")),
        Branch("B-2", nodes["N-1"], nodes["EAST"], heater),
    ]
    engine = Engine(
        [branch.device for branch in branches],
        topology=Topology(nodes.values(), branches),
        boundary_temperatures={"WEST": 200.0},
    )
    before = temperatures(engine.step(1.0))

    heater.refuse = True
    nodes["WEST"].set_boundary_pressure(10.0)
    held = engine.step(1.0)
    transport = engine.transports["default"]

    assert temperatures(held) == before
    assert not transport.settled
    assert "inlet_pressure" in transport.failure

    assert held.streams["B-1"]["flow"] < 0.0

    heater.refuse = False
    recovered = engine.step(1.0)

    assert transport.settled
    assert recovered.nodes["N-1"]["temperature"] == pytest.approx(90.0)


def test_an_unsettled_loop_keeps_its_stream_temperatures_too():
    transport = recycle_loop(Heater(temperature_rise=0.5))
    transport.propagate()
    streams = {
        branch_id: branch.stream.temperature
        for branch_id, branch in transport.topology.branches.items()
    }

    transport.thermal["B-3"] = Compressing(factor=1.1)
    transport.propagate()

    assert not transport.settled
    assert {
        branch_id: branch.stream.temperature
        for branch_id, branch in transport.topology.branches.items()
    } == streams


def test_a_heated_loop_no_feed_reaches_keeps_its_temperature():
    transport = recycle_loop(Heater(temperature_rise=5.0), feed=0.0, recycle=10.0)
    transport.propagate()
    transport.propagate()

    assert transport.settled
    assert transport.temperatures["N-1"] == STANDARD_TEMPERATURE
    assert transport.temperatures["N-2"] == STANDARD_TEMPERATURE


def test_a_line_that_stops_keeps_the_temperature_it_last_carried():
    west = Node("WEST", is_boundary=True)
    east = Node("EAST", is_boundary=True)
    line = Branch("B-1", west, east, Line("L-1"))
    transport = manual([(line, -5.0)], [west, east], WEST=200.0, EAST=90.0)
    transport.propagate()

    line.set_flow(0.0)
    transport.propagate()

    assert line.stream.temperature == pytest.approx(90.0)


def test_a_node_nothing_flows_into_keeps_its_temperature():
    feed = Node("FEED", is_boundary=True)
    out = Node("OUT", is_boundary=True)
    middle = Node("N-1")
    inlet = Branch("B-1", feed, middle, Line("L-1"))
    outlet = Branch("B-2", middle, out, Line("L-2"))
    transport = manual(
        [(inlet, 5.0), (outlet, 5.0)],
        [feed, out, middle],
        FEED=150.0,
    )
    transport.propagate()

    inlet.set_flow(0.0)
    outlet.set_flow(0.0)
    transport.propagate()

    assert transport.temperatures["N-1"] == pytest.approx(150.0)


def test_a_boundary_temperature_on_an_internal_node_is_refused():
    with pytest.raises(ValueError, match="not a boundary node"):
        Engine.from_plant(
            commanded("liquid_transfer"),
            boundary_temperatures={"N-102": 90.0},
        )


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), -500.0])
def test_a_non_physical_boundary_temperature_is_refused(temperature):
    with pytest.raises(ValueError, match="above absolute zero"):
        Engine.from_plant(
            commanded("liquid_transfer"),
            boundary_temperatures={"N-101": temperature},
        )


def test_a_thermal_device_in_a_domain_with_no_phase_is_refused():
    heater = Heater()

    for port in heater.ports.values():
        port.declare()

    feed = Node("FEED", is_boundary=True)
    out = Node("OUT", is_boundary=True)

    with pytest.raises(ValueError, match="declare phase"):
        Engine(
            [heater],
            topology=Topology([feed, out], [Branch("B-1", feed, out, heater)]),
        )


def test_a_domain_declaring_two_phases_is_refused():
    feed = Node("FEED", is_boundary=True)
    middle = Node("N-1")
    out = Node("OUT", is_boundary=True)
    branches = [
        Branch("B-1", feed, middle, Line("L-1", phase=LIQUID)),
        Branch("B-2", middle, out, Line("L-2", phase=VAPOR)),
    ]

    with pytest.raises(ValueError, match="more than one phase"):
        Engine(
            [branch.device for branch in branches],
            topology=Topology([feed, middle, out], branches),
        )
