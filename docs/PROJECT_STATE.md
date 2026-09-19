# Project State

Concise handoff for a new session. **This file goes stale by design** — it
records what is true right now, not the stable rules. Those are in
[CLAUDE.md](../CLAUDE.md).

Refresh this file whenever a task merges to `main`.

---

**Last refreshed:** 19 September 2026 (handoff refresh after T3-3 and T2-2)
**Current `main`:** `26748e79091366aa2491c5607aa55ad71d160c1c` — *Merge T2-2: Seeded RNG service*
**Full suite on `main`:** **297 passed** (`conda activate plant-simulator && python -m pytest -q`); `python -m mypy` clean

---

## Milestone progress

| Milestone | Status |
|---|---|
| **M0** Baseline Cleanup | **7/7 Complete** |
| **M1** Equipment Model Contract | **5/5 Complete** — Checkpoint A reached |
| **M2** Simulation Engine and Clock | 4/6 (T2-5 startable) |
| **M3** Plant Topology and Streams | 3/4 (T3-4 startable) |
| **M4** Pressure-Flow Network Solver | 1/5 — T4-1 Complete; **T4-2 startable** (T3-3 merged) |
| M5–M19 | Not started |

Overall: **20 of 94 tasks Complete.**

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
  `signed_square`, `Branch.characteristic()` / `Branch.residual()`).

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
| Topology (C2) | `app/plant/topology.py` | `Node`, `Branch`, `Stream`, `Topology`; `Branch.characteristic()` / `residual()` as of T4-1 |
| Plant config schema (C3) + validator | `config/schema/plant.schema.json`, `app/plant/validate.py` | |
| Golden regression harness | `tests/golden_regression.py`, `tests/fixtures/golden/` | |
| Flask app | `app/main.py` | Per-equipment routes, session-scoped |

### What intentionally does not exist yet

- **Plant-wide network solver (T4-2) and its engine wiring (T4-4).** No
  plant-wide pressure/flow solution.
- **A reference plant file (T3-4).** The loader exists, but no
  `config/plants/*.yaml` file does, and the loader reads JSON only — `pyyaml`
  is not in `requirements.txt`.
- **Single action endpoint (C5).** Routes are still per-equipment.
- **Controllers (PID), envelopes, alarms, trips, scenarios, scoring, historian,
  console.** All specified in the build plan, none implemented.
- `Snapshot`'s `nodes` / `streams` / `controllers` / `envelope` / `alarms`
  sections are deliberately empty, and `solver` is a trivial converged
  placeholder.

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
Merged local branches can be deleted at any time.

## Next integration checkpoint

**Checkpoint B — M4, network solver merged.** This is where the plant stops
being a collection of independent gauges and becomes connected. It is the single
largest risk in the plan: the build plan calls out solver non-convergence on a
reasonable-looking topology as one of three most-likely slip points.

## Handoff: the next agents

**Everything below is verified against `main`** (297 tests, `mypy` clean). Read
[CLAUDE.md](../CLAUDE.md) first, then the build plan entry for your task. Model
guidance is the **Agent model guidance** section of CLAUDE.md; the column below
is the build plan's own assignment.

### Critical path to Checkpoint B

`T4-2` (solver) -> `T4-3` (diagnostics) -> `T4-4` (wire into engine, **spine**,
freezes other merges) -> `T4-5` (cause-and-effect suite).

**T4-2 is startable now (Opus, `feature/network-solver`).** Two things to know
before it starts:

- Its acceptance criteria say "converges on the reference plant", but the
  reference plant file is **T3-4**, which is not done. Either run T3-4 first or
  in parallel, or build T4-2 against small hand-built configs through
  `load_plant()` and add the reference-plant convergence test when T3-4 lands.
- The `reset()` open question below affects any solver test that resets a
  loaded plant.

**T3-4 (Sonnet, `feature/reference-plant`) is the natural companion.** It needs
YAML, and the loader reads JSON only: adding `pyyaml` to `requirements.txt` and a
`.yaml` branch in `load_plant_file` is part of that task, not an accident.

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

### Startable now (19 tasks)

All dependencies are Complete, and none of these edits an existing spine file.
T4-3 (`snapshot.py`) and T4-4 (`engine.py`) will, and are not startable yet.
Three of them (T2-5, T4-2, T12-1) add a *new* file under `app/engine/`, which
CLAUDE.md lists as spine; the build plan treats them as satellites and `rng.py`
landed the same way, but if an agent finds it needs to edit an existing file
there, it stops and asks.

| Task | Name | Model | Branch |
|---|---|---|---|
| **T4-2** | Newton-Raphson network solver | Opus | `feature/network-solver` |
| **T3-4** | Reference plant configuration | Sonnet | `feature/reference-plant` |
| **T5-1** | Vessel model (likely hits the C3 multi-port gap) | Sonnet | `feature/vessel-model` |
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
| **T18-2** | CI pipeline (should run `python -m mypy` too) | Haiku | `chore/ci-pipeline` |

Working rules for every one of them: one worktree per task branched from
`origin/main`; full suite **and** `python -m mypy` green before review; small
commits led by the task ID; **Ready for Review** at PR time and **Complete** only
with the merge SHA. `tests/test_random_source_guard.py` shows the AST-scan style
a layering guard like T13-5 can follow.

## Test suite composition (297 tests)

| File | Tests | File | Tests |
|---|---|---|---|
| `test_equipment_contract.py` | 58 | `test_registry.py` | 10 |
| `test_topology.py` | 47 | `test_pump.py` | 8 |
| `test_plant_loader.py` | 27 | `test_pump_api.py` | 8 |
| `test_compressor.py` | 21 | `test_api.py` | 7 |
| `test_golden_regression.py` | 17 | `test_sessions.py` | 7 |
| `test_plant_config_validation.py` | 13 | `test_session_isolation.py` | 6 |
| `test_engine.py` | 13 | `test_clock.py` | 13 |
| `test_snapshot.py` | 11 | `test_random_source_guard.py` | 21 |
| `test_rng.py` | 10 | | |

`test_equipment_contract.py` and `test_registry.py` discover device classes
dynamically, so a new `Equipment` subclass is swept into the contract tests
automatically — adding one raises the total by more than the tests you wrote.
