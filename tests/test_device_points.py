"""Device points in the snapshot (T9-5).

A device that sits in exactly one branch carries that branch's flow
and the pressures at its two ends - `flow`, `inlet_pressure` and
`outlet_pressure` - on its snapshot equipment row. The engine composes them
from the solved branch and nodes; the device holds none of them (C1). What
these tests pin is that the published row agrees with the solved plant, that
the names follow the branch's direction rather than any port's name, and that
only a device with one branch of its own gets them.
"""

from pathlib import Path

import pytest

from app.engine.engine import Engine
from app.engine.instruments import Instrument
from app.equipment.base import INLET, OUTLET
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant, load_plant_file
from app.plant.topology import Branch, Node, Topology

PLANT_FILE = Path(__file__).resolve().parent.parent / "config" / "plants" / "olefins_lite.yaml"

POINTS = ("flow", "inlet_pressure", "outlet_pressure")

# Each two-port device of the reference plant, with its branch and its two nodes.
WIRING = {
    "P-101": ("B-P-101", "N-101", "N-102"),
    "LV-101": ("B-LV-101", "N-102", "N-103"),
    "K-101": ("B-K-101", "N-201", "N-204"),
    "FV-201": ("B-FV-201", "N-204", "N-202"),
    "PV-101": ("B-PV-101", "N-201", "N-203"),
}


def running_plant():
    engine = Engine.from_plant(load_plant_file(PLANT_FILE))
    engine.equipment["P-101"].start()
    engine.equipment["K-101"].start()
    engine.start()

    return engine


def published(sections, tag):
    branch, inlet, outlet = WIRING[tag]

    return {
        "flow": sections.streams[branch]["flow"],
        "inlet_pressure": sections.nodes[inlet]["pressure"],
        "outlet_pressure": sections.nodes[outlet]["pressure"],
    }


def test_every_single_branch_device_carries_its_branch_and_nodes_on_every_step():
    engine = running_plant()

    for _ in range(30):
        snapshot = engine.step(1.0)

        for sections in (snapshot, snapshot.truth):
            for tag in WIRING:
                row = sections.equipment[tag]
                assert {name: row[name] for name in POINTS} == published(sections, tag)


def test_the_points_follow_the_solved_plant_when_it_moves():
    engine = running_plant()

    for _ in range(60):
        before = engine.step(1.0).equipment["P-101"]["flow"]

    engine.equipment["P-101"].stop()

    for _ in range(30):
        snapshot = engine.step(1.0)

    assert snapshot.equipment["P-101"]["flow"] < 0.0 < before
    assert snapshot.equipment["P-101"]["flow"] == snapshot.streams["B-P-101"]["flow"]


def test_a_device_in_no_branch_gets_none_of_the_points():
    snapshot = Engine.from_plant(load_plant_file(PLANT_FILE)).snapshot()

    assert not set(POINTS) & set(snapshot.equipment["V-101"])


def test_an_engine_without_a_topology_publishes_only_device_state():
    vessel = Vessel()
    snapshot = Engine([vessel]).snapshot()

    assert snapshot.equipment["V-101"] == vessel.get_state()


def test_a_device_in_several_branches_gets_none_of_the_points():
    valve = ControlValve("XV-1")
    valve.add_port("second_in", INLET)
    valve.add_port("second_out", OUTLET)
    nodes = [Node(f"N-{n}", pressure=100.0 - 10.0 * n, is_boundary=True) for n in range(4)]
    topology = Topology(
        nodes,
        [
            Branch("B-1", nodes[0], nodes[1], valve, from_port="inlet", to_port="outlet"),
            Branch("B-2", nodes[2], nodes[3], valve, from_port="second_in", to_port="second_out"),
        ],
    )

    snapshot = Engine([valve], topology=topology).snapshot()

    assert not set(POINTS) & set(snapshot.equipment["XV-1"])


def test_the_names_follow_the_branch_direction_not_the_port_names():
    """A valve whose inlet port happens to be called `discharge` still
    publishes the branch's from_node as its inlet pressure."""
    valve = ControlValve("XV-1")
    valve.ports.clear()
    valve.add_port("discharge", INLET)
    valve.add_port("suction", OUTLET)
    upstream = Node("N-UP", pressure=120.0, is_boundary=True)
    downstream = Node("N-DOWN", pressure=80.0, is_boundary=True)
    topology = Topology([upstream, downstream], [Branch("B-1", upstream, downstream, valve)])

    row = Engine([valve], topology=topology).snapshot().equipment["XV-1"]

    assert row["inlet_pressure"] == 120.0
    assert row["outlet_pressure"] == 80.0
    assert row["flow"] > 0.0


def test_a_device_that_publishes_a_point_name_itself_keeps_its_own_value():
    config = {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 300.0},
            {"id": "N-02", "boundary": True, "pressure": 14.696},
        ],
        "equipment": [
            {
                "tag": "PSV-101",
                "type": "relief_valve",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {"set_pressure": 260.0, "blowdown": 10.0, "capacity": 50.0},
            },
        ],
    }
    plant = load_plant(config)
    relief = plant.devices["PSV-101"]
    relief.inlet_pressure = 123.0

    snapshot = Engine.from_plant(plant).snapshot()
    row = snapshot.equipment["PSV-101"]

    assert row["inlet_pressure"] == 123.0
    assert row["outlet_pressure"] == pytest.approx(14.696)
    assert row["flow"] == snapshot.streams["B-PSV-101"]["flow"]


def test_get_state_never_carries_a_solved_point():
    engine = running_plant()
    engine.step(1.0)

    for tag in WIRING:
        assert not set(POINTS) & set(engine.equipment[tag].get_state())


@pytest.mark.parametrize(
    ("biased", "unbiased"),
    [
        (("nodes", "N-204", "pressure"), ("equipment", "K-101", "outlet_pressure")),
        (("equipment", "K-101", "outlet_pressure"), ("nodes", "N-204", "pressure")),
    ],
)
def test_an_instrument_on_one_reading_of_a_pressure_leaves_the_other_alone(biased, unbiased):
    """The points are composed in the truth, before any instrument runs, so a
    transmitter on K-101's discharge is separate from one on node N-204 - as a
    trip's transmitter is separate from a control loop's."""
    section, row, field = biased
    engine = Engine.from_plant(
        load_plant_file(PLANT_FILE),
        instruments=[Instrument("PT-TEST", section, row, field, bias=25.0)],
    )
    snapshot = engine.snapshot()

    def read(view, point):
        section, row, field = point
        return view[section][row][field]

    indicated = snapshot.as_dict()
    truth = snapshot.truth.as_dict()

    assert read(indicated, biased) == pytest.approx(read(truth, biased) + 25.0)
    assert read(indicated, unbiased) == read(truth, unbiased)
    assert read(truth, biased) == read(truth, unbiased)
