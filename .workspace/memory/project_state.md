# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 10 October 2026, at `ac4376e` (Merge T20-9, PR #163: landing page) - **this is a snapshot,
not a live pointer.** Run `git log ac4376e..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **3787 passed** · `python -m mypy` clean over 87 source files · no golden trace movement
**In flight:** nothing. The spine lock and the `app/main.py` lock are free.
No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T20-9** | `ac4376e` | Landing page: leads with one selected scenario (title, difficulty, time limit, briefing, inert plant thumbnail from `GET /` passing the graphic url); START loads then starts it and opens the console; the rest behind "Choose another scenario", the choice kept in localStorage behind try/catch; Free play and an empty Recent scenarios card; a return strip while a scenario stands. A run in progress is aborted only after an in-page confirmation (START and free play); a 409 makes the next click ask first. Decided by Opus, built on Sonnet (PR #163). Seen in headless Firefox on desktop, both themes; phone width unchecked (desktop first) |
| **T20-8** | `f4ac696` | Console shell and ribbon: one ribbon in `base.html` on the landing page and the console (brand, Home and Console, run title and state, run clock, Start and two-step Abort on the console only, Change scenario, alarm summary by priority with the unacknowledged count, theme toggle). `scenario.js` still runs the run controls (Escape disarms Abort from inside them; focus follows the visible control); new `ribbon.js` adds the run clock (result `elapsed_s`/`time_limit_s` carried forward by snapshot `sim_time`, never a wall clock) and the summary (worst priority's tint, a "N new" badge, links to the console's alarm section until T20-12). The console is a full-height workspace around the canvas with the old Controllers, Trends and Alarms sections scrolled below until T20-10 to T20-12. Prototype radius, shadow, spacing and motion tokens joined `tokens.css` (motion zero under reduced motion). Landing and console pass `plant_running`; a landing page nobody has opened the console from says "Plant not started". The console polls alarm history twice until T20-12. Decided by Opus (PR #162). Seen in headless Firefox and by the user on desktop; phone width unchecked (desktop first, user 10 Oct) |
| **T20-6** | `f6f7eb2` | Limits named by position: the outer limit reads HIHI/LOLO whether it alarms or trips (`AlarmManager` caps the suffix at two steps; `graphic.js` band text and `trends.js` rule labels follow; alarm ids and snapshot band labels `hi`/`hihi`/`hihihi` unchanged). Palette is two colours for now (user, 10 October): yellow for HI/LO, red for HIHI/LOLO, so `high` and the alarm band share `critical` and trip's red; `test_console_tokens.py` skips only those two pairs (`SHARED_BAND_PAIR`, `SHARED_FILL_PAIR`, delete when orange returns). Rejected-command styling uses `--alarm-high-mark` and now reads red. Seen in a real browser (yellow and red) |
| **T20-2** | `ddaba90` | Plant description: `GET /api/plant` (`app/api/plant.py`) returns the shown plant's id (the plant file stem), its graphic (`static/graphics/<id>.svg`, or null) and, per device, `kind` (a `KIND` table beside `VISIBLE`), `service`, operator-visible `points` plus its branch's stream, `actions` and `driven_by` (a loop-driven device lists none); per loop `service`, `pv` point and `out`; per interlock only the devices it acts on. Built once per `PlantRuntime`, read with the id under `step_lock`. C3 equipment and controllers entries take an optional `service` (config 2.1.0). `plant.svg` is now `olefins_lite.svg`; `ProcessGraphic.follow` mounts the named graphic, re-mounts only when the plant id changes (the scenario bar's `onChange` triggers a refresh) and retries a failed load on the next snapshot. A swap to a second graphic is tested in Node only until T20-17. Decided by Opus (PR #159) |
| **T20-1** | `d975feb` | Units per point: `app/plant/units.py` `point_units` names each trend point's unit once per `PlantRuntime` (equipment fields from a per-class `FIELD_UNITS` allowlist, node pressure psia, loop pv/sp its `pv_unit` and out `fraction`, stream and machine flow the domain's unit from the coupling's classification, never a name); disagreeing sources raise at runtime build, an unclassified domain gets `''`. `GET /api/trend/points` adds `units`. Follow-up T20-20: refuse a domain whose units disagree at load. Decided by Opus (PR #158) |
| **T10-6** | `f8911a7` | Point descriptors: a per-class `DESCRIPTORS` table in `app/api/visibility.py` (with `descriptor(device, field)`) names a pump's or compressor's `inlet_pressure` and `outlet_pressure` as suction and discharge pressure, else the field with spaces. `PlantRuntime._observe` builds the alarm `pv` from it, so a K-101 alarm reads `K-101 discharge pressure HIHI` (its alarm id changes); `GET /api/trend/points` adds `descriptors` (one per listed point, via `trend_descriptors()` on `PlantRuntime`, `ScenarioRunner` and `TrainingSession`; `create_trend_blueprint` takes a fourth callable); the trend legend and add-a-pen list show `<id> <descriptor>`, the point id staying the key. Decided by Opus (PR #157). Not seen in a real browser |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-3, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12-M18, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 5/6 | T10-1, T10-2, T10-3, T10-5, T10-6; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M20** Console Redesign | 5/20 | T20-1, T20-2, T20-6, T20-8, T20-9; startable T20-3, T20-4, T20-5, T20-7, T20-14, T20-15, T20-20 |
| M19 | 0 | - |

**133 of 153 tasks Complete.** Checkpoints A-C reached. Checkpoint **D** (M8)
needs only its "loops reject an injected disturbance" gate, which T20-17 meets
on the separator plant.

## The next task

**Startable in M20:** T20-3 (spine), T20-4 (spine), T20-5, T20-7, T20-14,
T20-15 and T20-20 - the `startable` field of
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json). All are Opus but
T20-7, T20-14 and T20-20 (Sonnet). T20-3 and T20-4 share the one spine lock, so run them one at a
time. Also startable but deferred: T10-4 and T11-4 (V1.1), T19-1 and T19-2 (V2).

**CP-G passed for the current console** (user's browser pass, 9 October 2026). The console is about to be redesigned, so the full pass (all six scenarios, both themes, phone width, a real alarm marker) is an acceptance criterion of that redesign rather than repeated on this console.

**Next by leverage:** **M20 Console Redesign** (added 9 October 2026, see [BUILD_PLAN.html](../../docs/BUILD_PLAN.html)). The accepted design is frozen in [prototype/console-redesign/](../../prototype/console-redesign/index.html): every M20 task ports from it, and it is never edited to follow production. **User priority (10 October 2026): the new design replaces the old pages first.** T20-6, T20-8 (ribbon and console shell) and T20-9 (landing page) are merged, so the old pages are replaced. Run T20-14 (pause and resume, Sonnet, takes the `app/main.py` lock) or T20-7 (schematic restyle, Sonnet) next. The user is working desktop first: phone-width checks are deferred. T20-3, T20-4 and T20-5 run alongside, and T20-10 to T20-12 follow. T20-1 (units per point), T20-2 (plant description and a graphic per plant) and T20-6 (alarm palette and limit names) are merged. The contract tasks left: T20-3 (interlock state in C4, spine), T20-4 (loops on any point, spine) and T20-5 (two-phase feed ADR). Checkpoint D's remaining gate is now met on the separator plant (T20-17), with PIC-101 left in MANUAL because olefins_lite stays untouched. `app/main.py` and `app/config.py` are free.

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
  add a transmitter. A `controllers.pv` resolves against a node id (T8-3), so
  only pressure loops can bind; a flow loop on `P-101.flow` is now possible but
  unbuilt. A `limits`, interlock or permissive `(tag, variable)` resolves
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
   but only `olefins_lite.svg` exists; the second schematic is T20-17 (the
   separator), which is also the first real swap.
4. **The plant has one controller** (user, 9 October 2026). `olefins_lite`
   configures only PIC-101; a real operator adjusts several loops: level,
   pressure, feed flow, compressor speed. The redesign's interaction work
   covers adjusting many loops, but more loops is also a plant-config and
   backend change: `controllers.pv` resolves only a node id (see "Known
   technical debt"), so a level or flow loop needs that widened first. Owned by T20-4 (loops on any point) and T20-17 (three loops on the separator).

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
