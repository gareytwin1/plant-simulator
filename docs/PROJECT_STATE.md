# Project State

Concise handoff for a new session. **This file goes stale by design** — it
records what is true right now, not the stable rules. Those are in
[CLAUDE.md](../CLAUDE.md).

Refresh this file whenever a task merges to `main`.

---

**Last refreshed:** 19 September 2026 (post-merge refresh after T4-3)
**Current `main`:** `a2a1596f48554733a0e295ec6105ac3bfc1adba9` — *Merge pull request #14 (T4-3: solver diagnostics)*
**Last code merge:** `a2a1596` — T4-3, solver diagnostics and failure handling
**Full suite on `main`:** **355 passed** (`conda activate plant-simulator && python -m pytest -q`); `python -m mypy` clean

---

## Milestone progress

| Milestone | Status |
|---|---|
| **M0** Baseline Cleanup | **7/7 Complete** |
| **M1** Equipment Model Contract | **5/5 Complete** — Checkpoint A reached |
| **M2** Simulation Engine and Clock | 4/6 (T2-5 startable) |
| **M3** Plant Topology and Streams | 3/4 (T3-4 Blocked) |
| **M4** Pressure-Flow Network Solver | 3/5 — T4-1, T4-2, T4-3 Complete; **T4-4 startable (Checkpoint B)** |
| M5–M19 | Not started |

Overall: **22 of 94 tasks Complete.**

### Completed and merged to `main`

- **M0** — `T0-1` … `T0-7`: baseline cleanup, pump endpoints, unit convention,
  portable requirements, static typing baseline.
- **M1** — `T1-1` golden harness · `T1-2` Equipment base + Port (C1) ·
  `T1-3` compressor onto C1 · `T1-4` pump onto C1 · `T1-5` equipment registry.
- **M2** — `T2-1` simulation clock · `T2-2` seeded RNG (`SeededRNG`, no global
  stream — see the note below) · `T2-3` engine + snapshot (C4) ·
  `T2-4` session/plant registry.
- **M3** — `T3-1` plant config schema + validator (C3) ·
  `T3-2` node/branch/stream (C2) · `T3-3` plant loader (`app/plant/loader.py`).
- **M4** — `T4-1` branch characteristic interface (sign convention,
  `signed_square`, `Branch.characteristic()` / `Branch.residual()`) · `T4-2`
  Newton-Raphson network solver (`NetworkSolver`, standalone, not yet wired
  into the engine) · `T4-3` solver diagnostics and failure handling
  (`SolverResult.failure`, `snapshot.solver_status()`; C4's shape unchanged).

### Seeded RNG: what shipped and what did not (T2-2)

`SeededRNG` (`app/engine/rng.py`) is a seeded generator instance; a seed is
required. `tests/test_random_source_guard.py` fails the build if any other module
in `app/` imports `random`. Nothing in `app/` draws random numbers yet.

**There is deliberately no module-level RNG and no `set_seed()`.** A process-wide
stream would be shared by every browser session, and each session owns its own
plant. **Open decision:** which object owns a plant's RNG (session, engine or
plant). It belongs to the first task that needs randomness. (T2-2 was once
recorded as Complete before it was merged; that was corrected, and the task is
now genuinely merged.)

## Architecture status

### What exists

