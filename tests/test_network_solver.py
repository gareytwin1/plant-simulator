import pytest

from app.engine.network import (
    DEFAULT_FLOW_TOLERANCE,
    DEFAULT_PRESSURE_TOLERANCE,
    NetworkSolver,
    SolverError,
    solve_network,
)
from app.equipment.base import INLET, OUTLET, Equipment, signed_square
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.plant.loader import load_plant
from app.plant.topology import Branch, Node, Topology


class SolverCurve(Equipment):
    """One curve shape covering both things a branch can hold.

    `shutoff_rise` of zero is a line or a valve — a pure resistance, zero at
    zero flow, dropping whichever way the flow runs. Anything positive is a
    machine holding that head at shutoff. Written against `signed_square` so
    the curve is non-increasing across zero, which is what C1 requires and
    what the solver's Jacobian assumes.

    The name is deliberately not `Curve` or `Machine`: `Equipment._registry`
    is keyed by class name, and the other test modules already own those.
    """

    def __init__(self, tag="R-901", shutoff_rise=0.0, resistance=0.01):
        super().__init__(
            tag,
            ports={
                "inlet": INLET,
                "outlet": OUTLET,
            },
        )

        self.shutoff_rise = shutoff_rise
        self.resistance = resistance

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return (
            self.shutoff_rise
            - self.resistance * signed_square(flow)
        )

    def get_state(self):
        return {
            "shutoff_rise": self.shutoff_rise,
            "resistance": self.resistance,
        }


def single_branch(
    from_pressure=100.0,
    to_pressure=150.0,
    shutoff_rise=75.0,
    resistance=0.01,
):
    """N-A -> N-B across one device, both ends pinned.

    The only unknown is the flow, so the answer is analytic:
    from + rise - resistance * |q|q - to = 0.
    """
    from_node = Node("N-A", pressure=from_pressure, is_boundary=True)
    to_node = Node("N-B", pressure=to_pressure, is_boundary=True)

    return Topology(
        [from_node, to_node],
        [
            Branch(
                "B-01",
                from_node,
                to_node,
                SolverCurve(
                    shutoff_rise=shutoff_rise,
                    resistance=resistance,
                ),
            ),
        ],
    )


def series_plant(
    supply_pressure=50.0,
    delivery_pressure=50.0,
    initial_pressure=50.0,
    pump=None,
    line_resistance=0.00005,
):
    """Supply -> pump -> N-MID -> line -> delivery.

    One internal node, two branch equations, and a mass balance that has to
    make both branches carry the same flow. Liquid throughout, so every flow
    in it is GPM — docs/UNITS_CONVENTION.md forbids one network spanning two
    process domains.
    """
    supply = Node("N-SUPPLY", pressure=supply_pressure, is_boundary=True)
    middle = Node("N-MID", pressure=initial_pressure)
    delivery = Node("N-DELIVERY", pressure=delivery_pressure, is_boundary=True)

    return Topology(
        [supply, middle, delivery],
        [
            Branch(
                "B-PUMP",
                supply,
                middle,
                CentrifugalPump() if pump is None else pump,
            ),
            Branch(
                "B-LINE",
                middle,
                delivery,
                SolverCurve("FV-101", resistance=line_resistance),
            ),
        ],
    )


def parallel_plant(initial_pressure=100.0):
    """One feed splitting into two unequal legs and recombining.

    Four branch equations and two mass balances, and the split between the
    legs is not something any single branch equation determines — only the
    simultaneous solve does.
    """
    supply = Node("N-1", pressure=300.0, is_boundary=True)
    split = Node("N-2", pressure=initial_pressure)
    join = Node("N-3", pressure=initial_pressure)
    delivery = Node("N-4", pressure=100.0, is_boundary=True)

    return Topology(
        [supply, split, join, delivery],
        [
            Branch("B-1", supply, split, SolverCurve("R-1", resistance=0.001)),
            Branch("B-2", split, join, SolverCurve("R-2", resistance=0.003)),
            Branch("B-3", split, join, SolverCurve("R-3", resistance=0.005)),
            Branch("B-4", join, delivery, SolverCurve("R-4", resistance=0.002)),
        ],
    )


def ramped_pump(speed_target=1.0, seconds=100.0):
    pump = CentrifugalPump()
    pump.start()
    pump.set_speed_target(speed_target)
    pump.integrate(seconds)

    return pump


