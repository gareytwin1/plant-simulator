# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 14 October 2026, at `8e1e697` (Merge T20-17, PR #174: separator plant with three loops in AUTO, limits and interlocks) - **this is a snapshot,
not a live pointer.** Run `git log 8e1e697..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **4098 passed** · `python -m mypy` clean over 88 source files · no golden trace movement
**In flight:** nothing. The spine lock and the `app/main.py` lock are free.
No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T20-17** | `8e1e697` | Separator plant (`config/plants/separator.yaml`, beside an untouched olefins_lite): FV-301 a liquid feed carrying 0.2 SCFM/GPM into V-301, and FIC-301, LIC-301 and PIC-301 in AUTO (LIC and PIC retuned from the prototype's gains, which swing and hunt on this drum). LSHH-301 and PSHH-301 close FV-301, LSLL-301 closes LV-301; a lost valve signal defeats the trip on that valve (no separate shutdown valve). `separator_normal.json` saves the design point (new recipe in `scripts/initial_conditions.py`); `separator.svg` in the accepted style. Meets Checkpoint D's last gate. Config 2.4.0; no golden moved. Free play stays on olefins_lite until the user decides (asked, unanswered). Built on Opus (PR #174). Phone width unchecked |
| **T20-5** | `c1e2713` | Two-phase feed (ADR 0002 Amendment 4, decided with the user): one valve and one line, the feed solved as liquid, and `Vessel.feed_gas_ratio` (SCFM/GPM, malfunction-writable) releasing `ratio * max(0, inlet_flow)` into the gas balance as `feed_gas_flow`. A ratio above zero is refused on a vessel that cannot release it (no vapor attachment, or a one-direction liquid node a branch runs the other way through) at build, at scenario arming and in `restore_state`. Initial conditions gained `_feed_gas_ratio: 0.0`; config 2.3.0; no golden moved. Decided and built on Opus (PR #173) |
| **T20-4** | `5e45ae6` | Loops measure any published point: a `controllers.pv` is a bare node id (its pressure, as before) or `<id>.<field>` on an equipment row, node or stream, resolved in `load_loops` against the engine's indicated view (`Engine.from_plant` now binds loops after building the engine). An unknown, ambiguous, unpublished, non-numeric or unitless point is a load error naming the entry. `pv_unit` comes from `app.plant.units.unit_lookup`; setpoints are checked against it at load and on `set_setpoint` (fraction [0, 1], °F above absolute zero, otherwise non-negative). The faceplate shows a fraction loop in percent. Config 2.2.0. Decided and built on Opus (PR #172). Phone width unchecked |
| **T20-13** | `339d106` | Equipment actions on the faceplate: a device panel offers exactly the actions `/api/plant` lists - Start and Stop (disabled while running / stopped, read from the run flag) and a percent target posted as a fraction, with actual and target on one bar; a loop-driven device offers only its loop. A device an interlock holds gets a Reset for each tripped, manual-reset interlock (`canvas.js` passes `/api/plant`'s `interlocks`). Feedback reads pending, then accepted or the server's words. `PlantRuntime.act` now refuses a reset that would do nothing (not tripped, auto-reset, condition still holds) with a `ValueError` and does not log it. `olefins_lite.svg` valves carry a state badge, shown only as `✖ TRIP` while an interlock holds them. The browser-script guard exempts the run verbs `start` and `stop`. Built on Sonnet (PR #171). Seen in headless Firefox on desktop, both themes; phone width unchecked (desktop first) |
| **T20-3** | `d96b4d1` | Interlock state in C4 (additive): `Snapshot.interlocks`, `{tag: {state, reset, devices}}` (state `normal`/`pending`/`tripped`, reset `auto`/`manual`, devices the tags its actions name; never the condition). `Engine` builds it empty; `PlantRuntime` fills it from `TripSystem.status()` after each step and on `snapshot()`, and `ScenarioRunner` reads its snapshots through the runtime. The operator view passes it unchanged. `graphic.js` draws a device a tripped interlock holds as `✖ TRIP` (a pending one holds nothing; an unpublished device stays unknown); the faceplate badge and `olefins_lite.svg` style it from `--equip-tripped-*`. Decided and built on Opus (PR #170). Seen in headless Firefox on desktop; phone width unchecked (desktop first) |
| **T20-12** | `98ad205` | Alarm overlay: the ribbon's alarm summary (a button on the console, a link to it elsewhere) opens a modal dialog over the workspace, which is inert behind it, replacing the alarm banner and the Alarms section. Filters by priority (with per-priority counts), acknowledgement, equipment and text; per-row Acknowledge, Show trend (only for an alarm with a point) and Locate; Acknowledge all shown acknowledges exactly the unacknowledged rows on screen. Closes by button, Escape (clears a filled search first), click outside or the ribbon button; focus moves in and back to the ribbon button, Tab wraps. The history is fetched on open and polled only while open. C6 `data` gains `point` (`<tag>.<variable>`, additive, set in `PlantRuntime`); `canvas.js` gains `locate` and `showTrend`, and a Show trend or Locate that finds nothing reopens the overlay saying so. Raised shows plant sim time, not run-relative. Built on Sonnet (PR #169). Seen in headless Firefox on desktop, both themes; phone width unchecked (desktop first) |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-4, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12-M18, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 5/6 | T10-1, T10-2, T10-3, T10-5, T10-6; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M20** Console Redesign | 16/21 | T20-1, T20-2, T20-3, T20-4, T20-5, T20-6, T20-7, T20-8, T20-9, T20-10, T20-11, T20-12, T20-13, T20-14, T20-17, T20-20; startable T20-15, T20-18; T20-21 after T20-15 |
| M19 | 0 | - |

**144 of 154 tasks Complete.** Checkpoints A-D reached: T20-17 met D's last
gate ("loops reject an injected disturbance") on the separator plant.

## The next task

**Startable in M20:** T20-15 (Opus) and T20-18 (Sonnet) - the
`startable` field of [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json).
Neither needs the spine lock. T20-18 (separator scenarios) has a gap to settle
first: two of its four faults are a lost valve signal, and no C8 malfunction
can drop one (`WRITABLE[ControlValve]` is only `capacity` and `min_position`),
so that mechanism is a contract question to escalate, not invent.
**T20-21** (Sonnet, added 14 October 2026 after the last regeneration) follows
T20-15 and gates T20-16: it puts the outcome in the score and counts only
interlock trips, because T20-15's score lets a failed run reach 100 and
charges pump_trip's premise as a trip. Also startable but deferred: T10-4 and T11-4 (V1.1), T19-1 and T19-2 (V2).

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

## Known interim behaviour — do not "fix" these in passing

Each is deliberate. Fixing one as a side effect of an unrelated task is out of
scope, and item 1 in particular reads like a bug and is not.

1. **The golden harness's machines run between two fixed battery limits with nothing in
   between.** The harness's single-machine plants (T16-10 moved them there) are one machine and two boundary nodes
   (750/750 psia, 50/50 psia). Flow reads above `max_flow` (compressor 331.7 vs
   120, pump 2236 vs 1200), the discharge valve strokes without changing flow,
   and spread equals the boundary difference, so temperature is flat. Equal
   boundaries keep an idle machine at zero flow. Equipment does not clamp —
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
3. **Every scenario runs on one schematic** (user, 9 October 2026). All six
   scenarios load `olefins_lite`. Since T20-2 the console follows the shown
   plant's graphic (`static/graphics/<plant id>.svg`, named by `GET /api/plant`),
   and `separator.svg` exists since T20-17, but no scenario loads the
   separator until T20-18, which is also the first real swap.
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
