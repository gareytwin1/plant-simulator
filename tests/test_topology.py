import json

import pytest

from app.equipment.base import INLET, OUTLET, Equipment
from app.equipment.compressor import GasCompressor
from app.plant.topology import (
    ATMOSPHERIC_PRESSURE,
    STANDARD_TEMPERATURE,
    Branch,
    Node,
    Stream,
    Topology,
)


# Shaped like the C3 config's nodes/equipment keys, so the loader (T3-3) has
# the same graph to build from a real file that these tests build by hand.
FIXTURE = {
    "nodes": [
        {"id": "N-01", "boundary": True, "pressure": 750.0},
        {"id": "N-02", "boundary": False, "pressure": 700.0},
        {"id": "N-03", "boundary": False, "pressure": 800.0},
        {"id": "N-06", "boundary": True, "pressure": 875.0},
    ],
    "equipment": [
        {"tag": "P-101", "node_in": "N-01", "node_out": "N-02"},
        {"tag": "K-101", "node_in": "N-02", "node_out": "N-03"},
        {"tag": "FV-101", "node_in": "N-03", "node_out": "N-06"},
    ],
}


class Device(Equipment):
    def __init__(self, tag="X-901", ports=None):
        super().__init__(
            tag,
            ports=(
                {
                    "inlet": INLET,
                    "outlet": OUTLET,
                }
                if ports is None
                else ports
            ),
        )

        self.position = 1.0

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return 0.0

    def get_state(self):
        return {
            "position": self.position,
        }


def build_topology(fixture=FIXTURE):
    topology = Topology()

    for row in fixture["nodes"]:
        topology.add_node(
            Node(
                row["id"],
                pressure=row["pressure"],
                is_boundary=row["boundary"],
            ),
        )

    for row in fixture["equipment"]:
        topology.add_branch(
            Branch(
                f"B-{row['tag']}",
                topology.node(row["node_in"]),
                topology.node(row["node_out"]),
                Device(row["tag"]),
            ),
        )

    return topology


def test_graph_construction_from_a_fixture():
    topology = build_topology()

    assert sorted(topology.nodes) == ["N-01", "N-02", "N-03", "N-06"]
    assert sorted(topology.branches) == ["B-FV-101", "B-K-101", "B-P-101"]
    assert sorted(topology.devices) == ["FV-101", "K-101", "P-101"]


def test_nodes_keep_their_fixture_pressures():
    topology = build_topology()

    assert topology.node("N-01").pressure == pytest.approx(750.0)
    assert topology.node("N-02").pressure == pytest.approx(700.0)
    assert topology.node("N-06").pressure == pytest.approx(875.0)


def test_boundary_and_internal_nodes_split_on_the_fixture_flag():
    topology = build_topology()

    assert sorted(topology.boundary_nodes) == ["N-01", "N-06"]
    assert sorted(topology.internal_nodes) == ["N-02", "N-03"]


def test_lookups_return_the_graph_objects_not_copies():
    topology = build_topology()
    branch = topology.branch("B-P-101")

    assert branch.from_node is topology.node("N-01")
    assert branch.to_node is topology.node("N-02")
    assert topology.device("P-101") is branch.device


def test_unknown_lookups_name_what_is_available():
    topology = build_topology()

    with pytest.raises(KeyError, match="N-99"):
        topology.node("N-99")

    with pytest.raises(KeyError, match="B-99"):
        topology.branch("B-99")

    with pytest.raises(KeyError, match="E-999"):
        topology.device("E-999")


def test_port_to_node_binding_follows_port_direction():
    topology = build_topology()
    branch = topology.branch("B-K-101")
    device = branch.device

    assert device.port("inlet").node is topology.node("N-02")
    assert device.port("outlet").node is topology.node("N-03")


def test_every_device_port_in_the_fixture_is_connected():
    topology = build_topology()

    assert topology.unconnected_ports() == ()

    for device in topology.devices.values():
        for port in device.ports.values():
            assert port.connected


