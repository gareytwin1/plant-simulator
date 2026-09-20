# Project State

Concise handoff for a new session. **This file goes stale by design** — it
records what is true right now, not the stable rules. Those are in
[CLAUDE.md](../CLAUDE.md).

Refresh this file whenever a task merges to `main`.

---

**Last refreshed:** 19 September 2026 (**T5-1 merged** as `b8af231`, [PR #23](https://github.com/gareytwin1/plant-simulator/pull/23))
**Current `main`:** `b8af231` — *Merge pull request #23 from gareytwin1/feature/vessel-model*
**Last code merge:** `b8af231` — T5-1, vessel model (PR #23). Previous: `d6a6cfb` T3-4 single-domain reference fixtures (PR #19); `2c08bb8` T3-6 multi-port equipment wiring (PR #20); `11517f6` T3-5 flow-domain declaration (PR #17); `c406ca0` YAML plant loading; previous solver merge `a2a1596` (T4-3). ADR 0001 ([docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md](ADR_0001_FLOW_DOMAIN_SEPARATION.md)) and its Amendment 1 are on `main`
**Full suite on `main`:** **464 passed** (`conda activate plant-simulator && python -m pytest -q`); `python -m mypy` clean

---

## Milestone progress

| Milestone | Status |
|---|---|
| **M0** Baseline Cleanup | **7/7 Complete** |
| **M1** Equipment Model Contract | **5/5 Complete** — Checkpoint A reached |
| **M2** Simulation Engine and Clock | 4/6 (T2-5 startable) |
| **M3** Plant Topology and Streams | **6/6 Complete** |
| **M4** Pressure-Flow Network Solver | 3/5 — T4-1, T4-2, T4-3 Complete; **T4-4 (Checkpoint B) startable** |
| **M5** Inventory and Mass Balance | 1/5 — T5-1 Complete; **T5-3 startable**; T5-2 waits on T4-4 (M5 has 5 tasks: T5-5, the integrated reference plant, was added) |
| M6–M19 | Not started |

Overall: **26 of 97 tasks Complete.**

### Completed and merged to `main`

- **M0** — `T0-1` … `T0-7`: baseline cleanup, pump endpoints, unit convention,
  portable requirements, static typing baseline.
- **M1** — `T1-1` golden harness · `T1-2` Equipment base + Port (C1) ·
  `T1-3` compressor onto C1 · `T1-4` pump onto C1 · `T1-5` equipment registry.
- **M2** — `T2-1` simulation clock · `T2-2` seeded RNG (`SeededRNG`, no global
  stream — see the note below) · `T2-3` engine + snapshot (C4) ·
  `T2-4` session/plant registry.
- **M3** — `T3-1` plant config schema + validator (C3) ·
  `T3-2` node/branch/stream (C2) · `T3-3` plant loader (`app/plant/loader.py`) ·
  `T3-5` flow-domain declaration · `T3-6` multi-port equipment wiring.
- **M5** — `T5-1` vessel model (`app/equipment/vessel.py`): a coupling device, level integrated from caller-written `inlet_flow` / `outlet_flow` (GPM). **Not registered in `DEVICE_TYPES` yet** — register it before T5-5.
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
  the placeholder. The `snapshot.py` spine lock is released and T3-5, which
  T4-4 depended on, is merged, so T4-4 is startable.
- **Reference plant files.** The loader reads `.json`, `.yaml` and `.yml` (YAML
  via `yaml.safe_load`, same C3 validation and `load_plant()` path), and T3-4
  (`d6a6cfb`) added the first plant files: `config/plants/liquid_transfer.yaml`
  and `gas_compression.yaml`, two single-domain fixtures checked against
  closed-form values in `tests/test_reference_plants.py`. The full
  `olefins_lite.yaml` train is T5-5, in M5, and does not exist.
- **Flow-domain enforcement is done for the loader only (T3-5, `11517f6`).**
  `load_plant` rejects a branch across domains, a domain with no boundary and a
  domain that is not one connected piece, and partitions into `Plant.topologies`.
  A `Topology` built by hand is still unchecked, and nothing on the request path
  calls the loader yet.
- **Multi-port wiring in C3 is done for the loader only (T3-6, `2c08bb8`).**
  `equipment` takes `node_in`/`node_out` sugar or `ports` + `paths`; a device with
  `paths: []` is a coupling device, held in `Plant.devices` and in no `Topology`.
  The vessel (T5-1, `b8af231`) is the first device that uses it, and is tested only through the `device_types` override; nothing on the
  request path calls the loader.
- **Single action endpoint (C5).** Routes are still per-equipment.
- **Controllers (PID), envelopes, alarms, trips, scenarios, scoring, historian,
  console.** All specified in the build plan, none implemented.
- `Snapshot`'s `nodes` / `streams` / `controllers` / `envelope` / `alarms`
  sections are deliberately empty.

### Architecture decisions on file

[docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md](ADR_0001_FLOW_DOMAIN_SEPARATION.md) (accepted 19 September 2026) froze these. A later task does
not reverse one without a new ADR.

- **`NetworkSolver` stays single-domain.** One invocation solves exactly one
  compatible flow domain. A mass balance mixing GPM with SCFM is invalid and is
  rejected at load, not in the solver. The solver is not changed.
- **Domain belongs to the hydraulic connection, not the device.** Nodes carry it
  (optional `domain` in C3); branches and topologies derive it; a separator that
  spans two domains through different ports is why no device-wide domain exists.
- **The integrated train is two hydraulic problems** (liquid, gas) coupled through
  the vessel's slow state, never one mixed topology.
- **C1 and C2 need no change** — the conclusion stands, but Amendment 1 replaced
  the reason. Not because "multi-port devices already work in C2": a named
  `ports` map does **not** say which two ports form a `Branch`, and an
  inventory device that holds no branch cannot be in a `Topology` at all,
  because `Topology.devices` is derived from `Topology.branches`. C2 is
  untouched because a coupling device lives on `Plant`, one layer up, and its
  port-to-node binding is made with `Port.connect()`, which is existing C1 API.
- **`characteristic(flow)` is device-wide** (no port or branch argument). Recorded
  as a limitation, deliberately not widened.
- **Vessel pressure is integrated slow state supplying a boundary condition**, not
  a solver result written back into equipment. The route that does this is T5-2's.
- **No boundary pair is sane both cold and running** with only the pump and
  compressor models, so the "plausible steady state from cold" criterion moved
  from T3-4 to T7-1.

Build-plan changes it made: **T3-5** (new, satellite), **T5-5** (new), T3-4
re-scoped, and new dependencies on T3-5 (T3-4, T4-4, T5-1) and on T5-5 (T7-2,
T8-3, T9-2, T11-1, the tasks that edit `olefins_lite.yaml`).

### Amendment 1 (19 September 2026) — the connection model

Raised by the T3-5 implementation session, which stopped before editing anything
and was right to. Three contradictions, all confirmed; the full ruling and the
implementation contract are ADR 0001 **Amendment 1** and **section 12**.

- **A named `ports` map is attachment, and only attachment.** It says which node
  each port attaches to. It does **not** say which two ports form a `Branch`,
  and no heuristic may guess — not declaration order, not inlet×outlet, not
  reading `liquid_*` / `vapor_*`, not a "primary" outlet.
- **Hydraulic paths are a separate, explicit declaration.** C3 gains `paths`:
  each `{from, to}` names two of the device's ports and becomes exactly one
  `Branch`. In the `ports` form `paths` is required, and **`paths: []`** is the
  explicit way to say "coupling device, in no hydraulic solve".
- **A device with no path is in no `Topology`.** It lives in `Plant.devices`,
  bound by `Port.connect()`. That is where a vessel lives. One object, one
  slow-state identity, never duplicated across domains.
- **"A port wired across domains" is withdrawn** — a port attaches to one node
  and so sits in one domain. The invariant is per **branch**: both nodes of a
  path share a domain. A device whose *ports* span domains is valid, and is the
  coupling mechanism.
- **At most one hydraulic path per device, enforced at load**, because
  `characteristic(flow)` is device-wide and a second path would publish the same
  curve into a second branch. The task that widens C1 deletes that check.
- **No `oneOf` in C3.** `app/plant/validate.py` silently ignores `oneOf`,
  `additionalProperties`-as-subschema and `patternProperties`, so the schema
  clause ADR 0001 prescribed would have enforced nothing. The wiring-form
  alternation is a loader check that names the config path; the validator gains
  only `additionalProperties`-as-subschema and `minProperties`.
- **Domains never propagate.** An undeclared node is in `DEFAULT_DOMAIN`
  (`"default"`) and does not inherit a neighbour's, so a branch between a `gas`
  node and an undeclared one is a mismatch and is rejected.
- **T3-5 is split.** T3-5 keeps flow domains; new **T3-6** takes `ports`,
  `paths` and `Plant.devices`. T3-4 and T4-4 depend on T3-5 only, so Checkpoint
  B is unblocked by the smaller task. **T5-1 now depends on T3-6**, not T3-5.
- **T4-4 must build `Engine`'s equipment from `Plant.devices`**, never from
  `Topology.devices`, or a coupling device would silently never be integrated.

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

**T4-4 (Checkpoint B) is in flight and holds the spine lock.**
[PR #25](https://github.com/gareytwin1/plant-simulator/pull/25) from
`refactor/solver-integration`, rebased on `9720aed`, 486 passed and `mypy`
clean — **Ready for Review, not merged.** It wires the solver into
`Engine.step()` and retires both devices' standalone operating point, and it
also holds `app/main.py` and `app/engine/sessions.py`, which the live routes
forced into scope. **Freeze other merges until it lands**, and release both
locks the moment it does. T7-1 only adds a new file, so it is safe to develop
alongside and merge after.

T5-1 merged as `b8af231` ([PR #23](https://github.com/gareytwin1/plant-simulator/pull/23)): `Vessel` and `tests/test_vessel.py`, nothing else. T3-4 merged
as `d6a6cfb` ([PR #19](https://github.com/gareytwin1/plant-simulator/pull/19)): two
single-domain reference fixtures and their tests, nothing under `app/`.

T3-6 merged as `2c08bb8` ([PR #20](https://github.com/gareytwin1/plant-simulator/pull/20)).
It implements ADR 0001 section 12.1-12.4, 12.8 and 12.9: `ports` (attachment only)
and `paths` (one `Branch` each) in C3, one-form-only enforced in the loader, more
than one path rejected (A6), `Plant.devices` (config-ordered, every device, coupling
devices included), the dangling-port check moved onto it, and a form-preserving
`to_config()`. `validate.py` gained `minProperties` and `additionalProperties` as a
subschema. C1, C2, `NetworkSolver` and `Engine` are untouched. **T4-4 must build
the Engine's equipment from `Plant.devices`, never `Topology.devices`**, or a
coupling device would never be integrated.

T3-5 merged as `11517f6` ([PR #17](https://github.com/gareytwin1/plant-simulator/pull/17)): domains only, `Plant.topologies`, flat `Plant.nodes`.

T4-3 merged as `a2a1596` ([PR #14](https://github.com/gareytwin1/plant-simulator/pull/14)) and released `app/engine/snapshot.py`.

Merged local branches can be deleted at any time.

## Next integration checkpoint

**Checkpoint B — M4, network solver merged.** This is where the plant stops
being a collection of independent gauges and becomes connected. It is the single
largest risk in the plan: the build plan calls out solver non-convergence on a
reasonable-looking topology as one of three most-likely slip points.

## Handoff: the next agents

**Everything below is verified against `main`** (464 tests, `mypy` clean). Read
[CLAUDE.md](../CLAUDE.md) first, then the build plan entry for your task. Model
guidance is the **Agent model guidance** section of CLAUDE.md; the column below
is the build plan's own assignment.

### Critical path to Checkpoint B

**T4-2, T4-3, T3-5 (Complete)** ->
**T4-4 (wire into engine, spine, freezes other merges)** -> T4-5
(cause-and-effect suite). T3-5 has merged, so T4-4 is the next step on this path.

`NetworkSolver` is in `app/engine/network.py`, fully tested, with its failure
policy and diagnostics settled by T4-3 — but it is still not called from
`Engine.step()` or the Flask request path.

**T4-4 is startable (Opus, `refactor/solver-integration`).** No spine lock is
held. It wires the Engine against `Plant.topology`, the single-domain
convenience T3-5 keeps; one solver per domain arrives with T5-2. **Build the
Engine's equipment collection from `Plant.devices`, never from
`Topology.devices`** — a coupling device such as a vessel holds no branch and
would silently never be integrated (ADR 0001 section 12.6). It wires the solver into the engine and retires the legacy device
`step()` / `_calculate_operating_point()` / `*_boundary_pressure` path for both
devices at once. Build the snapshot's solver section with
`snapshot.solver_status(result)` rather than hand-rolling it — see "Solver and
snapshot state" below for the contract it presents. Expect golden traces to
move, and justify any delta in the PR rather than regenerating it away.

**T3-4 is Complete (`d6a6cfb`).** ADR 0001 settled the design and re-scoped the task. Verified against
`main` on 19 September:

- The **YAML half is merged** (PR #15, `c406ca0`) — `.json`/`.yaml`/`.yml` in
  `load_plant_file`, `PyYAML` + `types-PyYAML` pinned, `tests/test_plant_yaml.py`.
- The **original full train cannot be expressed or solved honestly yet**: there
  is no vessel model (`DEVICE_TYPES` maps only `pump` and `compressor`), C3
  cannot wire a multi-port device, and a mixed liquid/gas config solves to a
  meaningless answer (a pump and compressor in series converge to the same number
  as GPM and as SCFM). That train is now **T5-5**, after the vessel and the
  inventory coupling.
- **T3-4 delivered two single-domain fixtures**, both re-run on `main`:
  `liquid_transfer.yaml` (booster into transfer pump, 50 to 180 psia, solves to
  816.50 GPM with the internal node at 115.00 psia) and `gas_compression.yaml`
  (two-stage compression, 60 to 480 psia, solves to 70.71 SCFM with the internal
  node at 270.00 psia). Both answers are closed-form and hand-checkable.
- **Do not assert a cold-start operating point in those fixtures**
  (`tests/test_reference_plants.py` deliberately does not). With no check
  valve, line resistance or control valve, a stopped machine backflows if the
  boundaries are sized for running and a started one runs away if they are sized
  for cold. That criterion moved to T7-1.

### Open questions carried from T3-3

Question 1 still has no task and needs an owner (an Opus decision). Question 2
is retired: **T3-6** (merged) carried the C3 named-port wiring (moved from T3-5 by ADR
0001 Amendment 1).

1. **`Equipment.reset()` drops design values.** The loader applies a config's
   `design` by setting attributes after construction, but `reset()` restores the
   state captured at the end of `__init__`. A reset device returns to class
   defaults, not the configured design. Fixing it needs a design/configure hook
   on C1 — a spine change — before anything relies on `reset()` for a loaded
   plant. Also relevant to T12-1 (save/restore).
2. **Resolved by T3-6 (`2c08bb8`).** C3 now wires a multi-port device with
   `ports` and `paths`; T5-1 uses it and must not invent its own.

### Open question carried from T2-2

**Who owns a plant's RNG** (session, engine or plant)? Undecided on purpose — see
"Seeded RNG" above. `SeededRNG` also has no state save/restore, which T12-1
(snapshot save and restore) and T14-5 (deterministic replay) will need.

### Startable now (18 tasks)

All dependencies are Complete. Two of them (T2-5, T12-1) add *new* isolated
modules under `app/engine/`, which is satellite work under the **`app/engine/`
rule** in CLAUDE.md.

**Startable:** T5-3 (gas pressure, same file as T5-1) takes no spine lock. T4-4 is Checkpoint B and a spine task that
freezes other merges.

| Task | Name | Model | Branch |
|---|---|---|---|
| **T4-4** | Wire the solver into the engine (Checkpoint B) | Opus | `refactor/solver-integration` |
| **T5-3** | Gas-phase pressure accumulation | Sonnet | `feature/gas-inventory` |
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
| **T18-2** | CI pipeline | Haiku | `chore/ci-pipeline` |
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

**The build plan's reference-plant acceptance test can now run.** "Residual falls
below tolerance on the reference plant" has files to run against: T3-4's two
single-domain fixtures converge under `NetworkSolver` unchanged (5 and 4
iterations). T4-3's own convergence tests still use a hand-built plant
(`tests/test_solver_diagnostics.py`). The integrated train (T5-5) comes later.

**Process-domain caveat (enforced in the loader as of T3-5)**:
A `Topology` must be a single flow domain (gas or liquid, not both), and
`NetworkSolver` solves exactly one. The loader now enforces this (T3-5, `11517f6`): it partitions
nodes by declared `domain` into one `Topology` per domain and rejects a branch
that crosses domains, at load, naming the config path. C2 and the solver still
carry no domain metadata, so a `Topology` assembled by hand is unchecked. The
solver is untouched. T4-4 wires the engine against `Plant.topology`, which raises
on a multi-domain plant; one solver per domain arrives with T5-2. (T5-1's
dependency moved to T3-6, the multi-port wiring half of the original T3-5, when
Amendment 1 split the task.)

## Test suite composition (464 tests on `main`)

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
| `test_solver_diagnostics.py` | 14 | `test_plant_yaml.py` | 17 |
| `test_plant_domains.py` | 23 | `test_plant_ports.py` | 32 |
| `test_reference_plants.py` | 20 | `test_vessel.py` | 16 |

`test_equipment_contract.py` and `test_registry.py` discover device classes
dynamically, so a new `Equipment` subclass is swept into the contract tests
automatically — adding one raises the total by more than the tests you wrote.
(T4-2 added `test_network_solver.py` with 36 tests, taking the total to 336;
T4-3 added `test_solver_diagnostics.py` with 14 and 5 to `test_snapshot.py`,
taking it to 355; `test_plant_yaml.py` added 17, taking it to 372; `test_plant_domains.py` added 23, taking it to 395; `test_plant_ports.py` added 32, `test_reference_plants.py` added 20, `test_vessel.py` added 16, and `main` runs 464. The vessel did not raise the sweep counts: the contract tests import only the compressor and pump modules, so `vessel` is not yet swept into them.)
