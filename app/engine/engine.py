"""
Simulation engine — advances equipment, couples inventory to the plant,
solves every flow domain and publishes snapshots.

Owns the clock and a tag-keyed collection of equipment. Each step advances
the clock, integrates every device by the same elapsed simulated time —
never touching flow or pressure, per the Equipment contract (C1) — writes
the boundary conditions that slow state now implies, solves each flow domain
against them, reads the resulting exchange back onto the coupling devices,
and publishes one immutable Snapshot (C4), the only thing the rest of the
system reads.

That order is the whole of the coupling, and it is explicit Euler with one
step of lag (T5-2). Integrating before writing boundaries is what makes the
published snapshot internally consistent: the boundary pressure it reports
is the one the level it reports produces. The lag lands instead on the
vessel's flows — a level advances on the flows solved at the end of the
previous step, because those are the only flows that exist when integrate
runs. Nothing iterates between the integrator and the solver, and nothing
solves a domain twice.

Flow and pressure are solver outputs and live on the topology: node
pressures and branch streams. The snapshot's nodes and streams sections
report them; devices hold none of their own. A coupling device's level is
the exception that proves it — the level is slow state the device owns, and
what reaches the plant is a head added to a battery limit, never a pressure
the device read.

One solver per flow domain. Domains are independent within a step because
each is solved against boundary conditions held fixed for the whole step, so
solve order cannot matter. Node ids and branch ids are unique plant-wide, so
the sections merge without collision and C4 needs no change. C4's solver
section is one row for the whole plant: converged only if every domain
converged, reporting the worst iteration count and the worst residual, which
is the reading that cannot flatter a plant where one domain failed.
`residual` is dimensionless, so the worst of several is meaningful.

An engine built without a topology (`Engine(devices)`) has nothing to
solve: it integrates only, and the snapshot's solver section reports the
trivial placeholder. `Engine.from_plant` is the connected form. It builds
its equipment from `Plant.devices`, never `Topology.devices`, because a
coupling device such as a Vessel sits in no topology and would otherwise
never be integrated (ADR 0001 section 12.6), and it wires one solver against
each entry in `Plant.topologies`.

A solve that does not converge leaves that domain exactly as it was (T4-3),
so the step still advances time and slow state but its flow and pressure
hold their last solved values, and the failure is published in the
snapshot's solver section rather than raised. No coupling reads a flow out
of a domain that failed. A network that cannot be solved as posed raises
SolverError.

start()/stop() and the snapshot's running field delegate entirely to the
clock's pause/resume. There is deliberately no second "is it running"
flag to keep in sync with the clock's own — a stopped engine is exactly
a paused clock. A stopped engine still solves, and since nothing moved,
the solve lands on the same answer.

The clock is the sole speed authority. A device has no speed multiplier of
its own to consult.

dt is always injected by the caller, never defaulted from config or read
from a wall clock — determinism depends on the caller owning time.
"""

from collections.abc import Iterable, Mapping

from app.engine.clock import SimulationClock
from app.engine.coupling import VesselCoupling, build_couplings
from app.engine.network import NetworkSolver, SolverResult
from app.engine.snapshot import DEFAULT_SOLVER_STATUS, JSONValue, Snapshot, build_snapshot
from app.equipment.base import Equipment
from app.plant.loader import DEFAULT_DOMAIN, Plant
from app.plant.topology import Topology