def test_single_branch_between_fixed_boundaries_matches_the_analytic_root():
    topology = single_branch()

    result = solve_network(topology)

    # 100 + 75 - 0.01 * |q|q - 150 = 0 -> q = sqrt(25 / 0.01) = 50
    assert result.converged
    assert topology.branch("B-01").flow == pytest.approx(50.0)


def test_single_branch_residual_is_driven_below_the_pressure_tolerance():
    topology = single_branch()

    result = solve_network(topology)

    assert topology.branch("B-01").residual() == pytest.approx(
        0.0,
        abs=DEFAULT_PRESSURE_TOLERANCE,
    )
    assert result.pressure_residual < DEFAULT_PRESSURE_TOLERANCE
    assert result.residual <= 1.0


def test_an_internal_node_balances_the_flow_through_it():
    topology = series_plant(pump=ramped_pump())

    result = solve_network(topology)

    assert result.converged
    assert topology.branch("B-PUMP").flow == pytest.approx(
        topology.branch("B-LINE").flow,
    )
    assert result.flow_residual < DEFAULT_FLOW_TOLERANCE


def test_two_branch_equations_are_satisfied_at_the_same_time():
    pump = ramped_pump()
    topology = series_plant(pump=pump)

    solve_network(topology)

    # 75 - 1.5e-5|q|q (pump at full speed) then -5e-5|q|q (the line), between
    # boundaries that sit at the same pressure: q = sqrt(75 / 6.5e-5).
    expected_flow = (75.0 / (pump.pump_resistance + 0.00005)) ** 0.5

    assert topology.branch("B-PUMP").flow == pytest.approx(expected_flow)
    assert topology.branch("B-PUMP").residual() == pytest.approx(
        0.0,
        abs=DEFAULT_PRESSURE_TOLERANCE,
    )
    assert topology.branch("B-LINE").residual() == pytest.approx(
        0.0,
        abs=DEFAULT_PRESSURE_TOLERANCE,
    )


def test_a_split_and_rejoin_divides_flow_by_the_legs_resistances():
    topology = parallel_plant()

    result = solve_network(topology)

    feed = topology.branch("B-1").flow
    leg_a = topology.branch("B-2").flow
    leg_b = topology.branch("B-3").flow

    assert result.converged
    assert leg_a + leg_b == pytest.approx(feed)
    assert topology.branch("B-4").flow == pytest.approx(feed)

    # Both legs span the same two nodes, so they drop the same pressure and
    # the looser leg carries sqrt(0.005 / 0.003) times the flow.
    assert 0.003 * signed_square(leg_a) == pytest.approx(
        0.005 * signed_square(leg_b),
    )


def test_every_internal_node_balances_across_a_split_and_rejoin():
    topology = parallel_plant()

    solve_network(topology)

    for node_id in topology.internal_nodes:
        balance = sum(
            branch.flow for branch in topology.branches_to(node_id)
        ) - sum(
            branch.flow for branch in topology.branches_from(node_id)
        )

        assert balance == pytest.approx(0.0, abs=DEFAULT_FLOW_TOLERANCE)


def test_it_converges_from_a_cold_plant_with_every_flow_at_zero():
    topology = parallel_plant(initial_pressure=14.696)

    for branch in topology.branches.values():
        assert branch.flow == 0.0

    result = solve_network(topology)

    assert result.converged
    assert result.iterations <= 20


def test_a_cold_start_and_a_warm_restart_land_on_the_same_answer():
    cold = parallel_plant(initial_pressure=14.696)
    warm = parallel_plant(initial_pressure=250.0)

    solve_network(cold)
    solve_network(warm)

    for branch_id in cold.branches:
        assert cold.branch(branch_id).flow == pytest.approx(
            warm.branch(branch_id).flow,
        )


def test_re_solving_a_settled_plant_takes_no_iterations_and_moves_nothing():
    topology = series_plant(pump=ramped_pump())
    solver = NetworkSolver(topology)

    solver.solve()
    settled_flow = topology.branch("B-PUMP").flow
    settled_pressure = topology.node("N-MID").pressure

    again = solver.solve()

    assert again.iterations == 0
    assert again.converged
    assert topology.branch("B-PUMP").flow == settled_flow
    assert topology.node("N-MID").pressure == settled_pressure