| Component | Where | Notes |
|---|---|---|
| Equipment contract (C1) + `Port` | `app/equipment/base.py` | Frozen at Checkpoint A; `characteristic()` sign convention and `signed_square` added at T4-1 |
| `GasCompressor` (`K-101`) | `app/equipment/compressor.py` | On C1 |
| `CentrifugalPump` (`P-101`) | `app/equipment/pump.py` | On C1 as of T1-4 |
| `EquipmentRegistry` | `app/equipment/registry.py` | Tag → device; rejects duplicate tags |
| `SimulationClock` | `app/engine/clock.py` | Sim time, speed, pause |
| `Engine` | `app/engine/engine.py` | Integrates all devices, publishes snapshot |
| `Snapshot` (C4) | `app/engine/snapshot.py` | Immutable; shape frozen |
| `SessionRegistry` / `Session` | `app/engine/sessions.py` | Per-browser plant isolation |
| `NetworkSolver` | `app/engine/network.py` | Newton-Raphson solver for plant-wide flows and pressures; standalone, not yet on the request path. Diagnostics and failure policy as of T4-3 |
| Topology (C2) | `app/plant/topology.py` | `Node`, `Branch`, `Stream`, `Topology`; `Branch.characteristic()` / `residual()` as of T4-1 |
| Plant config schema (C3) + validator | `config/schema/plant.schema.json`, `app/plant/validate.py` | |
| Golden regression harness | `tests/golden_regression.py`, `tests/fixtures/golden/` | |
| Flask app | `app/main.py` | Per-equipment routes, session-scoped |

### What intentionally does not exist yet

- **The network solver is not wired into the engine (T4-4).** `NetworkSolver`
  (T4-2) is complete and tested but standalone — nothing calls it from
  `Engine.step()` or the Flask request path. Device-owned operating-point
  solving and interim `*_boundary_pressure` attributes remain until T4-4.
  T4-3 made a solve's diagnostics *representable* in a snapshot; it did not
  make anything produce one, so the live snapshot's `solver` section is still
  the placeholder. **T4-4 is now startable** — the `snapshot.py` spine lock is
  released.
- **A reference plant file (T3-4).** The loader exists, but no
  `config/plants/*.yaml` file does. T3-4 is **Blocked** pending a design
  decision on flow-domain metadata: both the config schema (C3) and the solver
  (T4-2) lack metadata to track liquid/gas boundaries, and the design of how
  topology will enforce single hydraulic domain is undecided. T5-1 (vessel)
  will also need this decision.
- **Single action endpoint (C5).** Routes are still per-equipment.
- **Controllers (PID), envelopes, alarms, trips, scenarios, scoring, historian,
  console.** All specified in the build plan, none implemented.
- `Snapshot`'s `nodes` / `streams` / `controllers` / `envelope` / `alarms`
  sections are deliberately empty.

## Branch characteristic convention (T4-1)

The convention T4-2 builds against. Authoritative text is the
`Equipment.characteristic` docstring in `app/equipment/base.py`.

- `characteristic(flow)` is **outlet minus inlet**: positive is a rise (machine),
  negative a drop (valve, pipe). The direction is fixed by the ports, never by
  the sign of the flow.
- Flow is signed; positive runs inlet -> outlet. Every real flow is in range,
  including negative — a device may not raise, clamp or return a non-finite
  number outside the range it expects to operate in.
- The curve is **non-increasing in flow everywhere**, so the branch equation has
  exactly one root and the Jacobian diagonal does not vanish.
- Write every quadratic term against `signed_square(flow)` (`|q|*q`), never
  `flow ** 2`. The latter is even and reports the same pressure change at -100
  as at +100.
- `Branch.residual(flow)` is `from_node.pressure + characteristic(flow) -
  to_node.pressure`: the branch equation T4-2 drives to zero. Both branch
  methods take a trial flow and default to the flow the branch is carrying.

## Static typing

**Permanent policy:** type hints are **required** in new and modified production
Python under `app/`. The rules — what must be typed, what must not be annotated
for its own sake, and when `Any` is acceptable — are in
[CLAUDE.md](../CLAUDE.md). This replaced the project's earlier "no type hints"
rule.

**Current migration state:** all of `app/` is inside the checked scope and
`python -m mypy` is clean. `mypy` is pinned in `requirements-dev.txt` and
configured in `pyproject.toml`; it checks `app/` only.

