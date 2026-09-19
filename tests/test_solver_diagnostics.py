"""What a solve reports about itself, and what a snapshot does with it (T4-3).

The solver's own mathematics is tested in test_network_solver.py. This module
tests the thing a training simulator cannot afford to get wrong: that a solve
which did not land says so, in the result and in the snapshot a console would
read, and that the plant behind it is not carrying an unchecked guess.

Fixtures come from test_network_solver so there is one SolverCurve — Equipment
keys its registry by class name, so a second class of the same name would
quietly displace the first.
"""

import pytest

from app.engine.network import (
    DEFAULT_MAX_ITERATIONS,
    ITERATION_CAP,
    LINE_SEARCH_STALL,
    NetworkSolver,
    SolverError,
    SolverResult,
    solve_network,
)
from app.engine.snapshot import build_snapshot, solver_status
from app.plant.topology import Branch, Node, Topology
from tests.test_network_solver import (
    SolverCurve,
    ramped_pump,
    series_plant,
    single_branch,
)


def orphan_node_plant():
    """Structurally impossible: N-Z is an unknown pressure that appears in no
    equation, so the Jacobian has an empty column whatever the iteration does.
    """
    supply = Node("N-A", pressure=200.0, is_boundary=True)
    delivery = Node("N-B", pressure=100.0, is_boundary=True)
    orphan = Node("N-Z", pressure=125.0)

    return Topology(
        [supply, delivery, orphan],
        [Branch("B-1", supply, delivery, SolverCurve(resistance=0.01))],
    )


def slopeless_plant():
    """Two pure-head devices between mismatched boundaries: no flow satisfies
    either branch equation and no step improves on any other, so the line
    search has nothing to find.
    """
    supply = Node("N-A", pressure=100.0, is_boundary=True)
    middle = Node("N-B", pressure=100.0)
    delivery = Node("N-C", pressure=150.0, is_boundary=True)

    return Topology(
        [supply, middle, delivery],
        [
            Branch("B-1", supply, middle, SolverCurve("R-1", resistance=0.0)),
            Branch("B-2", middle, delivery, SolverCurve("R-2", resistance=0.0)),
        ],
    )


def snapshot_of(topology, result):
    """The snapshot a caller would publish after a solve — which is what T4-4
    will do, from the engine, with the diagnostics this task defines.
    """
    return build_snapshot(
        sim_time=0.0,
        speed=1.0,
        running=True,
        equipment={
            tag: device.get_state()
            for tag, device in topology.devices.items()
        },
        solver=solver_status(result),
    )


def test_a_structurally_invalid_network_raises_rather_than_flagging():
    with pytest.raises(SolverError, match="no solution as posed"):
        solve_network(orphan_node_plant())


def test_a_network_with_nothing_to_anchor_it_raises_at_construction():
    first = Node("N-A", pressure=100.0)
    second = Node("N-B", pressure=100.0)

    topology = Topology(
        [first, second],
        [Branch("B-1", first, second, SolverCurve(resistance=0.01))],
    )

    with pytest.raises(SolverError, match="no boundary node"):
        solve_network(topology)


def test_the_iteration_cap_flags_the_solve_and_names_the_cap():
    result = NetworkSolver(single_branch(), max_iterations=2).solve()

    assert not result.converged
    assert result.failure == ITERATION_CAP
    assert result.residual > 1.0


def test_a_stalled_line_search_flags_the_solve_and_names_the_stall():
    result = NetworkSolver(slopeless_plant(), max_iterations=200).solve()

    assert not result.converged
    assert result.failure == LINE_SEARCH_STALL
    assert result.iterations < 200


def test_heavy_under_relaxation_reads_as_a_slow_solve_not_a_stuck_one():
    """The condition T4-2 recorded, made legible without retuning anything.

    Damping at 0.25 runs out the default cap on a plant that solves in a
    handful of iterations at full step. The diagnostics say which kind of
    failure that is: the cap, with a residual a few times tolerance rather
    than orders above it, so the remedy is more iterations and not a
    different starting state.
    """
    result = NetworkSolver(single_branch(), damping=0.25).solve()

    assert not result.converged
    assert result.failure == ITERATION_CAP
    assert result.iterations == DEFAULT_MAX_ITERATIONS
    assert 1.0 < result.residual < 1e3

    assert NetworkSolver(single_branch(), damping=1.0).solve().converged


