"""
Simulation engine — advances equipment, solves the network and publishes
snapshots.

Owns the clock and a tag-keyed collection of equipment. Each step advances
the clock, integrates every device by the same elapsed simulated time —
never touching flow or pressure, per the Equipment contract (C1) — then
solves the plant's pressure-flow network against the freshly integrated
slow state and publishes one immutable Snapshot (C4), the only thing the
rest of the system reads. Integrating first is what lets the solve see the
load a compressor has just ramped to rather than the one it left.

Flow and pressure are solver outputs and live on the topology: node
pressures and branch streams. The snapshot's nodes and streams sections
report them; devices hold none of their own.

An engine built without a topology (`Engine(devices)`) has nothing to
solve: it integrates only, and the snapshot's solver section reports the
trivial placeholder. `Engine.from_plant` is the connected form. It builds
its equipment from `Plant.devices`, never `Topology.devices`, because a
coupling device such as a Vessel sits in no topology and would otherwise
never be integrated (ADR 0001 section 12.6), and it wires against
`Plant.topology`, which raises on a multi-domain plant — one solver per
domain arrives with T5-2.

A solve that does not converge leaves the plant exactly as it was (T4-3),
so the step still advances time and slow state but flow and pressure hold
their last solved values, and the failure is published in the snapshot's
solver section rather than raised. A network that cannot be solved as
posed raises SolverError.

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

from collections.abc import Iterable

from app.engine.clock import SimulationClock
from app.engine.network import NetworkSolver, SolverResult
from app.engine.snapshot import DEFAULT_SOLVER_STATUS, Snapshot, build_snapshot, solver_status
from app.equipment.base import Equipment
from app.plant.loader import Plant
from app.plant.topology import Topology


class Engine:
    def __init__(
        self,
        equipment: Iterable[Equipment] = (),
        topology: Topology | None = None,
    ) -> None:
        self.clock = SimulationClock()
        self.equipment: dict[str, Equipment] = {}
        self.topology = topology
        self.solver = None if topology is None else NetworkSolver(topology)
        self.solver_result: SolverResult | None = None

        for device in equipment:
            self.add_equipment(device)

        self._solve()

    @classmethod
    def from_plant(cls, plant: Plant) -> "Engine":
        return cls(plant.devices.values(), plant.topology)

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
        simulated time the clock actually applied, solve the network, and
        publish the resulting snapshot.

        While stopped (the clock paused), the clock applies zero elapsed
        time, and integrate(0) is a no-op per the Equipment contract — so
        stepping a stopped engine changes nothing, and the solve that
        follows reproduces the state it started from.
        """
        elapsed = self.clock.step(dt)

        for device in self.equipment.values():
            device.integrate(elapsed)

        self._solve()

        return self.snapshot()

    def snapshot(self) -> Snapshot:
        equipment_state = {
            tag: device.get_state()
            for tag, device in self.equipment.items()
        }

        clock_state = self.clock.get_state()

        topology_state = (
            {} if self.topology is None else self.topology.get_state()
        )

        return build_snapshot(
            sim_time=clock_state["sim_time"],
            speed=clock_state["speed"],
            running=not clock_state["paused"],
            equipment=equipment_state,
            nodes=topology_state.get("nodes"),
            streams=topology_state.get("streams"),
            solver=(
                DEFAULT_SOLVER_STATUS
                if self.solver_result is None
                else solver_status(self.solver_result)
            ),
        )

    def _solve(self) -> None:
        if self.solver is not None:
            self.solver_result = self.solver.solve()
