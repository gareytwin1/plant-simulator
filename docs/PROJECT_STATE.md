# Project State

Concise handoff for a new session. **This file goes stale by design** — it
records what is true right now, not the stable rules. Those are in
[CLAUDE.md](../CLAUDE.md).

Refresh this file whenever a task merges to `main`.

---

**Last refreshed:** 19 September 2026
**Current `main`:** `5e1a9f0512d9474f04b3fe3a69a252f983eb8415` — *Merge T1-4: Refactor the pump onto the equipment contract*
**Full suite on `main`:** **202 passed** (`conda activate plant-simulator && python -m pytest -q`)

---

## Milestone progress

| Milestone | Status |
|---|---|
| **M0** Baseline Cleanup | **6/6 Complete** |
| **M1** Equipment Model Contract | **5/5 Complete** — Checkpoint A reached |
| **M2** Simulation Engine and Clock | 3/6 (T2-2 corrected to In Progress; T2-5 startable) |
| **M3** Plant Topology and Streams | 2/4 (T3-3 startable) |
| **M4** Pressure-Flow Network Solver | 0/5 — **not started** |
| M5–M19 | Not started |

Overall: **16 of 93 tasks Complete.**

### Completed and merged to `main`

- **M0** — `T0-1` … `T0-6`: baseline cleanup, pump endpoints, unit convention,
  portable requirements.
- **M1** — `T1-1` golden harness · `T1-2` Equipment base + Port (C1) ·
  `T1-3` compressor onto C1 · `T1-4` pump onto C1 · `T1-5` equipment registry.
- **M2** — `T2-1` simulation clock · `T2-3` engine + snapshot (C4) ·
  `T2-4` session/plant registry.
- **M3** — `T3-1` plant config schema + validator (C3) ·
  `T3-2` node/branch/stream (C2).

### Status correction made during this refresh

**T2-2 (Seeded RNG service) was marked Complete but is not on `main`.** Its note
claimed "Merged feature/seeded-rng … commit 611c5b6", but `app/engine/rng.py`
and `tests/test_rng.py` do not exist on `main`. Commit `611c5b6` lives only on
the **local-only, never-pushed, never-merged** branch `feature/seeded-rng`,
which is also well behind current `main`. Status corrected to **In Progress** in
both the live artifact and `docs/BUILD_PLAN_STATUS.json`.

Impact is contained: nothing in `app/` imports `random` today, so no determinism
rule is currently violated, and the only dependent task (**T14-5**, deterministic
replay) is blocked on other dependencies anyway. No currently-startable task
changes. To finish T2-2: rebase the branch onto `main`, reconcile it with project
style (it uses type hints, which this codebase does not), run the full suite,
open a PR, merge.

## Architecture status

### What exists

| Component | Where | Notes |
|---|---|---|
| Equipment contract (C1) + `Port` | `app/equipment/base.py` | Frozen at Checkpoint A |
| `GasCompressor` (`K-101`) | `app/equipment/compressor.py` | On C1 |
| `CentrifugalPump` (`P-101`) | `app/equipment/pump.py` | On C1 as of T1-4 |
| `EquipmentRegistry` | `app/equipment/registry.py` | Tag → device; rejects duplicate tags |
| `SimulationClock` | `app/engine/clock.py` | Sim time, speed, pause |
| `Engine` | `app/engine/engine.py` | Integrates all devices, publishes snapshot |
| `Snapshot` (C4) | `app/engine/snapshot.py` | Immutable; shape frozen |
| `SessionRegistry` / `Session` | `app/engine/sessions.py` | Per-browser plant isolation |
| Topology (C2) | `app/plant/topology.py` | `Node`, `Branch`, `Stream`, `Topology` |
| Plant config schema (C3) + validator | `config/schema/plant.schema.json`, `app/plant/validate.py` | |
| Golden regression harness | `tests/golden_regression.py`, `tests/fixtures/golden/` | |
| Flask app | `app/main.py` | Per-equipment routes, session-scoped |

### What intentionally does not exist yet

- **Plant-wide network solver (T4-2).** No plant-wide pressure/flow solution.
- **Plant loader (T3-3).** Nothing builds a `Topology` from a config file yet;
  the schema and validator exist, but no loader consumes them, and no
  `config/plants/*.yaml` file exists.
- **Single action endpoint (C5).** Routes are still per-equipment.
- **Controllers (PID), envelopes, alarms, trips, scenarios, scoring, historian,
  console.** All specified in the build plan, none implemented.
- `Snapshot`'s `nodes` / `streams` / `controllers` / `envelope` / `alarms`
  sections are deliberately empty, and `solver` is a trivial converged
  placeholder.

## Known temporary compatibility paths

**Do not "fix" these as a side effect of an unrelated task.** Each is deliberate
and has a task that retires it.