| Module | State |
|---|---|
| `app/statetypes.py` | New. `JSONValue` / `StateRow` — the JSON-safe row every `get_state()` returns |
| `app/equipment/base.py`, `registry.py`, `compressor.py`, `pump.py` | Typed |
| `app/engine/clock.py`, `engine.py`, `snapshot.py`, `sessions.py` | Typed |
| `app/plant/topology.py`, `validate.py` | Typed |
| `app/main.py` | Typed (`flask.typing.ResponseReturnValue`) |
| `app/config.py` | Unannotated by design — its constants infer exactly |
| `tests/` | Outside the checked scope; tests stay lightly typed |

`app/plant/validate.py` is the only deliberate `Any`: it walks decoded JSON
whose shape is what it exists to discover.

Strictness today is "every def in `app/` is annotated, no implicit `Optional`,
no bare generics". Tightening further (`disallow_untyped_calls`,
`warn_unreachable`, `strict`) is a later decision, not a silent one.

## Known temporary compatibility paths

**Do not "fix" these as a side effect of an unrelated task.** Each is deliberate
and has a task that retires it.

1. **Legacy device `step()` + standalone operating-point solve.**
   Both `GasCompressor` and `CentrifugalPump` keep `step()`, which runs
   `integrate(dt)` then `_calculate_operating_point()`. The live Flask routes
   (`/api/step`, `/api/pump/step`) call this path, so removing it breaks the app.
   *Retired by:* **T4-4** (wires the solver into the engine and removes
   `_calculate_operating_point`; T4-2 only writes the solver).

2. **Device-owned `upstream_boundary_pressure` / `downstream_boundary_pressure`.**
   Interim attributes on both devices, feeding only the standalone solve. They
   are not node pressures and deliberately avoid the C1 forbidden-attribute
   names, but in the target architecture the topology owns these.
   *Retired by:* **T4-4**.

3. **`Engine` does not consult `device.simulation_speed`.**
   The clock is the sole speed authority for anything driven through the Engine.
   A device's own `simulation_speed` affects only its legacy `step()`. Pinned by
   `test_compressor_simulation_speed_is_not_consulted_by_the_engine` so the two
   cannot silently start disagreeing.

4. **`Engine.step()` computes no flow or pressure.**
   It calls `integrate()` only. Stepping a device through the Engine does not
   change its flow/pressure. This is correct until T4-4, and the two engine
   golden tests assert slow-state fields only for exactly this reason.

5. **Legacy `*_pressure_rise` is no longer clamped at zero.**
   T4-1 removed the `max(..., 0.0)` from both device curves, so on the legacy
   path `pump_pressure_rise` / `compressor_pressure_rise` can read negative when
   a machine is overrun. One golden value moved because of it: pump
   `supply_pressure_change` step 1, `0.0` -> `-1.4` (1 of 3528), edited by hand
   and equal to the `spread` on the same row. Separately, the reported rise
   still disagrees with `spread` when the legacy solve dead-heads or clips at
   `max_flow` — an artifact of the standalone solve, retired with it.
   *Retired by:* **T4-4**.

## Known technical debt (recorded, not scheduled)

- **`app/init.py` is a misnamed empty file** — it is not `__init__.py`. `app`
  resolves as a namespace package so imports work anyway. Harmless; has never
  had a task.
- **`static/style.css` is 15 lines** and defines almost none of the classes the
  templates use (`.panel`, `.status-grid`, `.control-group`, …). The pages are
  largely unstyled. Intentional — the console is rebuilt wholesale at M16.
- **Contract-test discovery is import-order dependent.** `REGISTERED` in
  `tests/test_equipment_contract.py` is computed at module import time, so an
  `Equipment` subclass defined in a test module imported *later* silently escapes
  the parametrized contract tests. Flagged at T1-2; `tests/test_topology.py`'s
  `Device` double is a live instance of it. Passes either way today.
- **History carries T1-5 twice** (`d41949a` + `5ea07cb`, identical content) from
  a branch race. Already pushed; deliberately not rewritten.
- **`package.json` / `node_modules/`** exist only for a TypeScript dev
  dependency and are not part of the app.

## Active branches and PRs

