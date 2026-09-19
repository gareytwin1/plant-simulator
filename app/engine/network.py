"""
Network solver: every node pressure and every branch flow at once (T4-2).

Until this module exists a device can only be asked what it *would* do at a
flow someone hands it. The plant-wide question — what flow does this train
actually carry, and what pressure does each point in it sit at — is a
simultaneous one, because a branch's flow depends on the pressures at its
ends and those pressures depend on what every other branch is carrying.

The unknowns are the branch flows and the internal node pressures. The
equations are, in the same order:

    branch b    from_node.pressure + characteristic(q_b) - to_node.pressure = 0
    node i      sum of flows in - sum of flows out = 0

One branch equation per branch and one mass balance per internal node, so the
system is square. Boundary node pressures are not unknowns: they anchor the
pressure field, which is why a topology without one is rejected here rather
than left to produce a singular matrix.

Newton-Raphson solves it. The branch rows are the only nonlinear ones — the
mass balances are linear and fall out exact — and C1 guarantees every device
curve is non-increasing in flow, so each branch row has one root and a
non-positive diagonal. The one place that guarantee runs thin is exactly zero
flow, where a quadratic curve's slope is zero as well: MIN_SLOPE_MAGNITUDE
keeps the diagonal off zero there, and backtracking keeps the large first step
that follows from overshooting. Neither affects the answer, only the path to
it — the iteration stops on the residual, not on the step.

State ownership is C2's, unchanged. The solver writes pressures through
`Node.set_pressure` and flows through `Branch.set_flow`, and it reads the
device curve only through `Branch.characteristic` / `Branch.residual`. No
solved value is written to a device or a port, and a non-converged solve
writes nothing at all: the plant is left where it was rather than holding a
guess nobody checked.

This module is the mathematics only. `Engine.step()` does not call it and the
Flask routes do not reach it — wiring it in, and retiring the devices' own
standalone operating point, is T4-4. Richer diagnostics and failure policy are
T4-3.

Units follow docs/UNITS_CONVENTION.md: pressures and branch residuals in psia,
flows and mass-balance residuals in the branch's own process-domain unit
(SCFM gas, GPM liquid). The two are never added together — see
`SolverResult.residual` for how a mixed-unit system reports one number.
"""

from dataclasses import dataclass

from app.plant.topology import Branch, Node, Topology


DEFAULT_PRESSURE_TOLERANCE = 1e-7  # psia
DEFAULT_FLOW_TOLERANCE = 1e-7  # SCFM or GPM
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_DAMPING = 1.0

# Halvings the line search will try before it calls the step a stall. 60 of
# them span a factor of 1e18, which is what a cold start needs: at zero flow
# a quadratic curve has no slope to speak of, so the first Newton step is
# enormous and the search has to walk a long way back to find the basin.
MAX_BACKTRACKS = 60

# Relative perturbation for the numerical slope of a branch curve. C1 asks a
# device for a value, not a derivative, so the Jacobian diagonal is a central
# difference. Large enough to stay clear of cancellation in double precision,
# small enough that the slope is the curve's and not a chord across it.
DERIVATIVE_STEP = 1e-6
DERIVATIVE_FLOOR = 1e-3  # absolute, for perturbing a branch near zero flow

# The Jacobian diagonal a branch row falls back to when its curve is locally
# flat. Small enough never to displace a real slope — a pump at 500 GPM is
# four orders above it — and only ever reached at a flow of almost exactly
# zero.
MIN_SLOPE_MAGNITUDE = 1e-9


class SolverError(RuntimeError):
    """The network cannot be solved as posed.

    Structural, not numerical: no boundary node to anchor the pressure field,
    an internal node nothing connects to, a Jacobian with no pivot. Running
    out of iterations is not one of these — that is an ordinary
    non-converged SolverResult, because a plant can be hard to solve without
    being ill-posed.
    """


@dataclass(frozen=True)
class SolverResult:
    """What one solve did. T4-3 owns the full diagnostic set.

    `residual` is the convergence measure, and it is dimensionless on
    purpose: a branch residual is in psia and a mass balance is in flow
    units, so the two are each divided by their own tolerance before being
    compared. It is the largest of those ratios, and converged is exactly
    `residual <= 1.0`. `pressure_residual` and `flow_residual` are the same
    two worst cases in their own units, for reading rather than testing
    against.
    """

    converged: bool
    iterations: int
    residual: float
    pressure_residual: float
    flow_residual: float


