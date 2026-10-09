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

Energy follows the flow (T6-5). Once a domain converges, its transport
recomputes every node and stream temperature upwind of the flows just
solved - see app/engine/transport.py. It runs after the solve and before the
snapshot, moves no mass and touches no pressure, so it cannot disturb the
solve it reads. Boundary nodes supply at the temperatures the engine was
built with, and at the 60 °F standard wherever none was given. A domain whose
temperatures have no steady answer holds them, as a failed solve holds its
flows, and says so on its transport rather than raising.

A solve that does not converge leaves that domain exactly as it was (T4-3),
so the step still advances time and slow state but its flow and pressure
hold their last solved values, and the failure is published in the
snapshot's solver section rather than raised. No coupling reads a flow out
of a domain that failed, and no transport does either: its temperatures hold
with its flows. A network that cannot be solved as posed raises
SolverError.

What the snapshot's measured sections carry is what the plant's instruments
indicate (T13-2). The engine reads every device, node and stream as the
physics has it, publishes that as `Snapshot.truth`, and runs it through its
instruments (app/engine/instruments.py) for the view every consumer reads.
Instruments sit outside the physics entirely: nothing in a step reads an
indicated value, so a biased transmitter changes what is published and
nothing that is solved.

**Control runs first, on what the last snapshot showed (T8-4).** A step is,
in this order and no other:

  1. advance the clock;
  2. **control** - every loop reads its measurement from the indicated view
     of the plant as it stands at the start of the step, which is what the
     previous snapshot published, computes its output over the elapsed time,
     and posts it to the command arbiter as a controller demand; the arbiter
     then writes every bound output;
  3. integrate every device, so a valve strokes toward the target control
     just wrote;
  4. couple and solve, as above;
  5. publish the snapshot, whose controllers section carries each loop's
     output from step 2 against the measurement standing after step 4, with
     what a faceplate needs beside them (T16-14): its gains, output range,
     whether an operator may retune it, and its measurement's unit.

Nothing in step 2 can see a mid-solve value, because no solve has started:
a loop reads only what the last step published, and the solve that follows
reads only the slow state the loop moved. And there is no extra step of lag:
a measurement published at the end of step n moves the valve during step
n+1, and the flow that results is published at the end of that same step.
Running control after the solve instead would write a target that step n+1's
integrate had already missed, and a loop would answer every change a step
late.

Loops run in the order they were added - configuration order, from
`Engine.from_plant`. Within one step that order changes nothing: every loop
reads the same pre-step view, and no two loops drive one output (the
arbiter refuses a second binding), so no loop can read or overwrite what
another has just written. Control reads the *indicated* view, never the
truth, so a biased transmitter fools the loop exactly as it fools the
operator. A loop is primed on its first execution - its integral preloaded
to reproduce the output it was bound at - so a loop that starts in AUTO
starts without a bump, as a MANUAL loop already does by tracking.

Every loop output reaches its device through `Engine.arbiter` (T7-4), never
by calling the setter itself, so an operator or an interlock that later
posts to the same arbiter outranks the controller by precedence rather than
by running last. A loop writes its output in MANUAL too: that output is the
operator's manual command.

**Envelope classification runs once per step, after couple (T9-4).** Every
configured `limits` entry (T9-2) gets one `Evaluator` and one
`ExcursionTracker` (T9-3), advanced exactly once per step by the elapsed
simulated time - a second call in the same step would double-count the
on-delay and time-in-band state both carry. `Engine.snapshot()` only reads
the band each call left, so polling it between steps cannot re-advance
either. The published `envelope` section carries one entry per point
currently outside NORMAL, keyed `"{tag}.{variable}"`: `band` is the
ISA-style severity/side label (`isa_band` - "hi", "hihi", "hihihi" and the
"lo" equivalents), and `since` is the sim_time this engine's clock read when
that band was last entered. A point that clears to NORMAL drops out of the
section entirely rather than reporting a "normal" row.