None. No open PRs and no feature branch in flight; **no spine lock is held.**
T4-3 merged as `a2a1596` ([PR #14](https://github.com/gareytwin1/plant-simulator/pull/14)) and released `app/engine/snapshot.py`.

**`feature/reference-plant` (T3-4) is parked, not merged.** It carries three
commits of YAML config loading (`loader.py`, `PyYAML` pinned, 191 lines of
tests) but no `config/plants/olefins_lite.yaml`, and it predates T4-2 so it
needs a rebase. See the T3-4 note below before touching it.

Merged local branches can be deleted at any time.

## Next integration checkpoint

**Checkpoint B — M4, network solver merged.** This is where the plant stops
being a collection of independent gauges and becomes connected. It is the single
largest risk in the plan: the build plan calls out solver non-convergence on a
reasonable-looking topology as one of three most-likely slip points.

## Handoff: the next agents

**Everything below is verified against `main`** (355 tests, `mypy` clean). Read
[CLAUDE.md](../CLAUDE.md) first, then the build plan entry for your task. Model
guidance is the **Agent model guidance** section of CLAUDE.md; the column below
is the build plan's own assignment.

### Critical path to Checkpoint B

**T4-2, T4-3 (Complete)** -> **T4-4 (wire into engine, spine, freezes other
merges — startable now)** -> T4-5 (cause-and-effect suite).

`NetworkSolver` is in `app/engine/network.py`, fully tested, with its failure
policy and diagnostics settled by T4-3 — but it is still not called from
`Engine.step()` or the Flask request path.

**T4-4 is startable now (Opus, `refactor/solver-integration`).** No spine lock
is held. It wires the solver into the engine and retires the legacy device
`step()` / `_calculate_operating_point()` / `*_boundary_pressure` path for both
devices at once. Build the snapshot's solver section with
`snapshot.solver_status(result)` rather than hand-rolling it — see "Solver and
snapshot state" below for the contract it presents. Expect golden traces to
move, and justify any delta in the PR rather than regenerating it away.

**T3-4 (`feature/reference-plant`) is Blocked, and its branch is parked
part-finished.** Verified against `main` on 19 September:

- The **YAML half is done on the branch** — `.json`/`.yaml`/`.yml` in
  `load_plant_file`, `PyYAML` + `types-PyYAML` pinned, 191 lines of tests. It
  touches only `loader.py`, so it is landable on its own once rebased (the
  branch predates T4-2).
- The **reference plant file itself cannot be written yet**, for two reasons
  that are hard load-time rejections rather than judgement calls. The plan's
  train needs a **separator, and there is no vessel model**: `DEVICE_TYPES` in
  `app/plant/loader.py` maps only `pump` and `compressor`, so a `vessel` config
  is rejected with *"has no device model yet"*. The vessel is **T5-1, in M5** —
  so T3-4 as specified sits in M3 but depends on a task two milestones later.
  And **C3 cannot wire a separator even once one exists**: `equipment` requires
  exactly `node_in`/`node_out` with `additionalProperties: false`, so a vessel
  with both a liquid and a vapour outlet is inexpressible (the T3-1-owned
  multi-port gap).
- The **flow-domain risk is real, not theoretical.** A pump-plus-compressor
  config loads and solves happily today, converging to −410.773 GPM through the
  pump and −410.773 SCFM through the compressor as though they were the same
  quantity. Since T3-4's file "becomes the fixture for every later test", that
  is the number that would get baked in.

The likely shape of the fix: land the YAML work as its own task, re-scope T3-4
to the single-domain fixtures it can actually deliver (one liquid, one gas —
which is what coupling through vessel inventory implies anyway), and move the
full `olefins_lite.yaml` train after M5. **That is a build-plan change and an
Opus decision; it has not been made.**

### Open questions carried from T3-3

Neither has a task yet; both need an owner (an Opus decision each).

1. **`Equipment.reset()` drops design values.** The loader applies a config's
   `design` by setting attributes after construction, but `reset()` restores the
   state captured at the end of `__init__`. A reset device returns to class
   defaults, not the configured design. Fixing it needs a design/configure hook
   on C1 — a spine change — before anything relies on `reset()` for a loaded
   plant. Also relevant to T12-1 (save/restore).
2. **C3 cannot wire a multi-port device.** The schema has only
   `node_in`/`node_out`, so a device with more than one inlet or outlet (a
   vessel with a vent) is rejected at load. It needs `from_port`/`to_port` in
   the schema (owned by T3-1). **T5-1 (vessel) is likely the first task to hit it.**
   A Sonnet agent on T5-1 should stop and escalate rather than invent the schema
   change.

### Open question carried from T2-2

**Who owns a plant's RNG** (session, engine or plant)? Undecided on purpose — see
"Seeded RNG" above. `SeededRNG` also has no state save/restore, which T12-1
(snapshot save and restore) and T14-5 (deterministic replay) will need.

### Startable now (17 tasks)

All dependencies are Complete. **T4-4 is the exception to the satellite rule
below**: it edits existing spine files and is Checkpoint B, so it takes the
spine lock and other merges freeze while it lands. Two of the others (T2-5,
T12-1) add *new* isolated modules under `app/engine/`, which is satellite work
under the **`app/engine/` rule** in CLAUDE.md.

**T3-4 (reference plant) is Blocked** on flow-domain metadata design (see above).

| Task | Name | Model | Branch |
|---|---|---|---|
| **T4-4** | Wire the solver into the engine — **Checkpoint B, spine, freezes other merges** | Opus | `refactor/solver-integration` |
| **T5-1** | Vessel model (likely hits the C3 multi-port gap; also blocked on flow-domain design) | Sonnet | `feature/vessel-model` |
| **T6-1** | Stream enthalpy and mixing | Opus | `feature/stream-enthalpy` |
| **T7-1** | Control valve model | Sonnet | `feature/control-valve` |
| **T13-1** | Malfunction model and registry | Opus | `feature/malfunction-model` |
| **T2-5** | Background scheduler | Sonnet | `feature/engine-scheduler` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T8-1** | PID block | Sonnet | `feature/pid-block` |
| **T9-1** | Envelope evaluator | Sonnet | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | Sonnet | `feature/alarm-state-machine` |
| **T14-1** | Scenario file schema | Sonnet | `feature/scenario-schema` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T18-1** | Container and WSGI serving | Sonnet | `chore/container-and-ci` |
| **T13-5** | Physics isolation guard | Haiku | `test/import-direction-guard` |
| **T15-4** | Score persistence | Haiku | `feature/score-store` |
| **T17-1** | Ring-buffer historian | Haiku | `feature/historian` |

Working rules for every one of them: one worktree per task branched from
`origin/main`; full suite **and** `python -m mypy` green before review; small
commits led by the task ID; **Ready for Review** at PR time and **Complete** only
with the merge SHA. `tests/test_random_source_guard.py` shows the AST-scan style
a layering guard like T13-5 can follow.

## Solver and snapshot state

**`SolverResult` (`app/engine/network.SolverResult`)**:
- `converged`: bool — whether the solver converged
- `iterations`: int — number of Newton-Raphson iterations
- `residual`: float — maximum of all residuals, dimensionless (each scaled by
  its own tolerance); converged is exactly `residual <= 1.0`
- `pressure_residual`: float — worst branch-equation residual, psia
- `flow_residual`: float — worst mass-balance residual, in the branch's flow unit
- `failure`: `str | None` — why a non-converged solve stopped, `iteration_cap`
  or `line_search_stall`. Set on exactly the results that did not converge:
  `converged` and `failure` cannot disagree, and a result that tried to claim
  both is refused at construction.

**Failure policy (T4-3).** Two-sided and deliberate:

- **Structural / ill-posed network raises `SolverError`** — no boundary node to
  anchor the pressure field, an unknown appearing in no equation. No iteration
  count or tolerance would have helped, so there is nothing for a caller to
  decide.
- **Numerical non-convergence returns `SolverResult(converged=False)`** with
  `failure` set. The cap was reached or the line search stalled; the same plant
  may solve from a different state or at a looser tolerance. That is a flag,
  never a success. `raise_if_not_converged()` is the opt-in escalation for a
  caller that would rather have the exception, and `describe()` gives one
  diagnostic line.

`solve_network()` delegates and therefore follows exactly the same policy —
there is one, not two. A failed solve is atomic either way: unless the solve
converges and commits, every node pressure and branch flow is what it was on
entry, so a caller that ignores the flag reads the pre-solve plant rather than
an unchecked iterate.

**Snapshot solver section (C4) is unchanged in shape**: `converged`,
`iterations`, `residual`, and nothing else. `app.engine.snapshot.solver_status(result)`
is the projection a caller uses, reading `converged` straight off the result so
a failed solve cannot arrive looking like one that landed. `pressure_residual`,
`flow_residual` and `failure` stay off C4 on purpose — widening it is a contract
change and its own task. `build_snapshot()` refuses *half* a solver section (a
residual with no `converged` reads as a clean solve to any consumer that treats
the flag as optional) and refuses an unexpected key; `solver={}` still means "no
solve to report", and the default placeholder is unchanged because nothing on
the request path solves yet (T4-4).

**Damping, recorded not retuned.** Heavy under-relaxation (`damping <= 0.25`)
can exhaust the default 50-iteration cap on a plant that solves in four
iterations at full step. T4-3 made that legible rather than changing it: the
failure reads `iteration_cap` at a residual a few times tolerance — a slow
solve, not a stuck one. The cap is untouched and is not coupled to damping.

**The build plan's reference-plant acceptance test is deferred, not satisfied.**
"Residual falls below tolerance on the reference plant" needs
`config/plants/olefins_lite.yaml`, which does not exist because **T3-4 is
Blocked**. T4-3 proves convergence on a hand-built single-domain series plant
instead (`tests/test_solver_diagnostics.py`). Re-run the criterion against a
reference plant once one lands.

**Process-domain caveat (recorded, unenforced; blocker for T3-4 and T5-1)**:
A `Topology` must be a single hydraulic domain (gas or liquid, not both). Nothing
enforces this — C2/C3/`NetworkSolver` carry no flow-domain metadata. **T3-4 (reference
plant) is Blocked** pending the design of how config/topology will track and
enforce flow boundaries. **T5-1 (vessel)** will also depend on this decision,
since a vessel can have both liquid and gas phases. Hold both until flow-domain
architecture is settled.

## Test suite composition (355 tests on `main`)

| File | Tests | File | Tests |
|---|---|---|---|
| `test_equipment_contract.py` | 58 | `test_registry.py` | 10 |
| `test_topology.py` | 47 | `test_pump.py` | 8 |
| `test_plant_loader.py` | 27 | `test_pump_api.py` | 8 |
| `test_compressor.py` | 21 | `test_api.py` | 7 |
| `test_golden_regression.py` | 17 | `test_sessions.py` | 7 |
| `test_plant_config_validation.py` | 13 | `test_session_isolation.py` | 6 |
| `test_engine.py` | 13 | `test_clock.py` | 13 |
| `test_snapshot.py` | 16 | `test_random_source_guard.py` | 21 |
| `test_rng.py` | 10 | `test_network_solver.py` | 36 |
| `test_solver_diagnostics.py` | 14 | | |

`test_equipment_contract.py` and `test_registry.py` discover device classes
dynamically, so a new `Equipment` subclass is swept into the contract tests
automatically — adding one raises the total by more than the tests you wrote.
(T4-2 added `test_network_solver.py` with 36 tests, taking the total to 336;
T4-3 added `test_solver_diagnostics.py` with 14 and 5 to `test_snapshot.py`,
taking it to 355.)
