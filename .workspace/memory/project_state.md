# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 7 October 2026, at `d9d00d2` (Merge T17-3, PR #153: trend API) - **this is a snapshot,
not a live pointer.** Run `git log d9d00d2..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **3545 passed** · `python -m mypy` clean over 85 source files · no golden trace movement
**In flight:** nothing. The spine lock and the `app/main.py` lock are free.
No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T17-3** | `d9d00d2` | Trend API: `PlantRuntime` owns a `Historian` and records every operator-view point (`app/historian/points.py`, `<id>.<field>`, allowlisted) each step; `GET /api/trend?tags=&from=&to=&max_points=` slices then decimates, refuses over-limit `max_points` (2-2000) and tags (8) with a 400, and `GET /api/trend/points` lists points; history follows the shown plant (free play, a run, abort starts fresh); `TREND_*` config (1 s, 1800 samples, about 350 MB worst case at `MAX_SESSIONS`); `Historian` throttle tolerates 1e-9 of float drift (PR #153) |
| **T17-2** | `67b8ee8` | Peak-preserving decimation: `app/historian/decimate.py` `decimate(samples, max_points)` splits a history into `max_points // 2` index buckets and keeps each bucket's first minimum and last maximum as real timestamped samples, NaN skipped; output is `max_points` long when even, `max_points - 1` when odd; nothing calls it yet (T17-3 will) (PR #152) |
| **T16-4** | `f90be61` | Controller faceplates: `static/js/faceplate.js` renders one faceplate per `controllers` row of the operator view (tag and mode, PV/SP/OUT, an output bar, MAN/AUTO, setpoint and output entry in percent of range, MANUAL only, and Kp/Ki/Kd tuning locked unless the loop is `tunable`). Every command goes through `POST /api/action` and a server refusal is shown as worded; a snapshot never overwrites typing, and one with no `controllers` section is ignored. `console.html` fans `onSnapshot` out to the graphic and the faceplates, each in its own try/catch. Seen only in headless Firefox (PR #150) |
| **T16-14** | `cdab8f3` | Operator loop actions: a loop tag is an `/api/action` target (`LOOP_ACTIONS`: `manual`, `auto`, `set_setpoint`, `set_output` in MANUAL within the output range, `set_kp`/`set_ki`/`set_kd` on a loop whose config says `tunable`); a device a loop drives refuses direct actions, naming the loop, because the loop re-posts its demand every step. `apply_action` takes `loops=` as a required keyword and `PlantRuntime.act` passes `engine.loops`. `PID.retune` keeps a Kp or Ki change bumpless in AUTO (a new ki of 0 is not). C3 gains optional `tunable` (default false; PIC-101 is tunable; config 1.1.0) and each `controllers` row gains `kp`, `ki`, `kd`, `out_min`, `out_max`, `tunable`, `pv_unit`. No setpoint upper bound: no config carries a range (PR #149) |
| **T18-8** | `bcaae4c` | Rate limit behind a reverse proxy: `config.API_TRUSTED_PROXIES` (env `PLANT_TRUSTED_PROXIES`, comma-separated addresses or CIDRs, empty by default = no trust, exactly the T18-3 behaviour) lists the proxies whose `X-Forwarded-For` is believed. `validate.client_key` keys on the peer unless it is trusted, then walks the header right to left past trusted hops; a malformed entry stops the walk at the hop that wrote it. An allow-list, not `ProxyFix`, so a client reaching Gunicorn directly cannot spoof its key. A bad or straddling (`::/0`) entry fails at startup. Only `X-Forwarded-For` is read and `request.remote_addr` is not rewritten, so access logs still show the proxy. `docker-compose.yml` passes the variable through (PR #148) |
| **T16-3** | `23dd190` | Process graphic: `static/graphics/plant.svg` draws the reference train and `static/js/graphic.js` binds the operator view to it through `data-*` attributes only (`data-tag` sets state and worst envelope band, `data-bind` a value, `data-flow` pipe direction, `data-fill` the vessel level), so new equipment is new markup and no JavaScript; unknown tags show `--`, and snapshot equipment the SVG does not draw is listed. Mounted into `/console` by PR #147 (`2274eb9`, a follow-up with no task ID; the graphic is fed from `connection.js`'s `onSnapshot`, and the page was seen in headless Firefox, which does not close CP-G). There is no tripped equipment state (the snapshot does not publish interlock trips): a trip shows as the envelope trip band and a stopped machine as STOP. Rendered only in headless Firefox, not on the console page (PR #146) |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-3, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M9, M12-M16, M18, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M10** Alarms | 4/5 | T10-1, T10-2, T10-3, T10-5; T10-4 startable (V1.1-deferred) |
| **M11** Interlocks and Trips | 3/4 | T11-1, T11-2, T11-3; T11-4 startable (V1.1-deferred) |
| **M17** Historian and Trends | 3/4 | T17-1, T17-2, T17-3; T17-4 startable |
| M19 | 0 | - |

**124 of 130 tasks Complete.** Checkpoints A-C reached. Checkpoint **D** (M8)
needs only its "loops reject an injected disturbance" gate: PIC-101 switched to
AUTO in `olefins_lite.yaml`, which T8-6 enabled but no task owns yet.

## The next task

**Startable: T17-4 (Trend display, Sonnet, `static/js/trends.js`; draws from `GET /api/trend/points` and `/api/trend`), plus the deferred tasks** - list them from
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json) (`startable`
field). The rest are deferred: T10-4 and T11-4 are V1.1-deferred;
T19-1 and T19-2 are deferred further still, to **V2**.

**Next by leverage:** merge PR #151, then **close CP-G** by viewing `/console` in a real browser with the controls present (`flask --app app.main run`, load `pump_trip` for alarms); only headless Firefox has seen it. Then Checkpoint D's remaining gate (PIC-101 in AUTO, which T8-6 enabled and no task owns). `app/main.py` and `app/config.py` are free.

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