def test_a_real_device_binds_by_direction_not_by_port_name():
    """GasCompressor's ports are suction/discharge, not inlet/outlet.

    The branch reads Port.direction, so a device is wired in without the
    topology knowing anything about its port vocabulary.
    """
    suction = Node("N-02", pressure=700.0)
    discharge = Node("N-03", pressure=800.0)
    compressor = GasCompressor()

    Branch("B-K-101", suction, discharge, compressor)

    assert compressor.port("suction").node is suction
    assert compressor.port("discharge").node is discharge


def test_port_binding_survives_a_device_reset():
    """Port wiring is not process state — the topology owns it.

    C1 preserves ports across reset() precisely so that resetting a plant to
    its initial condition does not unplug it from its own graph.
    """
    topology = build_topology()
    device = topology.device("K-101")

    device.position = 0.25
    device.reset()

    assert device.position == pytest.approx(1.0)
    assert device.port("inlet").node is topology.node("N-02")
    assert device.port("outlet").node is topology.node("N-03")


def test_a_multi_port_device_must_be_told_which_ports_a_branch_claims():
    vessel = Device(
        "V-101",
        ports={
            "inlet": INLET,
            "outlet": OUTLET,
            "vent": OUTLET,
        },
    )

    with pytest.raises(ValueError, match="cannot pick an outlet port on V-101"):
        Branch(
            "B-V-101",
            Node("N-02"),
            Node("N-03"),
            vessel,
        )


def test_a_multi_port_device_sits_in_one_branch_per_side():
    """An exchanger's utility side is a second branch on the same device.

    Wiring by direction is the convenience for two-port devices; naming the
    ports is what keeps an exchanger, a vessel or a letdown line
    representable on the same graph instead of forcing a device per branch.
    """
    process_in = Node("N-02", pressure=700.0)
    process_out = Node("N-03", pressure=695.0)
    cooling_in = Node("CW-01", pressure=60.0, is_boundary=True)
    cooling_out = Node("CW-02", pressure=45.0, is_boundary=True)

    exchanger = Device(
        "E-101",
        ports={
            "process_in": INLET,
            "process_out": OUTLET,
            "utility_in": INLET,
            "utility_out": OUTLET,
        },
    )

    topology = Topology(
        nodes=[process_in, process_out, cooling_in, cooling_out],
        branches=[
            Branch(
                "B-E-101",
                process_in,
                process_out,
                exchanger,
                from_port="process_in",
                to_port="process_out",
            ),
        ],
    )

    assert topology.unconnected_ports() == (
        ("E-101", exchanger.port("utility_in")),
        ("E-101", exchanger.port("utility_out")),
    )

    topology.add_branch(
        Branch(
            "B-E-101-CW",
            cooling_in,
            cooling_out,
            exchanger,
            from_port="utility_in",
            to_port="utility_out",
        ),
    )

    assert exchanger.port("process_in").node is process_in
    assert exchanger.port("utility_out").node is cooling_out
    assert topology.unconnected_ports() == ()
    assert sorted(topology.devices) == ["E-101"]


def test_two_branches_cannot_claim_the_same_port():
    vessel = Device(
        "V-101",
        ports={
            "inlet": INLET,
            "outlet": OUTLET,
            "vent": OUTLET,
        },
    )
    feed = Node("N-02", pressure=700.0)
    liquid = Node("N-03", pressure=690.0)
    flare = Node("N-09", pressure=ATMOSPHERIC_PRESSURE, is_boundary=True)

    Branch("B-V-101", feed, liquid, vessel, to_port="outlet")

    with pytest.raises(ValueError, match="already connected to node 'N-02'"):
        Branch("B-V-101-VENT", liquid, flare, vessel, to_port="vent")