def test_a_converged_solve_names_no_failure():
    result = solve_network(series_plant(pump=ramped_pump()))

    assert result.converged
    assert result.failure is None
    assert result.residual <= 1.0


def test_a_result_cannot_claim_success_and_name_a_failure():
    with pytest.raises(ValueError, match="converged"):
        SolverResult(
            converged=True,
            iterations=3,
            residual=0.5,
            pressure_residual=0.0,
            flow_residual=0.0,
            failure=ITERATION_CAP,
        )

    with pytest.raises(ValueError, match="converged"):
        SolverResult(
            converged=False,
            iterations=3,
            residual=99.0,
            pressure_residual=1.0,
            flow_residual=0.0,
        )


def test_raise_if_not_converged_escalates_a_flagged_solve():
    result = NetworkSolver(single_branch(), max_iterations=1).solve()

    with pytest.raises(SolverError, match=ITERATION_CAP):
        result.raise_if_not_converged()


def test_raise_if_not_converged_passes_a_landed_solve_through():
    result = solve_network(single_branch())

    assert result.raise_if_not_converged() is result


def test_describe_reports_the_iteration_count_and_the_failure():
    failed = NetworkSolver(single_branch(), max_iterations=1).solve()
    landed = solve_network(single_branch())

    assert ITERATION_CAP in failed.describe()
    assert "1 iterations" in failed.describe()

    assert "converged" in landed.describe()
    assert ITERATION_CAP not in landed.describe()


def test_solve_network_and_solve_report_the_same_failure():
    """One policy, not two. The wrapper is where a second one would appear."""
    direct = NetworkSolver(single_branch(), max_iterations=2).solve()
    wrapped = solve_network(single_branch(), max_iterations=2)

    assert (direct.converged, direct.failure) == (wrapped.converged, wrapped.failure)
    assert direct.iterations == wrapped.iterations
    assert direct.residual == pytest.approx(wrapped.residual)

    with pytest.raises(SolverError):
        NetworkSolver(orphan_node_plant()).solve()

    with pytest.raises(SolverError):
        solve_network(orphan_node_plant())


def test_a_flagged_solve_leaves_the_plant_where_it_was_and_says_so():
    """The two halves of the guarantee together: the topology holds no guess,
    and the snapshot built from the result does not report a solve that landed.
    """
    topology = series_plant(pump=ramped_pump(), initial_pressure=50.0)

    result = NetworkSolver(topology, max_iterations=1).solve()
    published = snapshot_of(topology, result).as_dict()

    assert topology.node("N-MID").pressure == 50.0
    assert topology.branch("B-PUMP").flow == 0.0
    assert topology.branch("B-LINE").flow == 0.0

    assert published["solver"]["converged"] is False
    assert published["solver"]["iterations"] == 1
    assert published["solver"]["residual"] > 1.0


def test_a_converged_solve_reaches_the_snapshot_with_its_diagnostics():
    topology = series_plant(pump=ramped_pump())

    result = solve_network(topology)
    published = snapshot_of(topology, result).as_dict()

    assert published["solver"]["converged"] is True
    assert published["solver"]["iterations"] == result.iterations
    assert published["solver"]["iterations"] > 0
    assert published["solver"]["residual"] == pytest.approx(result.residual)
    assert published["solver"]["residual"] <= 1.0


def test_a_converged_hand_built_plant_lands_inside_both_tolerances():
    """Standing in for the reference-plant acceptance test, which needs
    config/plants/olefins_lite.yaml — T3-4, Blocked on the flow-domain
    decision. Single hydraulic domain throughout, GPM everywhere.
    """
    topology = series_plant(pump=ramped_pump())

    result = solve_network(topology)

    assert result.converged
    assert result.residual <= 1.0
    assert result.pressure_residual < NetworkSolver(topology).pressure_tolerance
    assert result.flow_residual < NetworkSolver(topology).flow_tolerance