**An equipment row also carries its device's solved points (T9-5).** A
device that sits in exactly one branch gets `flow`, `inlet_pressure`
and `outlet_pressure` on its row: that branch's stream flow and the pressures
of its from and to nodes. A branch is wired inlet-first (C2), so the names
follow port direction, never what a port is called. The device holds none of
them - they are composed here, in the truth, before the instruments run, so
an instrument on `equipment.K-101.outlet_pressure` is a transmitter of its
own, separate from one on the node, as a trip's transmitter is separate from
a control loop's. A device in no branch (a coupling device such as a vessel)
or in several gets none, and a device whose `get_state()` already publishes
one of the names keeps its own value.

That is what a `limits` entry, an interlock condition or a permissive
resolves against: a field of the equipment row the snapshot publishes. A
`limits` entry naming anything else is refused at construction - evaluating
nothing in its place would leave a configured trip silently dead.

start()/stop() and the snapshot's running field delegate entirely to the
clock's pause/resume. There is deliberately no second "is it running"
flag to keep in sync with the clock's own — a stopped engine is exactly
a paused clock. A stopped engine still solves, and since nothing moved,
the solve lands on the same answer. Nor does it control: a stopped step has
no elapsed time to integrate a loop over, so no loop runs and no output is
written.

The clock is the sole speed authority. A device has no speed multiplier of
its own to consult.

dt is always injected by the caller, never defaulted from config or read
from a wall clock — determinism depends on the caller owning time.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.controls.arbitration import CommandArbiter, Source
from app.controls.loader import LoopBinding, load_loops
from app.engine.clock import SimulationClock
from app.engine.coupling import VesselCoupling, build_couplings
from app.engine.instruments import Instrument, Point, indicate, true_reading
from app.engine.network import NetworkSolver, SolverResult
from app.engine.snapshot import DEFAULT_SOLVER_STATUS, JSONValue, Snapshot, build_snapshot
from app.engine.transport import ABSOLUTE_ZERO, DomainTransport
from app.envelope.evaluator import Evaluator, Severity, Side, isa_band
from app.envelope.loader import LimitKey, load_limits
from app.envelope.tracker import ExcursionTracker
from app.equipment.base import Equipment
from app.plant.loader import DEFAULT_DOMAIN, Plant
from app.plant.topology import Branch, Topology
from app.statetypes import StateError


@dataclass(frozen=True)
class EngineCheckpoint:
    """What `Engine` itself holds beyond its parts: which loops it has
    primed, and each envelope point's band and when that band was entered.
    The clock, devices, loops, arbiter, evaluators and trackers each
    checkpoint themselves; whoever saves a plant composes them."""

    primed: frozenset[str]
    envelope_band: dict[LimitKey, tuple[Severity, Side | None]]
    envelope_since: dict[LimitKey, float]