def test_a_named_port_must_face_the_right_way():
    with pytest.raises(ValueError, match="but that port is an outlet"):
        Branch(
            "B-X",
            Node("N-01"),
            Node("N-02"),
            Device(),
            from_port="outlet",
        )


def test_a_named_port_must_exist_on_the_device():
    with pytest.raises(KeyError, match="no port 'nozzle'"):
        Branch(
            "B-X",
            Node("N-01"),
            Node("N-02"),
            Device(),
            to_port="nozzle",
        )


def test_branch_rejects_a_device_with_no_inlet():
    with pytest.raises(ValueError, match="cannot pick an inlet port"):
        Branch(
            "B-X",
            Node("N-01"),
            Node("N-02"),
            Device(ports={"outlet": OUTLET}),
        )


def test_branch_rejects_a_missing_device():
    with pytest.raises(ValueError, match="needs a device"):
        Branch("B-X", Node("N-01"), Node("N-02"), None)


def test_branch_rejects_the_same_node_at_both_ends():
    node = Node("N-02")

    with pytest.raises(ValueError, match="starts and ends at node"):
        Branch("B-X", node, node, Device())


def test_boundary_node_pressure_is_held_fixed():
    header = Node("N-01", pressure=750.0, is_boundary=True)

    with pytest.raises(ValueError, match="N-01.*boundary"):
        header.set_pressure(600.0)

    assert header.pressure == pytest.approx(750.0)


def test_internal_node_pressure_is_written_by_the_solver():
    node = Node("N-02", pressure=700.0)

    node.set_pressure(712.5)

    assert node.pressure == pytest.approx(712.5)


def test_node_pressure_cannot_be_assigned_directly():
    """set_pressure() is the only way in, on purpose.

    A plain attribute would let any caller — including a device that somehow
    reached a node — write a pressure without going past the boundary check.
    """
    node = Node("N-02", pressure=700.0)

    with pytest.raises(AttributeError):
        node.pressure = 800.0


def test_node_carries_no_attributes_beyond_the_contract():
    node = Node("N-02")

    with pytest.raises(AttributeError):
        node.flow = 100.0


def test_stream_defaults_are_sane_at_zero_flow():
    stream = Stream()

    assert stream.flow == pytest.approx(0.0)
    assert stream.pressure == pytest.approx(ATMOSPHERIC_PRESSURE)
    assert stream.temperature == pytest.approx(STANDARD_TEMPERATURE)
    assert stream.composition == {}


def test_a_new_branch_starts_at_rest_at_its_upstream_pressure():
    topology = build_topology()
    branch = topology.branch("B-P-101")

    assert branch.flow == pytest.approx(0.0)
    assert branch.stream.flow == pytest.approx(0.0)
    assert branch.stream.pressure == pytest.approx(750.0)
    assert branch.stream.temperature == pytest.approx(STANDARD_TEMPERATURE)


def test_branch_flow_and_stream_flow_are_one_value():
    topology = build_topology()
    branch = topology.branch("B-P-101")

    branch.set_flow(98.4)

    assert branch.flow == pytest.approx(98.4)
    assert branch.stream.flow == pytest.approx(98.4)


def test_replacing_the_stream_replaces_the_flow_with_it():
    topology = build_topology()
    branch = topology.branch("B-P-101")
    branch.set_flow(98.4)

    branch.set_stream(
        Stream(
            flow=42.0,
            pressure=760.0,
            temperature=112.0,
        ),
    )

    assert branch.flow == pytest.approx(42.0)
    assert branch.stream.temperature == pytest.approx(112.0)


def test_stream_composition_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        Stream(
            composition={
                "ethane": 0.6,
                "ethylene": 0.3,
            },
        )


def test_stream_composition_rejects_a_negative_fraction():
    with pytest.raises(ValueError, match="negative"):
        Stream(
            composition={
                "ethane": 1.2,
                "ethylene": -0.2,
            },
        )