class Engine:
    def __init__(
        self,
        equipment: Iterable[Equipment] = (),
        topology: Topology | None = None,
        topologies: Mapping[str, Topology] | None = None,
        couplings: Iterable[VesselCoupling] = (),
    ) -> None:
        if topology is not None and topologies is not None:
            raise ValueError(
                "pass either topology (one domain) or topologies (many), "
                "not both",
            )

        if topology is not None:
            topologies = {DEFAULT_DOMAIN: topology}

        self.clock = SimulationClock()
        self.equipment: dict[str, Equipment] = {}
        self.topologies: dict[str, Topology] = dict(topologies or {})
        self.solvers: dict[str, NetworkSolver] = {
            domain: NetworkSolver(graph)
            for domain, graph in self.topologies.items()
        }
        self.solver_results: dict[str, SolverResult] = {}
        self.couplings: list[VesselCoupling] = list(couplings)

        for device in equipment:
            self.add_equipment(device)

        self._couple()

    @classmethod
    def from_plant(cls, plant: Plant) -> "Engine":
        return cls(
            plant.devices.values(),
            topologies=plant.topologies,
            couplings=build_couplings(plant.devices.values(), plant.topologies),
        )

    @property
    def topology(self) -> Topology | None:
        """The sole topology, for the single-domain form.

        Raises rather than picking one on a plant that spans several, which
        is the same bargain `Plant.topology` makes: a caller that has not
        thought about domains should be told, not served an arbitrary one.
        """
        if not self.topologies:
            return None

        if len(self.topologies) > 1:
            raise ValueError(
                f"engine spans {len(self.topologies)} flow domains "
                f"{list(self.topologies)}, use Engine.topologies",
            )

        return next(iter(self.topologies.values()))

    @property
    def solver(self) -> NetworkSolver | None:
        return None if self._sole_domain is None else self.solvers[self._sole_domain]

    @solver.setter
    def solver(self, solver: NetworkSolver) -> None:
        domain = self._sole_domain

        if domain is None:
            raise ValueError(
                "this engine has no single domain to replace the solver of, "
                "assign into Engine.solvers",
            )

        self.solvers[domain] = solver

    @property
    def solver_result(self) -> SolverResult | None:
        domain = self._sole_domain

        return None if domain is None else self.solver_results.get(domain)

    def add_equipment(self, device: Equipment) -> None:
        """Register a device, keyed by its own tag.

        The only way to add equipment, so the dict key and device.tag can
        never diverge.
        """
        self.equipment[device.tag] = device

    def start(self) -> None:
        self.clock.resume()

    def stop(self) -> None:
        self.clock.pause()

    def step(self, dt: float) -> Snapshot:
        """Advance the clock by dt, integrate every device by the elapsed
        simulated time the clock actually applied, couple the resulting slow
        state to the plant, solve every flow domain, and publish the
        resulting snapshot.

        While stopped (the clock paused), the clock applies zero elapsed
        time, and integrate(0) is a no-op per the Equipment contract — so
        stepping a stopped engine changes nothing: the boundary written from
        an unchanged level is the value already there, and the solve that
        follows reproduces the state it started from.
        """
        elapsed = self.clock.step(dt)

        for device in self.equipment.values():
            device.integrate(elapsed)

        self._couple()

        return self.snapshot()

    def snapshot(self) -> Snapshot:
        equipment_state = {
            tag: device.get_state()
            for tag, device in self.equipment.items()
        }

        clock_state = self.clock.get_state()

        nodes: dict[str, dict[str, JSONValue]] = {}
        streams: dict[str, dict[str, JSONValue]] = {}

        for graph in self.topologies.values():
            state = graph.get_state()

            nodes.update(state["nodes"])
            streams.update(state["streams"])

        return build_snapshot(
            sim_time=clock_state["sim_time"],
            speed=clock_state["speed"],
            running=not clock_state["paused"],
            equipment=equipment_state,
            nodes=nodes,
            streams=streams,
            solver=self._solver_section(),
        )

    def _couple(self) -> None:
        """Boundaries down, solve, exchange back up — one pass, no iteration.

        Called from the constructor as well as from step(), so a freshly
        built engine already reports a plant whose boundaries reflect the
        inventory standing in it, rather than one step of an as-built plant
        nobody asked for.
        """
        for coupling in self.couplings:
            coupling.write_boundary_pressures()

        self._solve()

        converged = [
            domain
            for domain, result in self.solver_results.items()
            if result.converged
        ]

        for coupling in self.couplings:
            coupling.write_flows(converged)

    def _solve(self) -> None:
        for domain, solver in self.solvers.items():
            self.solver_results[domain] = solver.solve()

    def _solver_section(self) -> Mapping[str, JSONValue]:
        results = list(self.solver_results.values())

        if not results:
            return DEFAULT_SOLVER_STATUS

        return {
            "converged": all(result.converged for result in results),
            "iterations": max(result.iterations for result in results),
            "residual": max(result.residual for result in results),
        }

    @property
    def _sole_domain(self) -> str | None:
        if len(self.topologies) != 1:
            return None

        return next(iter(self.topologies))
