# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 5 October 2026, at `da9b2d8` (Merge T16-8: Training
session, PR #139) - **this is a snapshot,
not a live pointer.** Run `git log da9b2d8..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **3175 passed** · `python -m mypy` clean over 81 source files · no golden trace movement
**In flight:** nothing.
**No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T16-8** | `da9b2d8` | Training session: `app/training/session.py` `TrainingSession` is a standalone session (not a `Session`) owning a free-play `PlantRuntime` (`config.FREE_PLAY_PLANT` at `FREE_PLAY_CONDITION`), a `ScenarioRunner` and one `Scheduler` over itself; step and snapshot follow the runner while a scenario is loaded, and every write is a scheduler command under `step_lock`. `SessionRegistry` is generic over any `Endable` session with a `factory` (default `Session`) and `lease(session_id)`, which pins a session against the idle sweep (T16-9 passes it as the stream's `hold`); the scenario blueprint takes a `ScenarioControl` protocol and gains `POST /api/scenario/unload`. `main.py` and the registry's default factory are unchanged: T16-9 switches them after T16-10 retires the legacy routes. 32 plants step in ~20 ms per round, so `MAX_SESSIONS` stays 32 (PR #139) |
| **T18-9** | `ed5934e` | Stream transport under Gunicorn: `_socket_of` also reads `gunicorn.socket`, so a client that stops reading is dropped within the dropout bound under the T18-1 deployment; the socket's prior timeout is restored on response close (after the server's closing write; gthread reuses kept-alive connections); `create_stream_blueprint` takes an optional `hold` context manager, entered before the first event and exited when the stream ends (T16-9 passes the T16-8 session lease). Tested under a real Gunicorn gthread server (PR #138) |
| **T16-7** | `d2b7583` | Scenarios run on the plant runtime: `ScenarioRunner` builds a `PlantRuntime` after `restore_state` on arm and on abort (the action log is carried over an abort), steps it, and routes `act` through it, so an interlock `reset` is journaled and replayed; adds `acknowledge`, `alarm_entries` and `unload`. Known gap: a scenario malfunction reaches the trip check one step late (needs `PlantRuntime.invalidate()`, T16-6's file). |
| **T16-6** | `a4c5882` | Plant runtime: `app/training/runtime.py` `PlantRuntime` wraps an `Engine` and is `Steppable`; each step runs `TripSystem.update`, `Engine.step`, then one `EnvelopeEvent` per configured limit into `AlarmManager` and `AlarmHistory` (also once at construction). `act()` is the one operator entry point (device actions, interlock `reset`); one lock covers step, act, acknowledge and history reads. The acknowledge sequence moved to `app/alarms/acknowledge.py` and `create_alarm_blueprint` takes `(get_entries, acknowledge)`. Not wired into any session yet (T16-7, T16-8); `app/training` is orchestration in the layer guard (PR #136) |
| **T18-6** | `f7a8b7c` | Scheduler log records carry sim time: the slow-step warning stamps the `sim_time` of the snapshot the step just published, the step-failure record the last published snapshot's (null if none), via `extra=` in `Scheduler._run`; no clock, no `Snapshot` field (PR #135) |
| **T18-7** | `508137c` | Read-only session lookup: `SessionRegistry.peek(session_id)` returns the live session under `_lock` with no idle sweep and no touch (a session past the timeout is still returned until the next `get()` or `create()` sweeps it). `/health/engine` resolves its cookie through it, so a probe no longer keeps its plant alive or ends others' idle plants (PR #134) |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-3, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12, M13, M14, M15, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 4/5 | T10-1, T10-2, T10-3, T10-5; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M16** Operator Console | 5/13 | T16-1, T16-2, T16-6, T16-7, T16-8; T16-3, T16-4, T16-5 and T16-10 startable; T16-11, T16-12, T16-13 then T16-9 chain behind T16-10 |
| **M17** Historian and Trends | 1/4 | T17-1; T17-2 startable (V1.1-deferred) |
| **M18** Deployment | 8/9 | T18-1 to T18-7, T18-9; T18-8 (rate limit behind a reverse proxy) startable once Opus decides its shape |
| M19 | 0 | - |

**112 of 129 tasks Complete.** Checkpoints A-C reached. Checkpoint **D** (M8)
needs only its "loops reject an injected disturbance" gate: PIC-101 switched to
AUTO in `olefins_lite.yaml`, which T8-6 enabled but no task owns yet.

## The next task

**10 tasks are startable** - list them from
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json) (`startable`
field). All are Sonnet except T18-8 (Opus: it decides how the rate limiter trusts a proxy header). T10-4, T11-4 and T17-2 are V1.1-deferred;
T19-2 (startable - its other dependency, T13-1, was already Complete) is
deferred further still, to **V2**.

**Next by leverage: T16-10, then T16-11, T16-12, T16-13 and T16-9** - the chain
that puts the live plant, its trips, alarms and scenarios behind a landing page
and the console. T16-10 retires the
single-machine pages and the legacy `Session` (the console does not build on
them); T16-11 is the landing page at `/` with the shared header; T16-12 keeps
a scenario's cause out of the browser until the run ends; T16-13 sends the
browser an operator view of the snapshot (no valve fault flags); T16-9 wires
the console last. Shapes are
decided in each task's build-plan note. T16-3 to T16-5 can still build against
fixtures meanwhile.

**Scheduling notes.** The spine lock is one global lock
([DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); it is free. **The container runs
exactly one Gunicorn worker** (T18-1) - `SessionRegistry` is per-process (R7); never scale it.
**Trips and alarms run in `TrainingSession` (T16-8), which the app does not serve yet**:
`PlantRuntime` (T16-6) runs both around the engine step, `ScenarioRunner` (T16-7)
builds one, and `main.py` still builds the legacy `Session` until T16-9.
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
