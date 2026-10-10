# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 9 October 2026, at `d975feb` (Merge T20-1, PR #158: units per point) - **this is a snapshot,
not a live pointer.** Run `git log d975feb..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **3689 passed** · `python -m mypy` clean over 86 source files · no golden trace movement
**In flight:** nothing. The spine lock and the `app/main.py` lock are free.
No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T20-1** | `d975feb` | Units per point: `app/plant/units.py` `point_units` names each trend point's unit once per `PlantRuntime` (equipment fields from a per-class `FIELD_UNITS` allowlist, node pressure psia, loop pv/sp its `pv_unit` and out `fraction`, stream and machine flow the domain's unit from the coupling's classification, never a name); disagreeing sources raise at runtime build, an unclassified domain gets `''`. `GET /api/trend/points` adds `units`. Follow-up T20-20: refuse a domain whose units disagree at load. Decided by Opus (PR #158) |
| **T10-6** | `f8911a7` | Point descriptors: a per-class `DESCRIPTORS` table in `app/api/visibility.py` (with `descriptor(device, field)`) names a pump's or compressor's `inlet_pressure` and `outlet_pressure` as suction and discharge pressure, else the field with spaces. `PlantRuntime._observe` builds the alarm `pv` from it, so a K-101 alarm reads `K-101 discharge pressure HIHI` (its alarm id changes); `GET /api/trend/points` adds `descriptors` (one per listed point, via `trend_descriptors()` on `PlantRuntime`, `ScenarioRunner` and `TrainingSession`; `create_trend_blueprint` takes a fourth callable); the trend legend and add-a-pen list show `<id> <descriptor>`, the point id staying the key. Decided by Opus (PR #157). Not seen in a real browser |
| **T9-5** | `91c5b46` | Device points in the snapshot: `Engine._truth` adds `flow`, `inlet_pressure` and `outlet_pressure` to the equipment row of every device that sits in exactly one branch (from that branch and its two nodes; by direction, never port name; the device holds none, and a device's own field of that name wins). A limit, interlock condition, restart-gate permissive or plant-state gate naming an unpublished field now raises at construction instead of warning. PSHH-101 and the `K-101.outlet_pressure` (renamed from `discharge_pressure`) and `P-101.flow` limits evaluate live; K-101 `hi` moved 320 to 330 psia because the cold start peaks at 323. `VISIBLE` shows the points for pumps and compressors, so they are trend pens with bands. Config 2.0.0. A stopped P-101 now alarms `P-101 flow LOLOLO` (no check valve). Decided by Opus (PR #156) |
| **T16-15** | `edf8e8f` | Scenario run controls: a Scenario bar at the top of `/console` (`static/js/scenario.js`, `static/css/scenario.css`) shows the mode, title and phase and offers Start run (loaded) and a two-click Abort run (running); complete, aborted and free play get a "Choose a scenario" link. It polls `GET /api/scenario/result` (409 = free play), follows phase and title, discards a poll that began before a click, words every refusal itself by status code and resyncs after any failed or unreadable response. `GET /console` passes the standing, title and `PHASE_LABELS`; the landing page links back to the console. Placement decided by Opus (console only; supersedes T16-11's Start/Abort on the landing page). Seen in a real browser for `pump_trip` only (PR #155) |
| **T17-4** | `2fc84e4` | Trend display: `static/js/trends.js` draws multi-pen inline SVG on `/console` (a Trends section fed from `onSnapshot`): selectable pens (at most `max_tags`), spans of 1, 5, 10 or 30 simulated minutes, each pen scaled to its own range, the focused pen's axis, envelope bands as tints plus labelled HI/HIHI/HIHIHI and LO/LOLO/LOLOLO rules, and alarm markers from `/api/alarms/history`. `GET /api/trend/points` now also returns `limits` (the bounds the engine evaluates, which since T9-5 include `K-101.outlet_pressure` and `P-101.flow`), `max_tags` and `max_points`. Every 2 s tick refetches (no paused-plant skip, so a history swap redraws), skips while one is in flight or the tab is hidden, and requests time out. No units per point (own Opus task). Seen only in headless Firefox, with a synthetic marker (PR #154) |
| **T17-3** | `d9d00d2` | Trend API: `PlantRuntime` owns a `Historian` and records every operator-view point (`app/historian/points.py`, `<id>.<field>`, allowlisted) each step; `GET /api/trend?tags=&from=&to=&max_points=` slices then decimates, refuses over-limit `max_points` (2-2000) and tags (8) with a 400, and `GET /api/trend/points` lists points; history follows the shown plant (free play, a run, abort starts fresh); `TREND_*` config (1 s, 1800 samples, about 350 MB worst case at `MAX_SESSIONS`); `Historian` throttle tolerates 1e-9 of float drift (PR #153) |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-3, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12-M18, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 5/6 | T10-1, T10-2, T10-3, T10-5, T10-6; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M20** Console Redesign | 1/20 | T20-1; startable T20-2, T20-3, T20-4, T20-5, T20-6, T20-15, T20-20 |
| M19 | 0 | - |

**129 of 153 tasks Complete.** Checkpoints A-C reached. Checkpoint **D** (M8)
needs only its "loops reject an injected disturbance" gate, which T20-17 meets
on the separator plant.

## The next task

**Startable in M20:** T20-2, T20-3 (spine), T20-4 (spine), T20-5, T20-6 and
T20-15, plus T20-20 (added after the last regeneration) - the `startable` field of
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json). All are Opus but
T20-6 and T20-20 (Sonnet). T20-3 and T20-4 share the one spine lock, so run them one at a
time. Also startable but deferred: T10-4 and T11-4 (V1.1), T19-1 and T19-2 (V2).

**CP-G passed for the current console** (user's browser pass, 9 October 2026). The console is about to be redesigned, so the full pass (all six scenarios, both themes, phone width, a real alarm marker) is an acceptance criterion of that redesign rather than repeated on this console.

**Next by leverage:** **M20 Console Redesign** (added 9 October 2026, see [BUILD_PLAN.html](../../docs/BUILD_PLAN.html)). The accepted design is frozen in [prototype/console-redesign/](../../prototype/console-redesign/index.html): every M20 task ports from it, and it is never edited to follow production. **User priority (10 October 2026): the new design replaces the old pages first.** Run T20-6, then T20-8 (ribbon and console shell), then T20-9 (landing page), in that order and all on Sonnet; none waits on a contract task. T20-2, T20-3, T20-4 and T20-5 run alongside, and T20-7 and T20-10 to T20-12 follow T20-2. T20-1 (units per point) is merged. The contract tasks: T20-2 (plant description and a graphic per plant), T20-3 (interlock state in C4, spine), T20-4 (loops on any point, spine) and T20-5 (two-phase feed ADR), with T20-6 (alarm palette and limit names) in parallel. Checkpoint D's remaining gate is now met on the separator plant (T20-17), with PIC-101 left in MANUAL because olefins_lite stays untouched. `app/main.py` and `app/config.py` are free.

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
   scenarios load `olefins_lite`, and the console hard-wires one graphic
   (`static/graphics/plant.svg` in `templates/console.html`). Acceptable for
   now; expanding training later means scenarios on different plants, each
   with its own schematic, so the graphic has to follow the scenario's plant. Owned by T20-2 (a graphic per plant) and T20-17
   (the separator).
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
