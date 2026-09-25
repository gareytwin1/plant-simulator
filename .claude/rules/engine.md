---
paths:
  - "app/engine/**/*.py"
  - "app/equipment/base.py"
  - "app/plant/topology.py"
---

# Engine, solver and branch contract

## `SeededRNG` (T2-2)

`app/engine/rng.py` is a seeded generator instance; a seed is required.
**There is deliberately no module-level RNG and no `set_seed()`.** A
process-wide stream would be shared by every browser session, and each session
owns its own plant. Nothing in `app/` draws random numbers yet. **Open
decision:** which object owns a plant's RNG (session, engine or plant) — it
belongs to the first task that needs randomness. `SeededRNG` also has no state
save/restore, which T12-1 and T14-5 will need.

## Spine vs. satellite in `app/engine/`

The existing spine modules are `clock.py`, `engine.py`, `snapshot.py` and
`sessions.py` — one branch at a time, no satellite edits. `coupling.py` (T5-2)
is a spine module too, despite being new when it landed: the Engine change and
the coupling were one design, not two.

A **new isolated module** may be developed as a satellite when the build-plan
task explicitly owns that new file and does not modify an existing spine
module or interface. That is how `rng.py` (T2-2), `network.py` (T4-2) and
`scheduler.py` (T2-5) landed.

**If a satellite task discovers it must modify a spine module, it stops and
escalates or acquires the spine lock — it does not expand scope silently.**
T4-3 (`snapshot.py`) and T4-4 (`engine.py`) were spine tasks for exactly this
reason.

## Branch characteristic convention (T4-1)

The convention the network solver (T4-2) builds against. Authoritative text is
the `Equipment.characteristic` docstring in `app/equipment/base.py`.

- `characteristic(flow)` is **outlet minus inlet**: positive is a rise
  (machine), negative a drop (valve, pipe). The direction is fixed by the
  ports, never by the sign of the flow.
- Flow is signed; positive runs inlet → outlet. Every real flow is in range,
  including negative — a device may not raise, clamp or return a non-finite
  number outside the range it expects to operate in.
- The curve is **monotone non-increasing in flow everywhere**, so the branch
  equation has exactly one root.
- Write every quadratic term against `signed_square(flow)` (`|q|*q`), never
  `flow ** 2`. The latter is even and reports the same pressure change at −100
  as at +100.
- `Branch.residual(flow)` is `from_node.pressure + characteristic(flow) -
  to_node.pressure`: the branch equation the solver drives to zero. Both
  branch methods take a trial flow and default to the flow the branch is
  carrying.

**Known imprecision in the docstring itself:** it claims a non-increasing curve
"keeps the Jacobian from going singular." For `-K * signed_square(q)` the
derivative is `-2K|q|`, **zero at q = 0**, so the diagonal does vanish at zero
flow — for a valve, a pipe and a machine alike. The curve's real guarantee is
monotone non-increasing with exactly one root, nothing stronger. `base.py` is
frozen, so this has not been corrected in place; do not restate the stronger
claim as fact, and see *A stopped machine keeps a small residual flow* in
[project_state.md](../../.workspace/memory/project_state.md) for the double-root behaviour
this causes.

## `SolverResult` and failure policy

`SolverResult` (`app/engine/network.py`):

- `converged`: `bool` — whether the solver converged.
- `iterations`: `int` — number of Newton-Raphson iterations.
- `residual`: `float` — maximum of all residuals, dimensionless (each scaled by
  its own tolerance); `converged` is exactly `residual <= 1.0`.
- `pressure_residual`: `float` — worst branch-equation residual, psia.
- `flow_residual`: `float` — worst mass-balance residual, in the branch's flow
  unit.
- `failure`: `str | None` — why a non-converged solve stopped: `iteration_cap`
  or `line_search_stall`. Set on exactly the results that did not converge;
  `converged` and `failure` cannot disagree, and a result claiming both is
  refused at construction.

**Failure policy is two-sided, deliberately:**

- **A structural / ill-posed network raises `SolverError`** — no boundary node
  to anchor the pressure field, an unknown appearing in no equation. No
  iteration count or tolerance would have helped, so there is nothing for a
  caller to decide.
- **Numerical non-convergence returns `SolverResult(converged=False)`** with
  `failure` set. The same plant may solve from a different state or at a
  looser tolerance — this is a flag, never a success.
  `raise_if_not_converged()` is the opt-in escalation for a caller that would
  rather have the exception; `describe()` gives one diagnostic line.

`solve_network()` delegates and follows the same policy — there is one, not
two. A failed solve is atomic: unless the solve converges and commits, every
node pressure and branch flow is what it was on entry.

**Snapshot solver section (C4) is unchanged in shape:** `converged`,
`iterations`, `residual`, and nothing else. `pressure_residual`,
`flow_residual` and `failure` stay off C4 on purpose — widening it is a
contract change and its own task. `build_snapshot()` refuses *half* a solver
section (a residual with no `converged` reads as clean to a consumer that
treats the flag as optional) and refuses an unexpected key; `solver={}` still
means "no solve to report."

**Damping is recorded, not retuned.** Heavy under-relaxation (`damping <=
0.25`) can exhaust the default 50-iteration cap on a plant that solves in four
iterations at full step — a slow solve, not a stuck one. The cap is untouched
and not coupled to damping.
