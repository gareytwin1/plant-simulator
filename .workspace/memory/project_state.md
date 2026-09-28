# Project State

What is true **right now**; stable rules are in [AGENTS.md](../../AGENTS.md).
Refreshed at each merge by `merge-task`, one line per merged task - the
regrowth rule is in [.claude/rules/docs.md](../../.claude/rules/docs.md).

---

## Right now

**Last state refresh:** 28 September 2026, at `bc27240` (Merge T10-3: Add
bounded alarm history with acknowledge, PR #109) - **this is a snapshot,
not a live pointer.** Run `git log bc27240..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **2127 passed** · `python -m mypy` clean over 58 source files · no golden trace movement
**In flight:** T16-2 is Ready for Review in the live artifact; no PR yet.
**No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (one line each; detail in the PR and
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T10-3** | `bc27240` | Bounded alarm history + acknowledge API, `app/alarms/history.py`, `app/api/alarms.py`; additive `AlarmManager.is_acknowledged()` landed in `app/alarms/manager.py`, outside this task's declared files - coordinate with T10-4 (PR #109) |
| **T11-2** | `868f484` | Trip actions, `app/safety/actions.py`: `TripSystem` posts interlock demands on the arbiter; not yet called from `Session`/`Scheduler` (PR #108) |
| **T14-2** | `eb195c0` | Trigger evaluator, `app/scenarios/triggers.py` (PR #106) |
| **T11-1** | `994b30e` | Interlock definitions and evaluator, `app/safety/interlocks.py`; 3 interlocks in `olefins_lite.yaml` (PR #105) |
| **T17-1** | `b36af0d` | Ring-buffer historian, `app/historian/buffer.py` (PR #101) |
| **T13-5** | `65458ee` | Physics/disturbance layer isolation guard, `tests/test_layer_isolation.py` (PR #100) |

**ADRs on `main`:** [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md)
(+ Amendment 1) and [0002](../../docs/ADR_0002_TYPED_PORTS.md) (+ Amendments
1-3, all implemented). Read the ADRs themselves; do not summarise them here.

## Milestone progress

Complete: **M0-M7, M9, MR**. Open:

| Milestone | Done | Complete / startable |
|---|---|---|
| **M8** PID Controllers | 5/6 | T8-5 startable (V1.1-deferred) |
| **M10** Alarms | 3/5 | T10-1, T10-2, T10-3; T10-4 startable (V1.1-deferred), T10-5 startable |
| **M11** Interlocks and Trips | 2/4 | T11-1, T11-2; T11-3 startable, T11-4 startable (V1.1-deferred) |
| **M13** Malfunctions | 3/5 | T13-1, T13-2, T13-5; T13-3, T13-4 startable |
| **M14** Scenario Engine | 2/6 | T14-1, T14-2; T14-3 startable |
| **M15** Action Log and Scoring | 1/4 | T15-1; T15-4 startable (V1.1-deferred) |
| **M17** Historian and Trends | 1/4 | T17-1; T17-2 startable (V1.1-deferred) |
| **M18** Deployment | 1/5 | T18-2 |
| M12, M16, M19 | 0 | - |

**82 of 115 tasks Complete.** Checkpoints A-C reached. Checkpoint **D** (M8)
needs only its "loops reject an injected disturbance" gate: PIC-101 switched to
AUTO in `olefins_lite.yaml`, which T8-6 enabled but no task owns yet.

## The next task

**17 tasks are startable** - list them from
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json) (`startable`
field). All are Sonnet. T8-5, T10-4, T11-4, T15-4 and T17-2 are V1.1-deferred. No startable task needs the spine lock;
T12-1 and T18-5 add new isolated modules under `app/engine/` (satellite work).

**Scheduling notes.** The spine lock is one global lock
([DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); it is free. `rng.py` is
spine, so RNG state save/restore (T12-1 or T14-5) takes it. **T18-1 must run
exactly one Gunicorn worker** - `SessionRegistry` is per-process (R7).
**Trips do not run in a live session yet**: nothing in `Session`/`Scheduler`
calls `TripSystem.update`, and wiring it in is a spine change no task owns.
T11-3 (permissives) builds on T11-2's `"<tag>.run"` arbiter output; a standing
lower-precedence RUN demand restarts a machine the moment its trip releases
(see `app/safety/actions.py`). A loop overridden by a trip is not tracked, so
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
   no state save/restore, which T12-1 and T14-5 will need.
2. **`Equipment.reset()` drops design values.** The loader applies a config's
   `design` by setting attributes after construction, but `reset()` restores
   state captured at the end of `__init__` — so a reset device returns to class
   defaults, not its configured design. Fixing it needs a design/configure hook
   on C1, a **spine change**, before anything relies on `reset()` for a loaded
   plant. Also relevant to T12-1. Needs an Opus decision.

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