class NetworkSolver:
    """Newton-Raphson over one topology.

    The ordering — branches then internal nodes, each sorted by id — is fixed
    at construction and is what makes a solve reproducible: the same plant in
    the same state produces the same arithmetic in the same sequence, however
    the topology was assembled. Nothing here reads a clock or draws a random
    number.

    A solver holds no solution of its own. It is cheap to keep one per plant
    across timesteps, which is what T4-4 will do, and equally correct to
    build a fresh one per solve.
    """

    topology: Topology

    def __init__(
        self,
        topology: Topology,
        pressure_tolerance: float = DEFAULT_PRESSURE_TOLERANCE,
        flow_tolerance: float = DEFAULT_FLOW_TOLERANCE,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        damping: float = DEFAULT_DAMPING,
    ) -> None:
        if pressure_tolerance <= 0.0 or flow_tolerance <= 0.0:
            raise ValueError(
                f"tolerances must be positive, got pressure "
                f"{pressure_tolerance} psia and flow {flow_tolerance}",
            )

        if max_iterations < 1:
            raise ValueError(
                f"max_iterations must be at least 1, got {max_iterations}",
            )

        if not 0.0 < damping <= 1.0:
            raise ValueError(
                f"damping is the fraction of the Newton step to take and "
                f"must be in (0, 1], got {damping}",
            )

        if not topology.boundary_nodes:
            raise SolverError(
                "topology has no boundary node, so nothing anchors the "
                "pressure field and every pressure is free",
            )

        self.topology = topology

        self.pressure_tolerance = pressure_tolerance
        self.flow_tolerance = flow_tolerance
        self.max_iterations = max_iterations
        self.damping = damping

        self._branches: tuple[Branch, ...] = tuple(
            topology.branch(branch_id)
            for branch_id in sorted(topology.branches)
        )
        self._internal: tuple[Node, ...] = tuple(
            topology.node(node_id)
            for node_id in sorted(topology.internal_nodes)
        )

        column = {node.id: i for i, node in enumerate(self._internal)}

        # Which branches meet each internal node, and with which sign in its
        # mass balance: +1 arriving, -1 leaving. Built once because the graph
        # does not change between solves.
        self._incidence: tuple[tuple[tuple[int, float], ...], ...] = tuple(
            tuple(
                (i, 1.0 if branch.to_node is node else -1.0)
                for i, branch in enumerate(self._branches)
                if node in branch.nodes
            )
            for node in self._internal
        )

        self._from_column = tuple(
            column.get(branch.from_node.id) for branch in self._branches
        )
        self._to_column = tuple(
            column.get(branch.to_node.id) for branch in self._branches
        )

        self._flows = len(self._branches)
        self._size = self._flows + len(self._internal)

    def solve(self) -> SolverResult:
        """Drive every residual to zero and write the result to the topology.

        Returns without writing anything if it does not get there: a plant
        holding the last iterate of a failed solve looks exactly like a
        plant holding an answer, and there is no way for a caller reading
        node pressures to tell the difference afterwards.
        """
        if self._size == 0:
            return SolverResult(
                converged=True,
                iterations=0,
                residual=0.0,
                pressure_residual=0.0,
                flow_residual=0.0,
            )

        entry_pressures = [node.pressure for node in self._internal]

        x = [branch.flow for branch in self._branches]
        x += [node.pressure for node in self._internal]

        residuals = self._residuals(x)
        iterations = 0

        while self._norm(residuals) > 1.0:
            if iterations == self.max_iterations:
                self._restore(entry_pressures)

                return self._result(False, iterations, residuals)

            step = self._newton_step(x, residuals)
            advanced = self._line_search(x, residuals, step)

            if advanced is None:
                self._restore(entry_pressures)

                return self._result(False, iterations, residuals)

            x, residuals = advanced
            iterations += 1

        self._commit(x)

        return self._result(True, iterations, residuals)

    def _residuals(self, x: list[float]) -> list[float]:
        # Branch residuals are read through Branch.residual, which reads the
        # node pressures, so the trial pressures have to be on the nodes
        # before it is asked. Writing them is the solver's own business —
        # node pressure is a solver output — and solve() puts them back if
        # the iteration never lands.
        self._write_pressures(x)

        residuals = [
            branch.residual(x[i]) for i, branch in enumerate(self._branches)
        ]

        residuals += [
            sum(sign * x[i] for i, sign in incidence)
            for incidence in self._incidence
        ]

        return residuals

    def _newton_step(
        self,
        x: list[float],
        residuals: list[float],
    ) -> list[float]:
        jacobian = [[0.0] * self._size for _ in range(self._size)]

        for i, branch in enumerate(self._branches):
            jacobian[i][i] = self._slope(branch, x[i])

            from_column = self._from_column[i]
            to_column = self._to_column[i]

            if from_column is not None:
                jacobian[i][self._flows + from_column] = 1.0

            if to_column is not None:
                jacobian[i][self._flows + to_column] = -1.0

        for row, incidence in enumerate(self._incidence):
            for i, sign in incidence:
                jacobian[self._flows + row][i] = sign

        return _solve_linear(
            jacobian,
            [-residual for residual in residuals],
        )

    def _slope(self, branch: Branch, flow: float) -> float:
        h = DERIVATIVE_STEP * max(abs(flow), DERIVATIVE_FLOOR)

        slope = (
            branch.characteristic(flow + h)
            - branch.characteristic(flow - h)
        ) / (2.0 * h)

        # C1 promises the curve is non-increasing, so a positive slope here
        # is numerical noise on a flat stretch, not a rising curve.
        return min(slope, -MIN_SLOPE_MAGNITUDE)

    def _line_search(
        self,
        x: list[float],
        residuals: list[float],
        step: list[float],
    ) -> tuple[list[float], list[float]] | None:
        """Take the largest damped fraction of the Newton step that improves
        the residual, halving until one does.

        The full step is right near the solution and wrong far from it — from
        a cold plant the branch rows are nearly flat and Newton asks for a
        flow no pipe could carry. `None` means no fraction helped, which is a
        stall rather than a slow solve, so solve() stops instead of burning
        the iteration cap on it.
        """
        norm = self._norm(residuals)
        fraction = self.damping

        for _ in range(MAX_BACKTRACKS):
            trial = [value + fraction * delta for value, delta in zip(x, step)]
            trial_residuals = self._residuals(trial)

            if self._norm(trial_residuals) < norm:
                return trial, trial_residuals

            fraction /= 2.0

        return None

    def _norm(self, residuals: list[float]) -> float:
        # Dimensionless: psia against the pressure tolerance, flow against
        # the flow tolerance. Adding a psia to a GPM would be the one thing
        # docs/UNITS_CONVENTION.md forbids outright.
        scaled = [
            abs(residual) / self.pressure_tolerance
            for residual in residuals[: self._flows]
        ]

        scaled += [
            abs(residual) / self.flow_tolerance
            for residual in residuals[self._flows :]
        ]

        return max(scaled)

    def _write_pressures(self, x: list[float]) -> None:
        for i, node in enumerate(self._internal):
            node.set_pressure(x[self._flows + i])

    def _commit(self, x: list[float]) -> None:
        self._write_pressures(x)

        for i, branch in enumerate(self._branches):
            branch.set_flow(x[i])

    def _restore(self, pressures: list[float]) -> None:
        for node, pressure in zip(self._internal, pressures):
            node.set_pressure(pressure)

    def _result(
        self,
        converged: bool,
        iterations: int,
        residuals: list[float],
    ) -> SolverResult:
        branch_residuals = [abs(r) for r in residuals[: self._flows]]
        node_residuals = [abs(r) for r in residuals[self._flows :]]

        return SolverResult(
            converged=converged,
            iterations=iterations,
            residual=self._norm(residuals),
            pressure_residual=max(branch_residuals, default=0.0),
            flow_residual=max(node_residuals, default=0.0),
        )


