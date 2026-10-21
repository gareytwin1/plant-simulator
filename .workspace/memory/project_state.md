# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 20 October 2026, at `c5063a0` (Merge T20-28, PR #186: ribbon run clock from `started_at`) - **this is a snapshot,
not a live pointer.** Run `git log c5063a0..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **4364 passed** · `python -m mypy` clean over 89 source files · no golden trace movement
**In flight:** nothing. The spine lock and the `app/main.py` lock are free.
No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T20-28** | `c5063a0` | The ribbon's run clock reads the later of the poll's `elapsed_s` and the latest snapshot's `sim_time - started_at`, capped at the limit, so it never runs ahead of the plant (it was up to a second ahead, 3 of 25 samples in headless Firefox). Anchor and ratchet removed from `createRunClock`; `runTimes` carries `startedAt`. Known gap: after Try again on the same scenario a stale in-flight snapshot can show the old time for one snapshot; the fix is a run id in the snapshot (C4), unowned. No golden moved. Built on Opus, merged on Sonnet (PR #186). Browser-checked at desktop width |
| **T20-27** | `7029111` | Escape from inside a trend window (title bar, Minimise, Close, span buttons) closes it and returns focus to its opener; the key stops at the window, so the canvas's page-wide listener does not also close a faceplate behind it. Reproduced in headless Firefox first. No golden moved. Built on Opus, merged on Sonnet (PR #185). Browser-checked at desktop width |
| **T20-26** | `bdecfae` | Free play runs the separator at `separator_normal` (`config.FREE_PLAY_PLANT`/`FREE_PLAY_CONDITION`), with FIC-301, LIC-301 and PIC-301 in AUTO: the user's decision of 19 October 2026. The 182 tests that read olefins_lite tags through free play pin it with a module-scoped `olefins_free_play` fixture (helper in `tests/free_play.py`). No golden moved. Built on Opus, merged on Sonnet (PR #184). Browser-checked at desktop width |
| **T20-25** | `098f867` | Try again keeps keyboard focus after a refusal: the button is disabled during its request, which drops focus to the page body, so Escape closed a faceplate instead of the result panel. `tryAgain` now gives focus back when it was dropped and the panel is still open. Found in the desktop re-check of T20-22's panel. Built on Sonnet (PR #183) |
| **T20-23** | `cc078a7` | Run time, not the plant clock, while a scenario stands: `public_result` gains `started_at` (the plant clock at run time zero, every phase but idle; C5 additive, kept out of `as_dict` so the replay end-state digest is unchanged), and `static/js/clock.js` formats a time from that one origin, which the console seeds from the page and the ribbon's scenario poll moves. The alarm list's Raised column, the trend windows (axis, labels, markers, request window; ticks on round run times; negative before the run) and the Home strip's "Run time mm:ss" read through it; free play is unchanged. `Standing` carries `elapsed_s` and `started_at` instead of `sim_time`. No golden moved. Built on Opus (PR #182). Browser-checked on pump_trip; the debrief panel's Escape, focus and failed Try again were not re-checked |
| **T20-22** | `d8edf4a` | Result and debrief panel on the console: `static/js/debrief.js` reads the ribbon's existing scenario poll (`onResult`, no second poll) and, once a run is complete or aborted, opens a non-modal dialog over the canvas with the outcome in words and a glyph, run time, the score and what counted against it ("in full", "partly", "a little", worded from a table keyed by `config/scoring.yaml`'s penalty names, which a test keeps in step), and the debrief. It opens itself once per finished run (keeping focus where an operator is typing), a ribbon Result button reopens it, Try again loads and starts the same scenario, and nothing of the outcome or score renders while a run is loaded or running. The run controls gain `adopt`, `say`, `resync` and a shared `timedFetch`. No golden moved. Built on Opus, review fixes on Sonnet (PR #181). Browser-checked one scenario per plant, pass and fail, before the review fixes |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-4, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12-M18, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 5/6 | T10-1, T10-2, T10-3, T10-5, T10-6; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M20** Console Redesign | 27/28 | T20-1 to T20-23 and T20-25 to T20-28; T20-24 (phone width, deferred by the user) is startable |
| M19 | 0 | - |

**155 of 161 tasks Complete.** Checkpoints A-D reached: T20-17 met D's last
gate ("loops reject an injected disturbance") on the separator plant.

## The next task

**Startable in M20:** **T20-24**, the phone-width gameplay pass the user
deferred (16 October 2026) until desktop is right. Also startable but
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
   moved to the separator (user, 19 October 2026; T20-26).
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