def test_stream_composition_is_copied_from_its_caller():
    composition = {
        "ethane": 0.4,
        "ethylene": 0.6,
    }
    stream = Stream(composition=composition)

    composition["ethane"] = 0.9

    assert stream.composition["ethane"] == pytest.approx(0.4)


def test_duplicate_node_id_is_rejected():
    topology = build_topology()

    with pytest.raises(ValueError, match="duplicate node id 'N-02'"):
        topology.add_node(Node("N-02"))


def test_duplicate_branch_id_is_rejected():
    topology = build_topology()

    with pytest.raises(ValueError, match="duplicate branch id 'B-P-101'"):
        topology.add_branch(
            Branch(
                "B-P-101",
                topology.node("N-03"),
                topology.node("N-06"),
                Device("P-999"),
            ),
        )


def test_duplicate_device_tag_is_rejected():
    topology = build_topology()

    with pytest.raises(ValueError, match="duplicate device tag 'K-101'"):
        topology.add_branch(
            Branch(
                "B-K-101-B",
                topology.node("N-03"),
                topology.node("N-06"),
                Device("K-101"),
            ),
        )


def test_branch_onto_a_foreign_node_is_rejected():
    """A node with the right id but from another graph is still foreign.

    The check is identity, not id, because two graphs holding two Node
    objects called N-02 would each solve a pressure the other never sees.
    """
    topology = build_topology()

    with pytest.raises(ValueError, match="not a node of this topology"):
        topology.add_branch(
            Branch(
                "B-X",
                topology.node("N-03"),
                Node("N-06", pressure=875.0, is_boundary=True),
                Device("X-901"),
            ),
        )


def test_branches_at_a_node_split_by_direction():
    topology = build_topology()

    assert topology.branches_from("N-02") == (topology.branch("B-K-101"),)
    assert topology.branches_to("N-02") == (topology.branch("B-P-101"),)
    assert set(topology.branches_at("N-02")) == {
        topology.branch("B-P-101"),
        topology.branch("B-K-101"),
    }


def test_a_dead_end_node_has_no_branches():
    topology = build_topology()
    topology.add_node(Node("N-99"))

    assert topology.branches_at("N-99") == ()


def test_get_state_is_json_serialisable():
    topology = build_topology()
    topology.node("N-02").set_pressure(712.5)
    topology.branch("B-P-101").set_flow(98.4)

    state = json.loads(json.dumps(topology.get_state()))

    assert state["nodes"]["N-02"]["pressure"] == pytest.approx(712.5)
    assert state["nodes"]["N-01"]["is_boundary"] is True
    assert state["branches"]["B-P-101"]["device"] == "P-101"
    assert state["branches"]["B-P-101"]["to_node"] == "N-02"
    assert state["branches"]["B-P-101"]["from_port"] == "inlet"
    assert state["branches"]["B-P-101"]["to_port"] == "outlet"
    assert state["streams"]["B-P-101"]["flow"] == pytest.approx(98.4)


def test_describe_prints_every_node_and_branch():
    topology = build_topology()
    printed = topology.describe()

    for node_id in topology.nodes:
        assert node_id in printed

    for branch_id, branch in topology.branches.items():
        assert branch_id in printed
        assert branch.device.tag in printed

    assert "boundary" in printed
    assert str(topology) == printed


def test_describe_names_open_ports():
    topology = build_topology()
    topology.device("K-101").add_port("vent", OUTLET)

    assert "K-101.vent" in topology.describe()
    assert topology.unconnected_ports() == (
        ("K-101", topology.device("K-101").port("vent")),
    )


def test_repr_summarises_the_graph():
    topology = build_topology()

    assert repr(topology) == "<Topology 4 nodes, 3 branches>"
    assert repr(topology.node("N-01")) == "Node('N-01', 750.0 psia, boundary)"
    assert repr(topology.branch("B-P-101")) == (
        "Branch('B-P-101', 'N-01' -> 'N-02', P-101, flow=0.0)"
    )
