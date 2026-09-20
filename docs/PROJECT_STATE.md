# Project State

Concise handoff for a new session. **This file goes stale by design** — it
records what is true right now, not the stable rules. Those are in
[CLAUDE.md](../CLAUDE.md).

Refresh this file whenever a task merges to `main`.

---

**Last refreshed:** 20 September 2026 (**T4-5 merged** as `e261134`, [PR #29](https://github.com/gareytwin1/plant-simulator/pull/29) — **M4 complete**)
**Current `main`:** `e261134` — *Merge pull request #29 from gareytwin1/test/cause-and-effect*
**Last code merge:** `e261134` — T4-5, the cause-and-effect assertion suite (PR #29, test-only). Previous: `154385c` T5-2, level to hydraulics coupling and the multi-domain Engine (PR #27). Previous: `0efa5be` T4-4, the solver wired into the engine (PR #25). Previous: `b8af231` T5-1 vessel model (PR #23); `d6a6cfb` T3-4 single-domain reference fixtures (PR #19); `2c08bb8` T3-6 multi-port equipment wiring (PR #20); `11517f6` T3-5 flow-domain declaration (PR #17); `c406ca0` YAML plant loading; previous solver merge `a2a1596` (T4-3). ADR 0001 ([docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md](ADR_0001_FLOW_DOMAIN_SEPARATION.md)) and its Amendment 1 are on `main`
**Full suite on `main`:** **567 passed** (`conda activate plant-simulator && python -m pytest -q`); `python -m mypy` clean

---

## Milestone progress

| Milestone | Status |
|---|---|
| **M0** Baseline Cleanup | **7/7 Complete** |
| **M1** Equipment Model Contract | **5/5 Complete** — Checkpoint A reached |
| **M2** Simulation Engine and Clock | 4/6 (T2-5 startable) |
| **M3** Plant Topology and Streams | **6/6 Complete** |
| **M4** Pressure-Flow Network Solver | **5/5 Complete** — Checkpoint B reached; T4-5 was re-scoped before implementation, see [The T4-5 re-scope](#the-t4-5-re-scope) |
| **M5** Inventory and Mass Balance | 2/5 — T5-1, T5-2 Complete; **T5-3 and T5-4 startable**; the engine and topology spine locks are released (M5 has 5 tasks: T5-5, the integrated reference plant, was added) |
| M6–M19 | Not started |

Overall: **29 of 97 tasks Complete.**

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
- **M5** — `T5-1` vessel model (`app/equipment/vessel.py`): a coupling device,
  level integrated from `inlet_flow` / `outlet_flow` (GPM) · `T5-2` level to
  hydraulics coupling (`154385c`): `head_at_full * level` added to the
  `configured_pressure` of the boundary the vessel's OUTLET port attaches to,
  flows read back as net signed flow at each attachment, one solver per flow
  domain, and `"vessel"` registered in `DEVICE_TYPES`.
- **M4** — `T4-1` branch characteristic interface (sign convention,
  `signed_square`, `Branch.characteristic()` / `Branch.residual()`) · `T4-2`
  Newton-Raphson network solver (`NetworkSolver`) · `T4-3` solver diagnostics
  and failure handling (`SolverResult.failure`, `snapshot.solver_status()`; C4's
  shape unchanged) · `T4-4` solver wired into the engine (`0efa5be`): `Engine.step()`
  integrates, solves and publishes real `solver` / `nodes` / `streams`;
  `Engine.from_plant()` builds equipment from `Plant.devices`; the devices'
  standalone operating point is retired · `T4-5` cause-and-effect assertion
  suite (`e261134`, `tests/test_cause_effect.py`, 26 tests, test-only), re-scoped
  before implementation — two criteria moved to T7-1.

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
| `Engine` | `app/engine/engine.py` | Integrates all devices, couples inventory, solves every flow domain, publishes snapshot. `Engine.from_plant()` wires one solver per entry in `Plant.topologies` (T5-2) |
| Inventory coupling | `app/engine/coupling.py` | T5-2. Vessel head down onto a boundary, net signed flow back up. `FLOW_UNITS` decides which ports are GPM; **a new branch device must be added to it** |
| `Snapshot` (C4) | `app/engine/snapshot.py` | Immutable; shape frozen |
| `SessionRegistry` / `Session` | `app/engine/sessions.py` | Per-browser plant isolation; one `Engine` per page over a loader-built single-device plant |
| `NetworkSolver` | `app/engine/network.py` | Newton-Raphson solver for plant-wide flows and pressures; called by `Engine.step()` since T4-4. Diagnostics and failure policy as of T4-3 |
| Topology (C2) | `app/plant/topology.py` | `Node`, `Branch`, `Stream`, `Topology`; `Branch.characteristic()` / `residual()` as of T4-1 |
| Plant config schema (C3) + validator | `config/schema/plant.schema.json`, `app/plant/validate.py` | |
| Golden regression harness | `tests/golden_regression.py`, `tests/fixtures/golden/` | |
| Flask app | `app/main.py` | Per-equipment routes, session-scoped |

### What intentionally does not exist yet

- **The Engine path is single-domain.** `Engine.from_plant()` wires against
  `Plant.topology`, which raises on a multi-domain plant. One solver per flow
  domain, and the boundary-condition update that couples them through vessel
  inventory, are T5-2.
- **The control valve has no hydraulic path (T7-1).** The line resistances and the
  `max_flow` clamp lived only in the retired standalone solve, so a page's flow
  runs above `max_flow`, the discharge valve strokes without changing flow, and
  spread equals the boundary difference. Equipment does not clamp; envelopes and
  alarms own that later. Known interim state, not a bug to fix in passing.
- **Reference plant files.** The loader reads `.json`, `.yaml` and `.yml` (YAML
  via `yaml.safe_load`, same C3 validation and `load_plant()` path), and T3-4
  (`d6a6cfb`) added the first plant files: `config/plants/liquid_transfer.yaml`
  and `gas_compression.yaml`, two single-domain fixtures checked against
  closed-form values in `tests/test_reference_plants.py`. The full
  `olefins_lite.yaml` train is T5-5, in M5, and does not exist.
- **Flow-domain enforcement is done for the loader only (T3-5, `11517f6`).**
  `load_plant` rejects a branch across domains, a domain with no boundary and a
  domain that is not one connected piece, and partitions into `Plant.topologies`.
  A `Topology` built by hand is still unchecked. `Session` calls the loader for
  the live pages' single-device plants.
- **Multi-port wiring in C3 is done for the loader only (T3-6, `2c08bb8`).**
  `equipment` takes `node_in`/`node_out` sugar or `ports` + `paths`; a device with
  `paths: []` is a coupling device, held in `Plant.devices` and in no `Topology`.
  The vessel (T5-1, `b8af231`) is the first device that uses it, and is tested only through the `device_types` override. The live pages' `Session`
  builds its single-device plants through the loader (T4-4); the vessel is still not registered in `DEVICE_TYPES`.
- **Single action endpoint (C5).** Routes are still per-equipment.
- **Controllers (PID), envelopes, alarms, trips, scenarios, scoring, historian,
  console.** All specified in the build plan, none implemented.
- `Snapshot`'s `controllers` / `envelope` / `alarms` sections are still empty
  by design. `nodes` and `streams` are filled from the solved topology since
  T4-4 for an engine built with one, and empty for `Engine(devices)`.

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
and has a task that retires it. T4-4 retired the legacy device `step()` path,
the interim `*_boundary_pressure` attributes, `simulation_speed`, and the
Engine-computes-nothing state; what remains is the consequence of that:

1. **A page's machine runs between two fixed battery limits with nothing in
   between.** The `Session` plants are one machine and two boundary nodes
   (750/750 psia and 50/50 psia). Flow reads above `max_flow` (compressor 331.7
   vs 120, pump 2236 vs 1200), the discharge valve strokes without changing
   flow, and spread equals the boundary difference so temperature is flat. Equal
   boundaries were kept so an idle machine sits at zero flow — exactly zero if it
   has never run, and within a hair of it once stopped; see *A stopped machine
   keeps a small residual flow* under Known technical debt.
   *Retired by:* **T7-1** (control valve on its own branch).

2. **No check valve.** With a boundary differential and a machine slower than it,
   the solver finds reverse flow through the machine, where the retired standalone
   solve clipped at zero. The live pages do not hit it because their boundaries
   are equal; a plant sized for running and started cold would.
   *Retired by:* **T7-1**, which carries the cold-start criterion.

3. **`get_state()` on a device is slow state only.** Flow and the two pressures
   are not device attributes; the page's row is assembled by
   `Session.compressor_state()` / `pump_state()` from the snapshot. The
   compressor's `temperature` became `temperature_at(spread)` because spread is
   a solver output. Do not put a flow or pressure back on a device.

## Known technical debt (recorded, not scheduled)

- **A stopped machine keeps a small residual flow.** A machine that has never run
  reads exactly `0.0`. One that has been *stopped* settles just off zero and
  stays there: **0.055 GPM** on the pump page, **0.004 SCFM** on the compressor,
  unchanged after a further 2200 simulated seconds. Convergence is measured in
  psia, and at shutoff the branch curve is flat — the root is a double root, so
  the 1e-7 psia of slack the tolerance allows maps to `sqrt(tolerance /
  resistance)` of flow, 0.08 GPM and 0.007 SCFM. The solver then reports
  `converged` at **`iterations: 0`**, because the residual is already inside
  tolerance on entry and nothing drives the flow the rest of the way down. The
  exact solution is still zero; only the reported one is not. Not a defect and
  not a reason to retune the tolerance — `tests/test_network_solver.py` has
  asserted `approx(0.0, abs=0.05)` on a stopped pump, with the reason in a
  comment, since T4-2, and `tests/test_cause_effect.py` asserts against a bound
  derived from the tolerance rather than a recorded value. **Do not assert an
  idle flow of exactly zero.** T5-2 settled what becomes of that residual
  downstream, and the answer is that nothing special does: a solved flow is
  written onto a coupling device and integrated with **no clamp and no
  deadband**, so a stopped machine's residual reaches vessel level at 0.055 GPM,
  or 3.3 gal/hour against a 1000 gal vessel. That is the decided behaviour; what
  stays recorded here is the numerical residual itself.
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

**No spine lock is held.** T5-2 merged as `154385c`
([PR #27](https://github.com/gareytwin1/plant-simulator/pull/27)) and released
the locks on `app/engine/engine.py` and `app/plant/topology.py`. **T5-3 is now
free to start** (`app/equipment/vessel.py`, gas-phase accumulation).

**T4-5 merged** as `e261134` ([PR #29](https://github.com/gareytwin1/plant-simulator/pull/29)):
`tests/test_cause_effect.py` and nothing else. It was rebased onto `154385c`
first and every reference value re-derived — each of its plants is
single-domain and holds no coupling device, so `build_couplings()` returns `[]`,
one solver is wired as before, and no assertion moved.

T4-4 merged as `0efa5be`
([PR #25](https://github.com/gareytwin1/plant-simulator/pull/25)) and released
`app/equipment/compressor.py`, `app/equipment/pump.py`,
`app/engine/sessions.py` and `app/main.py`.

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

**Checkpoint B (M4, network solver merged) is reached** (T4-4, `0efa5be`). The
plant is connected: a step integrates, solves and publishes. The next risk is
T5-2, which couples the liquid and gas domains through vessel inventory and takes
the `engine.py` spine lock again.

## Handoff: the next agents

**Everything below is verified against `main`** (567 tests, `mypy` clean). Read
[CLAUDE.md](../CLAUDE.md) first, then the build plan entry for your task. Model
guidance is the **Agent model guidance** section of CLAUDE.md; the column below
is the build plan's own assignment.

### Critical path after Checkpoint B

**M4 and the coupling are both done.** T4-4, **T4-5** (cause-and-effect suite,
test-only, re-scoped — see below) and **T5-2** (level-to-hydraulics coupling,
spine) are all Complete. Next on the critical path is T5-5 (integrated reference
plant, which also needs T3-4, Complete).

The solver is on the request path. `Engine.step()` calls `NetworkSolver`, the
snapshot's `solver`, `nodes` and `streams` are real, and topology owns hydraulic
flow and pressure. **T5-2 must not widen `characteristic(flow)`, and vessel
pressure stays integrated slow state supplying a boundary condition** — see ADR
0001 section 3.5.

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

### The T4-5 re-scope

**Agreed 20 September 2026, before any test was written.** The build plan's
original four acceptance criteria for T4-5 predate T4-4, which retired the
devices' standalone solve and with it the line resistances and the `max_flow`
clamp. Two of the four cannot be written against `main` at all:

| Original criterion | Now |
|---|---|
| Closing a downstream valve lowers flow and raises upstream pressure | **Moved to T7-1** |
| Added restriction raises upstream pressure | **Moved to T7-1** |
| More compressor load raises flow and discharge pressure | Kept, sharpened |
| Two parallel pumps raise flow by less than 2x | Kept, sharpened |

Both moved criteria need a second resistance in the network. Until T7-1 gives the
control valve a branch of its own, a machine runs between two fixed battery
limits with nothing between them and the compressor's discharge valve strokes
without changing anything hydraulically — item 1 of *Known temporary
compatibility paths*. They moved rather than merging as permanently skipped
tests, and **M4 is not held open waiting for T7-1**: T4-5 completes on what is
expressible now. T7-1 gained both criteria and a `T4-4` dependency, since a
valve's effect on a plant is asserted through `Engine.from_plant()`.

T4-5's kept criteria are joined by speed affinity, boundary response, stopping,
node mass balance and run-twice determinism. Two traps a session writing that
suite must not rediscover:

- **Backflow hides inside a "flow rises" assertion.** On the reference gas plant
  (60 -> 480 psia) raising `K-101`'s load 0.8 -> 0.9 -> 1.0 gives −121.7, −73.8,
  +70.7 SCFM. Flow does rise, so a naive assertion passes while the machine
  backflows. **Assert `flow > 0` in every compared state**, and pick a boundary
  difference the machines clear — 60 -> 300 psia gives forward flow on the series
  gas plant from about 0.75 load.
- **Assert only on settled states.** Load ramps at 0.05 per second, so a
  mid-ramp state can sit in the backflow regime.

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

**Startable:** T4-5 and T5-2 are both merged, so neither appears below. T5-3
(gas pressure, `app/equipment/vessel.py`) takes no spine lock and is free to
start now that T5-2 has released that file. **T7-1** carries two cause-and-effect
criteria moved off T4-5 and depends on T4-4 (Complete), so it stays startable —
and it is now the task that makes those two criteria expressible at all.

| Task | Name | Model | Branch |
|---|---|---|---|
| **T5-2** | Level to hydraulics coupling (spine) | Opus | `feature/inventory-coupling` |
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
solve to report". The default placeholder is now only what an `Engine` built
with no topology reports; an engine built from a plant publishes
`solver_status(result)` of its real solve every step (T4-4).

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
solver is untouched. `Engine.from_plant()` wires against `Plant.topology`, which
raises on a multi-domain plant; one solver per domain arrives with T5-2. (T5-1's
dependency moved to T3-6, the multi-port wiring half of the original T3-5, when
Amendment 1 split the task.)

## Test suite composition (567 tests on `main`)

| File | Tests | File | Tests |
|---|---|---|---|
| `test_api.py` | 7 | `test_plant_yaml.py` | 17 |
| `test_cause_effect.py` | 26 | `test_pump.py` | 9 |
| `test_clock.py` | 13 | `test_pump_api.py` | 8 |
| `test_compressor.py` | 19 | `test_random_source_guard.py` | 24 |
| `test_engine.py` | 13 | `test_reference_plants.py` | 20 |
| `test_engine_solver.py` | 17 | `test_registry.py` | 16 |
| `test_equipment_contract.py` | 67 | `test_rng.py` | 10 |
| `test_golden_regression.py` | 15 | `test_session_isolation.py` | 6 |
| `test_inventory_coupling.py` | 26 | `test_sessions.py` | 7 |
| `test_network_solver.py` | 36 | `test_snapshot.py` | 16 |
| `test_plant_config_validation.py` | 13 | `test_solver_diagnostics.py` | 14 |
| `test_plant_domains.py` | 23 | `test_topology.py` | 59 |
| `test_plant_loader.py` | 27 | `test_vessel.py` | 27 |
| `test_plant_ports.py` | 32 |  |  |

`test_equipment_contract.py` and `test_registry.py` discover device classes
dynamically, so a new `Equipment` subclass is swept into the contract tests
automatically — adding one raises the total by more than the tests you wrote.
The table above is regenerated from `pytest --collect-only`, not maintained by
hand, because it had drifted twice before T4-5.

Recent movement: T4-4 took `main` to 486; T5-2 added `test_inventory_coupling.py`
(26) and grew `test_vessel.py`, `test_topology.py` and `test_engine_solver.py`,
taking it to 541; T4-5 added `test_cause_effect.py` (26), taking it to 567.
