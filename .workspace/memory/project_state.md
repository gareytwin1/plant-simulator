# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 16 October 2026, at `d8edf4a` (Merge T20-22, PR #181: result and debrief panel) - **this is a snapshot,
not a live pointer.** Run `git log d8edf4a..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **4328 passed** · `python -m mypy` clean over 89 source files · no golden trace movement
**In flight:** nothing. The spine lock and the `app/main.py` lock are free.
No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T20-22** | `d8edf4a` | Result and debrief panel on the console: `static/js/debrief.js` reads the ribbon's existing scenario poll (`onResult`, no second poll) and, once a run is complete or aborted, opens a non-modal dialog over the canvas with the outcome in words and a glyph, run time, the score and what counted against it ("in full", "partly", "a little", worded from a table keyed by `config/scoring.yaml`'s penalty names, which a test keeps in step), and the debrief. It opens itself once per finished run (keeping focus where an operator is typing), a ribbon Result button reopens it, Try again loads and starts the same scenario, and nothing of the outcome or score renders while a run is loaded or running. The run controls gain `adopt`, `say`, `resync` and a shared `timedFetch`. No golden moved. Built on Opus, review fixes on Sonnet (PR #181). Browser-checked one scenario per plant, pass and fail, before the review fixes |
| **T20-19** | `d6a7ddd` | Redesign acceptance pass at desktop width (phone moved to T20-24): all ten scenarios played to a pass and to a fail in headless Firefox with real input events, one by keyboard only, an alarm marker seen on a trend. Fixes: each scenario shows its own plant on the Home page (`CatalogueEntry.plant`), keyboard focus after Start or Stop, a favicon, the prototype's canvas hint. The pass found that a finished run shows no result on the console (T20-22), and that alarms and trends show the plant clock (T20-23). Built on Opus (PR #180) |
| **T20-18** | `e952be3` | Separator scenarios: drain and vent restriction (capacity malfunctions) and feed and drain signal loss, each a fault at 30 s the loops in AUTO cannot correct. A scenario can now set a valve's failure-mode flag: C8 `FLAGS` beside `WRITABLE` (`ControlValve` `signal_ok`, `stuck`, `action_reversed`; bool value, step only), and the validator accepts a list of types. XV-301 is a new feed shutdown valve that LSHH-301 and PSHH-301 close, because a valve with a lost signal ignores a trip (a lost LV-301 signal still defeats LSLL-301); the feed surge fails at the high alarm, as the drum settles short of the trip. `separator_normal.json` regenerated, `separator.svg` reworked. Config 4.0.0; no golden moved. Decided and built on Opus (PR #179). Not browser-checked |
| **T20-16** | `832cfc2` | Run history per browser: an anonymous `plant_operator_id` cookie (uuid4 hex, HttpOnly, SameSite=Lax, one year, renewed on page loads; a live session's id wins over a changed cookie). `TrainingSession` records each completed run once through a recorder (errors logged, never stop the plant); `ScoreStore` at `config.SCORE_DB_PATH` (env `PLANT_SCORE_DB`, `plant-data` volume in compose) gains `recent`, `personal_bests` and a connection lock. `GET /api/history` returns recent runs (cap 5) and bests within the config major, by key and title, never id. The landing Recent scenarios card lists them. Built on Opus (PR #177). Docker image only covered by CI |
| **T20-21** | `4f80d36` | Outcome and interlock trips in the score: `RunMetrics.outcome` (a plain string, so `app/scoring` never imports `app/scenarios`) and a ninth criterion, `outcome`, weighted 101 (one more than the other eight) with an `outcome_penalty` table, so every succeeded run scores strictly above every failed or timed-out one. `PlantRuntime` records a `TripRecord(sim_time, tag)` per interlock latch and `trip_count` is their number; a trip-rated band with no interlock (pump_trip's P-101 flow LOLO) still feeds alarms, peak severity and time outside the envelope but is no trip. Rows stored before the metric read back with outcome `unknown`. Config 3.0.0. Built on Sonnet (PR #176) |
| **T20-15** | `8f123e4` | Score a finished run: `config/scoring.yaml` gains per-plant `plants` (excursion limits and throughput targets; olefins_lite and separator track drum level and pressure and liquid product at 50 GPM, not `P-101.flow`), read once at load and part of the run's fingerprint. `PlantRuntime` feeds the trackers from every published snapshot and refuses a point the plant lacks; `ScenarioRunner` computes `RunMetrics` and `Score` once at COMPLETE, and `public_result` shows them only beside the debrief (null when aborted). Every action counts as relevant for now. A malformed scoring file is a 400. Config 2.5.0; no golden moved. Built on Opus (PR #175). Backend only |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-4, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12-M18, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 5/6 | T10-1, T10-2, T10-3, T10-5, T10-6; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M20** Console Redesign | 22/24 | T20-1, T20-2, T20-3, T20-4, T20-5, T20-6, T20-7, T20-8, T20-9, T20-10, T20-11, T20-12, T20-13, T20-14, T20-15, T20-16, T20-17, T20-18, T20-19, T20-20, T20-21, T20-22; startable T20-23; T20-24 after it |
| M19 | 0 | - |

**150 of 157 tasks Complete.** Checkpoints A-D reached: T20-17 met D's last
gate ("loops reject an injected disturbance") on the separator plant.

## The next task

**Startable in M20:** **T20-23** (run time instead of the plant clock in
alarms, trends and the Home strip; T20-22 is merged, so it is free to start).
**T20-24** is the phone-width gameplay pass the user deferred (16 October 2026)
and waits on it. Also startable but
deferred: T10-4 and T11-4 (V1.1), T19-1 and T19-2 (V2).

**CP-G passed for the current console** (user's browser pass, 9 October 2026). The console is about to be redesigned, so the full pass (all six scenarios, both themes, phone width, a real alarm marker) is an acceptance criterion of that redesign rather than repeated on this console.

**Next by leverage:** **M20 Console Redesign** (added 9 October 2026, see [BUILD_PLAN.html](../../docs/BUILD_PLAN.html)). The accepted design is frozen in [prototype/console-redesign/](../../prototype/console-redesign/index.html): every M20 task ports from it, and it is never edited to follow production. **User priority (10 October 2026): the new design replaces the old pages first.** T20-6, T20-7 (schematic), T20-8 (ribbon and console shell), T20-9 (landing page), T20-10 (faceplates on the canvas), T20-11 (floating trend windows), T20-12 (alarm overlay), T20-13 (equipment actions), T20-14 (pause and resume) and T20-20 (units agree at load) are merged, so the old pages are replaced. The user is working desktop first: phone-width checks are deferred. T20-1 (units per point), T20-2 (plant description and a graphic per plant), T20-3 (interlock state in C4), T20-4 (loops on any point) and T20-6 (alarm palette and limit names) are merged. T20-5 (two-phase feed) is merged too, so every contract task is done. T20-17 (separator plant) is merged and met Checkpoint D's last gate, with PIC-101 left in MANUAL because olefins_lite stays untouched. `app/main.py` and `app/config.py` are free.

**Scheduling notes.** The spine lock is one global lock
([DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); it is free. **The container runs
exactly one Gunicorn worker** (T18-1) - `SessionRegistry` is per-process (R7); never scale it.
**Trips and alarms run in `TrainingSession` (T16-8), which `/console` (T16-9) now serves**:
`PlantRuntime` (T16-6) runs both around the engine step, `ScenarioRunner` (T16-7)
builds one, and `main.py` builds it for every cookie (T16-10); the pages are the landing page (T16-11) and the console (T16-9).
`RestartGate` (T11-3) must be updated before `TripSystem.update`, and only
blocks a restart for a trip listed in its `resets`; an ungated trip still lets a
standing lower-precedence RUN demand restart a machine the moment it releases.
Permissives have no C3 config key yet. A loop overridden by a trip is not tracked, so
its handback is not bumpless - also an unowned `Engine._control` change.

## Known interim behaviour - do not "fix" these in passing

Each is deliberate. Fixing one as a side effect of an unrelated task is out of
scope, and item 1 in particular reads like a bug and is not.

1. **The golden harness's machines run between two fixed battery limits with nothing in
   between.** The harness's single-machine plants (T16-10 moved them there) are one machine and two boundary nodes
   (750/750 psia, 50/50 psia). Flow reads above `max_flow` (compressor 331.7 vs
   120, pump 2236 vs 1200), the discharge valve strokes without changing flow,
   and spread equals the boundary difference, so temperature is flat. Equal
   boundaries keep an idle machine at zero flow. Equipment does not clamp -
   envelopes and alarms own that later.
   *Status:* **T7-1 built the valve** and a plant that uses it
   (`liquid_valve_train.yaml`); the golden plants are not rewired onto one, and must
   not be (the traces pin them).

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
   are not device attributes; a row is assembled from the snapshot (the golden
   harness does it for K-101 and P-101). The compressor's `temperature`
   became `temperature_at(suction, discharge)` because suction and discharge
   pressure are solver outputs. **Do not put a flow or a pressure back on a
   device.**

## Known technical debt (recorded, not scheduled)

- **Boundary temperatures are not in C3.** A config-loaded plant supplies 60 °F
  everywhere unless the caller passes `boundary_temperatures`; a node
  `temperature` field is an unowned C3 change. The compressor page's
  `temperature` still reads `temperature_at` from the 75 °F design suction, and
  C4 has no field for a transport that did not settle.
- **Instruments are not in C3.** `Engine(instruments=...)` is the only way to
  add a transmitter. A `controllers.pv` may name any published point with a
  unit (T20-4), so level and flow loops can bind; none is configured on
  `olefins_lite`. A `limits`, interlock or permissive `(tag, variable)` resolves
  against the published equipment row, solved points included (T9-5), and an
  unpublished one is a build error. Transmitters only have `bias` - no stuck
  or range-clamped reading (T13-2).
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
- **`ReliefValve`'s design checks depend on key order.** Each of
  `set_pressure` and `blowdown` checks `blowdown < set_pressure` against the
  other's current value, and `_apply_design` sets keys one at a time, so a valid
  pair far from the defaults (250/10) is refused at load: `{set_pressure: 8,
  blowdown: 2}` or `{blowdown: 300, set_pressure: 400}`. No plant configures a
  relief valve yet. The clean fix is the same design/configure hook as open
  decision 2. Unowned.
- **Contract-test discovery is import-order dependent**: `REGISTERED` in
  `tests/test_equipment_contract.py` is computed at import, so an `Equipment`
  subclass in a later-imported test module escapes the contract tests.
- Harmless oddities: `app/init.py` is a misnamed empty file (`app` is a
  namespace package); `static/style.css` is a 15-line stub until M16;
  `package.json` exists only for a TypeScript dev dependency.

## Open decisions with no owner

1. **Who owns a plant's RNG** - session, engine or plant? Undecided on purpose.
   `SeededRNG` requires a seed and there is deliberately no global stream,
   because a process-wide generator would leak draws between browser sessions.
   **It belongs to the first task that needs randomness.** `SeededRNG` also has
   no state save/restore, so neither `capture_state` (T12-1) nor a replay
   recording (T14-5) carries RNG state; that task must add it to both, and
   `tests/test_scenario_replay.py` fails the build once anything imports it.
2. **`Equipment.reset()` drops design values.** The loader applies a config's
   `design` by setting attributes after construction, but `reset()` restores
   state captured at the end of `__init__` - so a reset device returns to class
   defaults, not its configured design. Fixing it needs a design/configure hook
   on C1, a **spine change**, before anything relies on `reset()` for a loaded
   plant. `restore_state` (T12-1) does not use `reset()`, so it is unaffected.
   Needs an Opus decision.
3. **Every scenario runs on one schematic** (user, 9 October 2026). The
   six original scenarios load `olefins_lite`; T20-18's four load `separator`.
   The console follows the shown plant's graphic
   (`static/graphics/<plant id>.svg`, named by `GET /api/plant`). Free play
   stays on olefins_lite until the user decides (asked, unanswered).
4. **The plant has one controller** (user, 9 October 2026). `olefins_lite`
   configures only PIC-101; a real operator adjusts several loops: level,
   pressure, feed flow, compressor speed. The redesign's interaction work
   covers adjusting many loops; since T20-4 a level or flow loop is only a
   plant-config change. The separator (T20-17) now runs three loops in AUTO;
   olefins_lite still has only PIC-101.

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