class Engine:
    def __init__(
        self,
        equipment: Iterable[Equipment] = (),
        topology: Topology | None = None,
        topologies: Mapping[str, Topology] | None = None,
        couplings: Iterable[VesselCoupling] = (),
        boundary_temperatures: Mapping[str, float] | None = None,
        instruments: Iterable[Instrument] = (),
        loops: Iterable[LoopBinding] = (),
        limits: Mapping[LimitKey, Evaluator] | None = None,
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
        self._branch_of: dict[str, Branch] = self._single_branches()
        self.couplings: list[VesselCoupling] = list(couplings)
        self.instruments: dict[str, Instrument] = {}
        self.loops: dict[str, LoopBinding] = {}
        self.arbiter = CommandArbiter()
        self._primed: set[str] = set()

        _check_boundary_temperatures(
            boundary_temperatures or {},
            self.topologies,
        )

        self.transports: dict[str, DomainTransport] = {
            domain: DomainTransport(domain, graph, boundary_temperatures or {})
            for domain, graph in self.topologies.items()
        }

        for device in equipment:
            self.add_equipment(device)

        self._couple()

        for instrument in instruments:
            self.add_instrument(instrument)

        self.limits: dict[LimitKey, Evaluator] = self._checked_limits(limits or {})
        self.trackers: dict[LimitKey, ExcursionTracker] = {
            key: ExcursionTracker(evaluator.limits)
            for key, evaluator in self.limits.items()
        }
        self._envelope_band: dict[LimitKey, tuple[Severity, Side | None]] = {}
        self._envelope_since: dict[LimitKey, float] = {}
        self._update_envelope(0.0)

        for binding in loops:
            self.add_loop(binding)

    @classmethod
    def from_plant(
        cls,
        plant: Plant,
        boundary_temperatures: Mapping[str, float] | None = None,
        instruments: Iterable[Instrument] = (),
    ) -> "Engine":
        return cls(
            plant.devices.values(),
            topologies=plant.topologies,
            couplings=build_couplings(plant.devices.values(), plant.topologies),
            boundary_temperatures=boundary_temperatures,
            instruments=instruments,
            loops=load_loops(plant).values(),
            limits=load_limits({"limits": plant.passthrough("limits")}),
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

    def add_instrument(self, instrument: Instrument) -> None:
        """Register an instrument, keyed by its own tag.

        Refused unless the point it reads is published and numeric now, its
        tag is free among devices, instruments and loops alike, and no other
        instrument already reads that point - a snapshot has one indicated
        value per point, so a second instrument there would have nowhere to
        show.
        """
        if (
            instrument.tag in self.equipment
            or instrument.tag in self.instruments
            or instrument.tag in self.loops
        ):
            raise ValueError(f"tag {instrument.tag!r} is already in use")

        for other in self.instruments.values():
            if other.point == instrument.point:
                raise ValueError(
                    f"{instrument.tag} reads {'.'.join(instrument.point)}, "
                    f"which {other.tag} already reads",
                )

        true_reading(instrument, self._truth())

        self.instruments[instrument.tag] = instrument

    def add_loop(self, binding: LoopBinding) -> None:
        """Register a loop, keyed by its own tag, and bind its output to the
        arbiter.

        Refused unless its tag is free among devices, instruments and loops,
        the point it measures is published now, and the device it drives is
        one this engine integrates - a loop on a device outside the engine
        would move a valve that never strokes. The arbiter refuses a second
        loop on an output one already drives.
        """
        tag = binding.tag

        if tag in self.equipment or tag in self.instruments or tag in self.loops:
            raise ValueError(f"tag {tag!r} is already in use")

        _reading(self._indicated(), binding.pv_point, f"loop {tag}")

        if binding.out_tag not in self.equipment:
            raise ValueError(
                f"loop {tag} drives {binding.out_tag!r}, which is not "
                f"equipment of this engine",
            )

        self.arbiter.bind(binding.out_tag, binding.output_setter)
        self.loops[tag] = binding

    def start(self) -> None:
        self.clock.resume()

    def stop(self) -> None:
        self.clock.pause()

    def step(self, dt: float) -> Snapshot:
        """Advance the clock by dt, run every loop on the measurements the
        last step published, integrate every device by the elapsed simulated
        time the clock actually applied, couple the resulting slow state to
        the plant, solve every flow domain, and publish the resulting
        snapshot. The order is the module docstring's, and it is deliberate.

        While stopped (the clock paused), the clock applies zero elapsed
        time, no loop runs, and integrate(0) is a no-op per the Equipment
        contract — so stepping a stopped engine changes nothing: the
        boundary written from an unchanged level is the value already there,
        and the solve that follows reproduces the state it started from.
        """
        elapsed = self.clock.step(dt)

        if elapsed > 0.0:
            self._control(elapsed)

        for device in self.equipment.values():
            device.integrate(elapsed)

        self._couple()
        self._update_envelope(elapsed)

        return self.snapshot()

    def snapshot(self) -> Snapshot:
        truth = self._truth()
        indicated = indicate(truth, self.instruments.values())
        clock_state = self.clock.get_state()
        controllers: dict[str, dict[str, JSONValue]] = {
            tag: {
                "pv": _reading(indicated, binding.pv_point, f"loop {tag}"),
                "sp": binding.loop.pid.setpoint,
                "out": binding.loop.output,
                "mode": binding.loop.mode.name,
                "kp": binding.loop.pid.kp,
                "ki": binding.loop.pid.ki,
                "kd": binding.loop.pid.kd,
                "out_min": binding.loop.pid.output_min,
                "out_max": binding.loop.pid.output_max,
                "tunable": binding.tunable,
                "pv_unit": binding.pv_unit,
            }
            for tag, binding in self.loops.items()
        }
        envelope: dict[str, dict[str, JSONValue]] = {}
        for (tag, variable), (severity, side) in self._envelope_band.items():
            if severity is Severity.NORMAL:
                continue
            assert side is not None  # a non-NORMAL band always has a side
            envelope[f"{tag}.{variable}"] = {
                "band": isa_band(severity, side),
                "since": self._envelope_since[(tag, variable)],
            }

        return build_snapshot(
            sim_time=clock_state["sim_time"],
            speed=clock_state["speed"],
            running=not clock_state["paused"],
            equipment=indicated["equipment"],
            nodes=indicated["nodes"],
            streams=indicated["streams"],
            controllers=controllers,
            envelope=envelope,
            solver=self._solver_section(),
            truth=truth,
        )

    def checkpoint(self) -> EngineCheckpoint:
        return EngineCheckpoint(
            primed=frozenset(self._primed),
            envelope_band=dict(self._envelope_band),
            envelope_since=dict(self._envelope_since),
        )

    def validate_checkpoint(self, checkpoint: EngineCheckpoint) -> None:
        unknown = sorted(checkpoint.primed - self.loops.keys())

        if unknown:
            raise StateError("primed", f"{unknown} are not loops of this engine")

        for name, held in (
            ("envelope_band", checkpoint.envelope_band),
            ("envelope_since", checkpoint.envelope_since),
        ):
            if held.keys() != self.limits.keys():
                raise StateError(name, "does not cover exactly this engine's limits")

        for (tag, variable), (severity, side) in checkpoint.envelope_band.items():
            if (severity is Severity.NORMAL) != (side is None):
                raise StateError(
                    f"envelope_band.{tag}.{variable}",
                    f"{severity.name} with side {side!r}: a band has a side "
                    f"exactly when it is not NORMAL",
                )

    def restore_checkpoint(self, checkpoint: EngineCheckpoint) -> None:
        self.validate_checkpoint(checkpoint)
        self._primed = set(checkpoint.primed)
        self._envelope_band = dict(checkpoint.envelope_band)
        self._envelope_since = dict(checkpoint.envelope_since)

    def _control(self, dt: float) -> None:
        """Every loop, in order, on the plant as the last snapshot showed it."""
        if not self.loops:
            return

        indicated = self._indicated()

        for tag, binding in self.loops.items():
            loop = binding.loop
            measurement = _reading(indicated, binding.pv_point, f"loop {tag}")

            if tag not in self._primed:
                loop.pid.track(measurement, dt, loop.output)
                self._primed.add(tag)

            output = loop.compute(measurement, dt)

            self.arbiter.demand(binding.out_tag, Source.CONTROLLER, tag, output)

        self.arbiter.apply()

    def _checked_limits(
        self, limits: Mapping[LimitKey, Evaluator],
    ) -> dict[LimitKey, Evaluator]:
        """Refuse a configured limit this engine cannot read.

        A `variable` resolves if the equipment section this engine publishes
        carries it for that tag - the device's own `get_state()` field or one
        of the solved points the engine composes onto its row. Anything else
        names a number nobody publishes, and evaluating nothing in its place
        would leave a configured trip silently dead, so it is raised here,
        every unresolvable entry named at once.
        """
        equipment = self._indicated()["equipment"]
        unresolved = [
            f"{tag}.{variable}"
            for tag, variable in limits
            if variable not in equipment.get(tag, {})
        ]

        if unresolved:
            raise ValueError(
                f"envelope limit(s) {unresolved} name no number the equipment "
                f"section publishes",
            )

        return dict(limits)

    def _update_envelope(self, dt: float) -> None:
        """Advance every limit by dt and record when its band last
        changed. Called once at construction (dt=0.0, to seed the design
        point's classification) and once per step, after couple - never
        from snapshot(), which only reads what this last left."""
        if not self.limits:
            return

        indicated = self._indicated()
        sim_time = self.clock.get_state()["sim_time"]

        for key, evaluator in self.limits.items():
            tag, variable = key
            value = _reading(
                indicated,
                ("equipment", tag, variable),
                f"envelope limit {tag}.{variable}",
            )
            severity = evaluator.evaluate(value, dt)
            self.trackers[key].update(value, severity, dt)

            band = (severity, evaluator.side)
            if band != self._envelope_band.get(key):
                self._envelope_band[key] = band
                self._envelope_since[key] = sim_time

    def _indicated(self) -> dict[str, dict[str, dict[str, JSONValue]]]:
        return indicate(self._truth(), self.instruments.values())

    def _truth(self) -> dict[str, dict[str, dict[str, JSONValue]]]:
        equipment: dict[str, dict[str, JSONValue]] = {}

        for tag, device in self.equipment.items():
            row = dict(device.get_state())
            branch = self._branch_of.get(tag)

            if branch is not None:
                for name, value in (
                    ("flow", branch.flow),
                    ("inlet_pressure", branch.from_node.pressure),
                    ("outlet_pressure", branch.to_node.pressure),
                ):
                    row.setdefault(name, value)

            equipment[tag] = row

        nodes: dict[str, dict[str, JSONValue]] = {}
        streams: dict[str, dict[str, JSONValue]] = {}

        for domain, graph in self.topologies.items():
            state = graph.get_state()
            temperatures = self.transports[domain].temperatures

            nodes.update(
                (node_id, {**row, "temperature": temperatures[node_id]})
                for node_id, row in state["nodes"].items()
            )
            streams.update(state["streams"])

        return {"equipment": equipment, "nodes": nodes, "streams": streams}

    def _single_branches(self) -> dict[str, Branch]:
        """Every device that sits in exactly one branch, with that branch. A
        branch is wired inlet-first (C2), so `from_node` is the inlet and
        `to_node` the outlet whatever the ports are called. A device in no
        branch, or in several, has no single flow or pair of pressures of
        its own and is left out (see the module docstring)."""
        branches: dict[str, list[Branch]] = {}

        for graph in self.topologies.values():
            for branch in graph.branches.values():
                branches.setdefault(branch.device.tag, []).append(branch)

        return {
            tag: found[0] for tag, found in branches.items() if len(found) == 1
        }

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

        for domain in converged:
            self.transports[domain].propagate()

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


def _reading(
    view: Mapping[str, Mapping[str, Mapping[str, JSONValue]]],
    point: Point,
    reader: str,
) -> float:
    """The number `view` publishes at `point`, or ValueError naming `reader`
    if it publishes none - the same refusal `true_reading` gives an
    instrument."""
    section, source, variable = point
    row = view[section].get(source)

    if row is None or variable not in row:
        raise ValueError(
            f"{reader} measures {section}.{source}.{variable}, which the "
            f"plant does not publish",
        )

    value = row[variable]

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{reader} measures {section}.{source}.{variable}, which is "
            f"{value!r}, not a number",
        )

    return float(value)


def _check_boundary_temperatures(
    temperatures: Mapping[str, float],
    topologies: Mapping[str, Topology],
) -> None:
    boundaries = {
        node_id
        for graph in topologies.values()
        for node_id in graph.boundary_nodes
    }

    for node_id, temperature in temperatures.items():
        if node_id not in boundaries:
            raise ValueError(
                f"boundary temperature given for {node_id!r}, which is not a "
                f"boundary node of this plant - only a battery limit supplies "
                f"a temperature, an internal node's is transported",
            )

        if not math.isfinite(temperature) or temperature <= ABSOLUTE_ZERO:
            raise ValueError(
                f"node {node_id!r}: boundary temperature {temperature} °F is "
                f"not a finite temperature above absolute zero",
            )