1. **Legacy device `step()` + standalone operating-point solve.**
   Both `GasCompressor` and `CentrifugalPump` keep `step()`, which runs
   `integrate(dt)` then `_calculate_operating_point()`. The live Flask routes
   (`/api/step`, `/api/pump/step`) call this path, so removing it breaks the app.
   *Retired by:* **T4-2** (network solver).

2. **Device-owned `upstream_boundary_pressure` / `downstream_boundary_pressure`.**
   Interim attributes on both devices, feeding only the standalone solve. They
   are not node pressures and deliberately avoid the C1 forbidden-attribute
   names, but in the target architecture the topology owns these.
   *Retired by:* **T4-2**.

3. **`Engine` does not consult `device.simulation_speed`.**
   The clock is the sole speed authority for anything driven through the Engine.
   A device's own `simulation_speed` affects only its legacy `step()`. Pinned by
   `test_compressor_simulation_speed_is_not_consulted_by_the_engine` so the two
   cannot silently start disagreeing.

4. **`Engine.step()` computes no flow or pressure.**
   It calls `integrate()` only. Stepping a device through the Engine does not
   change its flow/pressure. This is correct until T4-2, and the two engine
   golden tests assert slow-state fields only for exactly this reason.

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

**None.** No open PRs; no feature branches in flight on `origin` as of this
refresh.

Two stale local-only branches exist in the primary clone and can be deleted once
confirmed merged: `feature/plant-topology`, `feature/seeded-rng` (their work is
on `main` as `2e56c48` and `611c5b6`).

## Next integration checkpoint

**Checkpoint B — M4, network solver merged.** This is where the plant stops
being a collection of independent gauges and becomes connected. It is the single
largest risk in the plan: the build plan calls out solver non-convergence on a
reasonable-looking topology as one of three most-likely slip points.

## Recommended next spine task

**T4-1 — Branch characteristic interface** (`feature/branch-characteristic`).

- Category: **core/spine**. Touches `app/equipment/base.py` and
  `app/plant/topology.py` — both spine files, so it takes the spine lock alone.
- Dependencies `T1-2` and `T3-2` are both Complete, so it is startable now.
- It is the direct prerequisite for **T4-2** (Newton-Raphson network solver,
  `app/engine/network.py`), which additionally needs **T3-3**.

**Shortest path to Checkpoint B:** `T4-1` (spine) and `T3-3` (plant loader, can
run in parallel) → then `T4-2` → `T4-3`.

## Tasks that can safely run in parallel now

18 tasks have all dependencies Complete. The independent ones touch no shared
file and can be handed to separate agents immediately:

| Task | Name | Branch |
|---|---|---|
| **T8-1** | PID block | `feature/pid-block` |
| **T9-1** | Envelope evaluator | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | `feature/alarm-state-machine` |
| **T13-5** | Physics isolation guard | `test/import-direction-guard` |
| **T14-1** | Scenario file schema | `feature/scenario-schema` |
| **T15-4** | Score persistence | `feature/score-store` |
| **T16-1** | Console design system | `design/console-system` |
| **T17-1** | Ring-buffer historian | `feature/historian` |
| **T18-1** | Container and WSGI serving | `chore/container-and-ci` |
| **T18-2** | CI pipeline | `chore/ci-pipeline` |
| **T12-1** | Plant snapshot save and restore | `feature/state-persistence` |
| **T15-1** | Operator action log | `feature/action-log` |
| **T5-1** | Vessel model | `feature/vessel-model` |
| **T6-1** | Stream enthalpy and mixing | `feature/stream-enthalpy` |
| **T13-1** | Malfunction model and registry | `feature/malfunction-model` |
| **T2-5** | Background scheduler | `feature/engine-scheduler` |
| **T3-3** | Plant loader and topology validation | `feature/plant-loader` |
| **T4-1** | Branch characteristic interface | `feature/branch-characteristic` ← **spine, one owner** |

Only **T4-1** takes the spine lock. Everything else above is a satellite and
merges independently.

## Test suite composition (202 tests)

| File | Tests | File | Tests |
|---|---|---|---|
| `test_topology.py` | 38 | `test_clock.py` | 13 |
| `test_equipment_contract.py` | 33 | `test_engine.py` | 13 |
| `test_compressor.py` | 17 | `test_snapshot.py` | 11 |
| `test_golden_regression.py` | 17 | `test_pump_api.py` | 8 |
| `test_plant_config_validation.py` | 13 | `test_api.py` | 7 |
| `test_sessions.py` | 7 | `test_registry.py` | 6 |
| `test_session_isolation.py` | 6 | `test_pump.py` | 4 |

`test_equipment_contract.py` and `test_registry.py` discover device classes
dynamically, so a new `Equipment` subclass is swept into the contract tests
automatically — adding one raises the total by more than the tests you wrote.
