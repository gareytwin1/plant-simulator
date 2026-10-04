# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 3 October 2026, at `cc2784f` (Merge T18-1:
Container and WSGI serving, PR #130) - **this is a snapshot,
not a live pointer.** Run `git log cc2784f..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **2962 passed** · `python -m mypy` clean over 75 source files · no golden trace movement
**In flight:** nothing.
**No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T18-1** | `cc2784f` | Container and WSGI serving: `gunicorn.conf.py` pins `workers = 1` (gthread, 64 threads, no preload; a test reads the pin), multi-stage `Dockerfile` (`runtime` default, `test` target runs the suite), `docker-compose.yml` (one service, never scale), `.dockerignore`; `gunicorn==26.2.0`. A `docker` job in `ci.yml` runs the suite in the image, serves the runtime image and asserts one master plus one worker. Gunicorn gives the SSE stream no socket send timeout, and the stream blueprint is not registered in `main.py` (PR #130) |
| **T18-5** | `1927cbb` | Session lifecycle and config versioning: `SessionRegistry.reclaim_idle()` ends sessions idle for `config.SESSION_IDLE_SECONDS` (1800), run lazily under the registry lock by `get()` and `create()` (no reaper thread, lock order unchanged); `config/VERSION` (1.0.0) read by the leaf `app/configversion.py`, and `tests/test_config_version_guard.py` fails a config edit that does not bump it; `ScoreStore` stamps results with the version (migration 2) and `personal_best` counts only the store's major. `STATE_VERSION` stays 1 (PR #129) |
| **T18-3** | `b49f9bc` | API input validation and rate limiting, `app/api/validate.py` (body readers: unknown keys, finite, +/-1e6 magnitude incl. huge int literals; 4 KiB body cap; JSON errors under `/api/`; per-address token bucket with an injected monotonic clock, 429 plus `Retry-After`; installed on the live app before the session hook). Consoles ignore non-ok responses and throttle slider posts. Targets still clamp to [0,1]. Limiter keys on `remote_addr`, so clients behind a proxy or NAT share one bucket. The action, alarm and scenario blueprints are validated but still not registered in `main.py`. |
| **T12-5** | `4d6eed4` | Public state accessors: `Engine`, `Loop` (with its `PID`), `CommandArbiter`, `Evaluator` and `ExcursionTracker` each `checkpoint()`, `validate_checkpoint()` and `restore_checkpoint()` their own slow state; `persistence.py` composes them and reads no private attribute (AST guard test). `StateError(path, reason)` now lives in `app/statetypes.py`; `Band` is public. Saved layout unchanged, `STATE_VERSION` stays 1. Follow-ups: drop `Engine._envelope_band` (repeats the evaluator band), rename `master` in `modes.py` (PR #127) |
| **T8-5** | `4386993` | Cascade control, `app/controls/cascade.py` (`Cascade` pairs an outer and an inner `Loop`, scales the outer output into the inner setpoint's range through a stand-in outer loop so `Loop` and `PID` are untouched; safe mode is the inner loop in MANUAL holding its last output, `engage()` resumes with no step). Not wired: the loader, C3 and `Engine` cannot create a cascade, and no level PV resolver exists (PR #126) |
| **T12-4** | `611a444` | Startup and shutdown sequences, `app/plant/sequences.py` and `config/sequences/olefins_lite.yaml` (the state machine's gates plus cold start, normal shutdown and emergency shutdown for the reference plant; `SequenceRun` takes steps strictly in order, a hold is a condition held for `for_s` of snapshot `sim_time`, nothing is judged on a reading from before the last actions; `Sequencer` drives one procedure at a time and an emergency shutdown replaces any run; every action goes through the caller's action function). Not wired: no session or API loads a sequence, and trips are not live so a caller starts an emergency shutdown. Follow-up: `PlantStateMachine.gated(target)` (PR #125) |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-3, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12, M13, M14, M15, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 3/5 | T10-1, T10-2, T10-3; T10-4 startable (V1.1-deferred), T10-5 startable |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M16** Operator Console | 1/5 | T16-2; T16-1 startable, T16-5 startable; T16-3 needs T16-1 first |
| **M17** Historian and Trends | 1/4 | T17-1; T17-2 startable (V1.1-deferred) |
| **M18** Deployment | 4/5 | T18-1, T18-2, T18-3, T18-5; T18-4 startable |
| M19 | 0 | - |

**103 of 117 tasks Complete.** Checkpoints A-C reached. Checkpoint **D** (M8)
needs only its "loops reject an injected disturbance" gate: PIC-101 switched to
AUTO in `olefins_lite.yaml`, which T8-6 enabled but no task owns yet.

## The next task

**9 tasks are startable** - list them from
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json) (`startable`
field). All are Sonnet. T10-4, T11-4 and T17-2 are V1.1-deferred;
T19-2 (startable - its other dependency, T13-1, was already Complete) is
deferred further still, to **V2**.

**Scheduling notes.** The spine lock is one global lock
([DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); it is free. **The container runs
exactly one Gunicorn worker** (T18-1) - `SessionRegistry` is per-process (R7); never scale it.
**Trips do not run in a live session yet**: nothing in `Session`/`Scheduler`
calls `TripSystem.update`, and wiring it in is a spine change no task owns.
`RestartGate` (T11-3) must be updated before `TripSystem.update`, and only
blocks a restart for a trip listed in its `resets`; an ungated trip still lets a
standing lower-precedence RUN demand restart a machine the moment it releases.
Permissives have no C3 config key yet. A loop overridden by a trip is not tracked, so
its handback is not bumpless - also an unowned `Engine._control` change.

## Known interim behaviour — do not "fix" these in passing

Each is deliberate. Fixing one as a side effect of an unrelated task is out of
scope, and item 1 in particular reads like a bug and is not.

1. **A page's machine runs between two fixed battery limits with nothing in
   between.** The `Session` plants are one machine and two boundary nodes
   (750/750 psia, 50/50 psia). Flow reads above `max_flow` (compressor 331.7 vs
   120, pump 2236 vs 1200), the discharge valve strokes without changing flow,
   and spread equals the boundary difference, so temperature is flat. Equal
   boundaries keep an idle machine at zero flow. Equipment does not clamp —
   envelopes and alarms own that later.
   *Status:* **T7-1 built the valve** and a plant that uses it
   (`liquid_valve_train.yaml`); the pages are not yet rewired onto a plant that
   contains one.

2. **No check valve.** With a boundary differential and a machine slower than
   it, the solver finds reverse flow through the machine where the retired
   standalone solve clipped at zero. The live pages have equal boundaries and do
   not hit it; a plant sized for running and started cold would.
   *Status:* **not retired.** A resistance cannot stop reverse flow against an
   adverse gradient. Needs a check valve, which no task owns. T11-1
   (`app/safety/interlocks.py`) left P-101's backflow trip (the interlock twin
   of `limits`' `P-101.flow lo_lo: -20.0` in `olefins_lite.yaml`) unconfigured
   for exactly this reason: `P-101.stop` cannot clear the backflow it would
   trip on, since a stopped P-101 already backflows past -20 GPM. Restore it
   when a check valve lands.

3. **`get_state()` on a device is slow state only.** Flow and the two pressures
   are not device attributes; a page's row is assembled from the snapshot by
   `Session.compressor_state()` / `pump_state()`. The compressor's `temperature`
   became `temperature_at(suction, discharge)` because suction and discharge
   pressure are solver outputs. **Do not put a flow or a pressure back on a
   device.**

## Known technical debt (recorded, not scheduled)

- **Boundary temperatures are not in C3.** A config-loaded plant supplies 60 °F
  everywhere unless the caller passes `boundary_temperatures`; a node
  `temperature` field is an unowned C3 change. The compressor page's
  `temperature` still reads `temperature_at` from the 75 °F design suction, and
  C4 has no field for a transport that did not settle.
- **Instruments are not in C3, and no device-to-point resolver exists.**
  `Engine(instruments=...)` is the only way to add a transmitter. A
  `controllers.pv` resolves against a node id (T8-3), so only pressure loops
  can bind. A `limits` `(tag, variable)` resolves only against a device's own
  `get_state()` field (T9-4): `V-101.level` classifies live, but
  `K-101.discharge_pressure` and `P-101.flow` in `olefins_lite.yaml` are warned
  about once at Engine construction and never evaluated. Transmitters only have
  `bias` - no stuck or range-clamped reading (T13-2).
- **A stopped machine keeps a small residual flow**: 0.055 GPM (pump), 0.004
  SCFM (compressor); never-run reads exactly `0.0`. At shutoff the branch root
  is a double root, so 1e-7 psia of slack maps to `sqrt(tolerance /
  resistance)` of flow. It reaches vessel level unclamped (3.3 gal/hour against
  1000 gal). Decided behaviour: **do not retune the tolerance, and do not assert
  an idle flow of exactly zero.**
- **`Engine._envelope_band` repeats its evaluator's held band** (T12-5 left it
  because removing it touches `Engine.snapshot`). Unowned. `Loop`'s `master`
  parameter in `modes.py` is naming debt (outer loop / inner loop). Unowned.
- **The `Equipment.characteristic` docstring overstates the Jacobian**; `base.py`
  is frozen. See [.claude/rules/engine.md](../../.claude/rules/engine.md).
- **A resistance-only valve cannot stop reverse flow and absorbs most of the
  drop** (107-139 of 150 psi, 70-93% of system drop vs 10-30% in a real plant).
  A pipe or line-loss resistance device is needed; **no task owns one.**
- **Contract-test discovery is import-order dependent**: `REGISTERED` in
  `tests/test_equipment_contract.py` is computed at import, so an `Equipment`
  subclass in a later-imported test module escapes the contract tests.
- Harmless oddities: `app/init.py` is a misnamed empty file (`app` is a
  namespace package); `static/style.css` is a 15-line stub until M16;
  `package.json` exists only for a TypeScript dev dependency.

## Open decisions with no owner

1. **Who owns a plant's RNG** — session, engine or plant? Undecided on purpose.
   `SeededRNG` requires a seed and there is deliberately no global stream,
   because a process-wide generator would leak draws between browser sessions.
   **It belongs to the first task that needs randomness.** `SeededRNG` also has
   no state save/restore, so neither `capture_state` (T12-1) nor a replay
   recording (T14-5) carries RNG state; that task must add it to both, and
   `tests/test_scenario_replay.py` fails the build once anything imports it.
2. **`Equipment.reset()` drops design values.** The loader applies a config's
   `design` by setting attributes after construction, but `reset()` restores
   state captured at the end of `__init__` — so a reset device returns to class
   defaults, not its configured design. Fixing it needs a design/configure hook
   on C1, a **spine change**, before anything relies on `reset()` for a loaded
   plant. `restore_state` (T12-1) does not use `reset()`, so it is unaffected.
   Needs an Opus decision.

## Traps for the next tasks

- **Mass-balance identity** (ADR 0002 §7.1) is written down and checked in
  `tests/test_mass_balance.py`; read it before re-deriving conservation.
- **Near zero vapour flow, explicit Euler limit-cycles** (stable period-2,
  ±0.0615 SCFM / ±7.5e-6 psi, bounded to step 12,000). Unowned until M8 testing
  needs it; **assert a pressure asymptote, not a final flow.**
- **Do not assert a cold-start operating point in T3-4's reference fixtures**:
  without a check valve or line resistance, a stopped machine backflows if
  boundaries are sized for running, and a started one runs away if sized cold.
- Repo-wide test-writing traps and golden-trace policy:
  [.claude/rules/testing.md](../../.claude/rules/testing.md).

Everything else: the "Where authoritative state lives" table in
[AGENTS.md](../../AGENTS.md).