def test_ramping_the_pump_moves_the_plant_and_the_solver_follows():
    pump = ramped_pump(speed_target=0.5)
    topology = series_plant(pump=pump)

    solve_network(topology)
    half_speed_flow = topology.branch("B-PUMP").flow

    pump.set_speed_target(1.0)
    pump.integrate(100.0)

    result = solve_network(topology)
    full_speed_flow = topology.branch("B-PUMP").flow

    assert result.converged
    assert full_speed_flow > half_speed_flow
    assert half_speed_flow == pytest.approx(
        (75.0 * 0.5**2 / (pump.pump_resistance + 0.00005)) ** 0.5,
    )
    assert full_speed_flow == pytest.approx(
        (75.0 / (pump.pump_resistance + 0.00005)) ** 0.5,
    )


def test_stopping_the_pump_settles_the_plant_at_zero_flow():
    pump = ramped_pump()
    topology = series_plant(pump=pump)

    solve_network(topology)
    running_flow = topology.branch("B-PUMP").flow

    pump.stop()
    pump.integrate(100.0)

    result = solve_network(topology)

    assert result.converged
    assert pump.speed == 0.0

    # Not bit-exactly zero, and it should not be: convergence is measured in
    # psia, and a quadratic curve this flat turns the 1e-7 psia of slack the
    # tolerance allows into about 0.04 GPM. Zero to well inside what the
    # plant could tell apart is the honest statement of a dead line.
    assert topology.branch("B-PUMP").flow == pytest.approx(0.0, abs=0.05)
    assert abs(topology.branch("B-PUMP").flow) < 1e-4 * running_flow


def test_the_same_plant_solved_twice_gives_bit_identical_numbers():
    first = series_plant(pump=ramped_pump())
    second = series_plant(pump=ramped_pump())

    solve_network(first)
    solve_network(second)

    assert first.branch("B-PUMP").flow == second.branch("B-PUMP").flow
    assert first.node("N-MID").pressure == second.node("N-MID").pressure


def test_the_answer_does_not_depend_on_the_order_the_graph_was_built_in():
    def build(node_order, branch_order):
        nodes = {
            "N-1": Node("N-1", pressure=300.0, is_boundary=True),
            "N-2": Node("N-2", pressure=100.0),
            "N-3": Node("N-3", pressure=100.0),
            "N-4": Node("N-4", pressure=100.0, is_boundary=True),
        }

        devices = {
            "B-1": ("N-1", "N-2", SolverCurve("R-1", resistance=0.001)),
            "B-2": ("N-2", "N-3", SolverCurve("R-2", resistance=0.003)),
            "B-3": ("N-2", "N-3", SolverCurve("R-3", resistance=0.005)),
            "B-4": ("N-3", "N-4", SolverCurve("R-4", resistance=0.002)),
        }

        topology = Topology(nodes[node_id] for node_id in node_order)

        for branch_id in branch_order:
            from_id, to_id, device = devices[branch_id]

            topology.add_branch(
                Branch(branch_id, nodes[from_id], nodes[to_id], device),
            )

        return topology

    forward = build(
        ("N-1", "N-2", "N-3", "N-4"),
        ("B-1", "B-2", "B-3", "B-4"),
    )
    shuffled = build(
        ("N-4", "N-2", "N-1", "N-3"),
        ("B-3", "B-1", "B-4", "B-2"),
    )

    solve_network(forward)
    solve_network(shuffled)

    for branch_id in forward.branches:
        assert forward.branch(branch_id).flow == shuffled.branch(branch_id).flow

    for node_id in forward.internal_nodes:
        assert forward.node(node_id).pressure == shuffled.node(node_id).pressure


def test_flow_runs_backwards_when_the_downstream_boundary_is_the_higher_one():
    topology = single_branch(
        from_pressure=100.0,
        to_pressure=200.0,
        shutoff_rise=0.0,
    )

    result = solve_network(topology)

    # -0.01 * |q|q = 100 -> q = -100. A resistance always opposes the flow
    # that caused it, so reversing the driving pressure reverses the flow.
    assert result.converged
    assert topology.branch("B-01").flow == pytest.approx(-100.0)


def test_a_machine_overrun_by_its_discharge_is_driven_backwards():
    topology = single_branch(
        from_pressure=100.0,
        to_pressure=300.0,
        shutoff_rise=75.0,
    )

    result = solve_network(topology)

    # 100 + 75 - 0.01|q|q - 300 = 0 -> q = -sqrt(125 / 0.01)
    assert result.converged
    assert topology.branch("B-01").flow == pytest.approx(
        -((125.0 / 0.01) ** 0.5),
    )