def solve_network(
    topology: Topology,
    pressure_tolerance: float = DEFAULT_PRESSURE_TOLERANCE,
    flow_tolerance: float = DEFAULT_FLOW_TOLERANCE,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    damping: float = DEFAULT_DAMPING,
) -> SolverResult:
    """Solve a topology once. For a caller that solves the same plant every
    timestep, build a NetworkSolver and keep it instead.
    """
    return NetworkSolver(
        topology,
        pressure_tolerance=pressure_tolerance,
        flow_tolerance=flow_tolerance,
        max_iterations=max_iterations,
        damping=damping,
    ).solve()


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Dense Gaussian elimination with partial pivoting.

    Thirty lines rather than a dependency: the V1 train is a handful of
    branches, so the system is a few rows square and a library solver would
    buy nothing but an install. Pivot selection breaks ties towards the
    earliest row, so the arithmetic is the same on every run.
    """
    n = len(rhs)
    rows = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(n):
        pivot = col

        for row in range(col + 1, n):
            if abs(rows[row][col]) > abs(rows[pivot][col]):
                pivot = row

        if rows[pivot][col] == 0.0:
            raise SolverError(
                f"the network has no solution as posed: column {col} of the "
                f"Jacobian is empty, so one unknown appears in no equation — "
                f"usually an internal node no branch reaches",
            )

        rows[col], rows[pivot] = rows[pivot], rows[col]

        for row in range(col + 1, n):
            factor = rows[row][col] / rows[col][col]

            if factor == 0.0:
                continue

            for k in range(col, n + 1):
                rows[row][k] -= factor * rows[col][k]

    solution = [0.0] * n

    for row in reversed(range(n)):
        total = rows[row][n] - sum(
            rows[row][k] * solution[k] for k in range(row + 1, n)
        )

        solution[row] = total / rows[row][row]

    return solution