def test_solved_pressures_are_written_to_the_nodes():
    pump = ramped_pump()
    topology = series_plant(pump=pump, initial_pressure=50.0)

    solve_network(topology)

    middle = topology.node("N-MID")
    flow = topology.branch("B-PUMP").flow

    assert middle.pressure != 50.0
    assert middle.pressure == pytest.approx(
        50.0 + pump.characteristic(flow),
    )


def test_solved_flows_are_written_to_the_branches_and_their_streams():
    topology = series_plant(pump=ramped_pump())

    solve_network(topology)

    for branch in topology.branches.values():
        assert branch.flow != 0.0
        assert branch.stream.flow == branch.flow


def test_boundary_pressures_are_left_exactly_where_they_were():
    topology = series_plant(
        supply_pressure=50.0,
        delivery_pressure=65.0,
        pump=ramped_pump(),
    )

    solve_network(topology)

    assert topology.node("N-SUPPLY").pressure == 50.0
    assert topology.node("N-DELIVERY").pressure == 65.0


def test_damping_reaches_the_same_answer_by_a_slower_route():
    full = single_branch()
    damped = single_branch()

    full_result = NetworkSolver(full, damping=1.0).solve()
    damped_result = NetworkSolver(damped, damping=0.5).solve()

    assert full_result.converged
    assert damped_result.converged
    assert damped_result.iterations > full_result.iterations
    assert damped.branch("B-01").flow == pytest.approx(
        full.branch("B-01").flow,
    )


def test_damping_must_be_a_fraction_of_a_step():
    for damping in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="damping"):
            NetworkSolver(single_branch(), damping=damping)


def test_the_iteration_cap_stops_the_solve_and_reports_it_unconverged():
    topology = single_branch()

    result = NetworkSolver(topology, max_iterations=1).solve()

    assert not result.converged
    assert result.iterations == 1
    assert result.residual > 1.0


def test_a_solve_that_does_not_converge_leaves_the_plant_untouched():
    topology = series_plant(pump=ramped_pump(), initial_pressure=50.0)

    result = NetworkSolver(topology, max_iterations=1).solve()

    assert not result.converged
    assert topology.node("N-MID").pressure == 50.0
    assert topology.branch("B-PUMP").flow == 0.0
    assert topology.branch("B-LINE").flow == 0.0


def test_a_plant_the_solver_cannot_move_is_reported_rather_than_looped_on():
    # Two devices with no slope at all between mismatched boundaries: no
    # flow satisfies either branch equation, and no Newton step improves on
    # any other, so the line search has nothing to find.
    supply = Node("N-A", pressure=100.0, is_boundary=True)
    middle = Node("N-B", pressure=100.0)
    delivery = Node("N-C", pressure=150.0, is_boundary=True)

    topology = Topology(
        [supply, middle, delivery],
        [
            Branch("B-1", supply, middle, SolverCurve("R-1", resistance=0.0)),
            Branch("B-2", middle, delivery, SolverCurve("R-2", resistance=0.0)),
        ],
    )

    result = NetworkSolver(topology, max_iterations=200).solve()

    assert not result.converged
    assert result.iterations < 200


def test_iterations_never_exceed_the_cap():
    for cap in (1, 2, 5, 50):
        result = NetworkSolver(single_branch(), max_iterations=cap).solve()

        assert result.iterations <= cap


def test_a_topology_with_no_boundary_node_is_refused():
    first = Node("N-A", pressure=100.0)
    second = Node("N-B", pressure=100.0)

    topology = Topology(
        [first, second],
        [Branch("B-1", first, second, SolverCurve(resistance=0.01))],
    )

    with pytest.raises(SolverError, match="no boundary node"):
        NetworkSolver(topology)


def test_an_internal_node_no_branch_reaches_is_refused():
    supply = Node("N-A", pressure=200.0, is_boundary=True)
    delivery = Node("N-B", pressure=100.0, is_boundary=True)
    orphan = Node("N-Z", pressure=100.0)

    topology = Topology(
        [supply, delivery, orphan],
        [Branch("B-1", supply, delivery, SolverCurve(resistance=0.01))],
    )

    with pytest.raises(SolverError, match="no solution as posed"):
        solve_network(topology)


def test_tolerances_and_the_iteration_cap_are_checked_at_construction():
    with pytest.raises(ValueError, match="tolerances must be positive"):
        NetworkSolver(single_branch(), pressure_tolerance=0.0)

    with pytest.raises(ValueError, match="tolerances must be positive"):
        NetworkSolver(single_branch(), flow_tolerance=-1.0)

    with pytest.raises(ValueError, match="max_iterations"):
        NetworkSolver(single_branch(), max_iterations=0)


def test_a_looser_tolerance_converges_sooner_on_the_same_plant():
    tight = single_branch()
    loose = single_branch()

    tight_result = NetworkSolver(tight, pressure_tolerance=1e-10).solve()
    loose_result = NetworkSolver(loose, pressure_tolerance=1e-2).solve()

    assert tight_result.converged
    assert loose_result.converged
    assert loose_result.iterations < tight_result.iterations


def test_a_nearly_closed_valve_narrows_the_flow_without_dividing_by_zero():
    flows = []

    for resistance in (0.01, 1.0, 1e3, 1e6):
        topology = single_branch(resistance=resistance)

        result = solve_network(topology)

        assert result.converged
        flows.append(topology.branch("B-01").flow)

    assert flows == sorted(flows, reverse=True)
    assert flows[-1] == pytest.approx((25.0 / 1e6) ** 0.5)


def test_a_gas_network_solves_in_its_own_process_domain():
    # SCFM throughout: the compressor and the line downstream of it are one
    # hydraulic problem. A liquid branch would not belong in it.
    compressor = GasCompressor()
    compressor.start()
    compressor.set_load_target(1.0)
    compressor.integrate(100.0)

    suction = Node("N-SUCTION", pressure=750.0, is_boundary=True)
    middle = Node("N-DISCH", pressure=750.0)
    header = Node("N-HEADER", pressure=750.0, is_boundary=True)

    topology = Topology(
        [suction, middle, header],
        [
            Branch("B-K101", suction, middle, compressor),
            Branch("B-FV101", middle, header, SolverCurve("FV-101", resistance=0.01)),
        ],
    )

    result = solve_network(topology)

    assert result.converged
    assert compressor.load == 1.0
    assert topology.branch("B-K101").flow == pytest.approx(
        (220.0 / (compressor.compressor_resistance + 0.01)) ** 0.5,
    )
    assert topology.node("N-DISCH").pressure > 750.0


def test_a_plant_built_by_the_loader_solves(tmp_path):
    plant = load_plant(
        {
            "nodes": [
                {"id": "N-01", "boundary": True, "pressure": 50.0},
                {"id": "N-02", "boundary": False, "pressure": 50.0},
                {"id": "N-03", "boundary": True, "pressure": 60.0},
            ],
            "equipment": [
                {
                    "tag": "P-101",
                    "type": "pump",
                    "node_in": "N-01",
                    "node_out": "N-02",
                    "design": {"shutoff_pressure_rise": 75.0},
                },
                {
                    "tag": "P-102",
                    "type": "pump",
                    "node_in": "N-02",
                    "node_out": "N-03",
                    "design": {"shutoff_pressure_rise": 0.0},
                },
            ],
        },
    )

    booster = plant.topology.device("P-101")
    booster.start()
    booster.set_speed_target(1.0)
    booster.integrate(100.0)

    result = solve_network(plant.topology)

    assert result.converged
    assert plant.topology.branch("B-P-101").flow == pytest.approx(
        plant.topology.branch("B-P-102").flow,
    )
    assert plant.topology.node("N-01").pressure == 50.0
    assert plant.topology.node("N-03").pressure == 60.0


def test_the_solver_writes_nothing_to_the_devices_it_reads():
    pump = ramped_pump()
    topology = series_plant(pump=pump)

    before = pump.get_state()

    solve_network(topology)

    assert pump.get_state() == before


def test_a_device_port_never_holds_a_solved_value():
    topology = series_plant(pump=ramped_pump())

    solve_network(topology)

    for device in topology.devices.values():
        for port in device.ports.values():
            assert set(type(port).__slots__) == {"name", "direction", "node"}


def test_the_result_reports_both_residuals_in_their_own_units():
    topology = series_plant(pump=ramped_pump())

    result = solve_network(topology)

    assert result.pressure_residual < DEFAULT_PRESSURE_TOLERANCE
    assert result.flow_residual < DEFAULT_FLOW_TOLERANCE
    assert result.residual == pytest.approx(
        max(
            result.pressure_residual / DEFAULT_PRESSURE_TOLERANCE,
            result.flow_residual / DEFAULT_FLOW_TOLERANCE,
        ),
    )
