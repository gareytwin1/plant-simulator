# Project State

What is true **right now**. This file goes stale by design; the stable rules are
in [AGENTS.md](../../AGENTS.md) and the architecture is in
[ARCHITECTURE.md](../../docs/ARCHITECTURE.md).

**Refresh this file when a task merges, and keep it lean:** a merged task gets
its **one-line** entry here, and its full completion note goes in
[BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json). Per-task handoff sections do
not accumulate here — that is what grew this file to 1,086 lines once already.
When the recent-merges table below passes ~6 rows, drop the oldest — it is
already in BUILD_PLAN_STATUS.json and does not need a second home.

---

## Right now

**Last state refresh:** 27 September 2026, at `6cafbd2` (Merge T15-1: Add
operator action log, PR #103) - **this is a snapshot,
not a live pointer.** Run `git log 6cafbd2..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **1803 passed** · `python -m mypy` clean over 49 source files · no golden trace movement
**In flight:** nothing.
**No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T15-1** | `6cafbd2` | Operator action log (C6 event) and the C5 `/api/action` endpoint. `ActionLog` (`app/scoring/actionlog.py`) records every operator input as an append-only, sim-timed `ActionEvent` (always `Priority.LOW`, reusing `app.alarms.manager.Priority`). `apply_action` (`app/api/action.py`) validates target/action against an explicit per-device-class allowlist (`ACTIONS`, mirroring `WRITABLE`/`OUTPUTS`) before calling the device and logging - a JSON int value is normalized to float once, before both the call and the log, so the two never disagree on type. `create_action_blueprint` takes its `Engine`/`ActionLog` as injected callables rather than `flask.g`; wiring into `app/main.py`'s session lifecycle is left unowned. Unblocks T18-3 |
| **T8-6** | `3733819` | Controller action (C3 contract change). A `controllers` entry takes an optional `action`, `DIRECT` or `REVERSE`, by the ISA convention (direct: output rises as the measurement rises). It flips only the error's sign, so anti-windup, `track()` and bumpless transfer hold in both; omitted means `REVERSE`, bit-identical to before. PIC-101 is configured `DIRECT` and holds a changed setpoint in AUTO, but the fixture still configures it **MANUAL** - its other tests pin open-loop response - and its kp 0.01 / ki 0.005 tuning is slow (~7800 s to settle). Unblocks nothing directly |
| **T9-4** | `73ff965` | Envelope status in the snapshot (closes M9, 4/4). `Engine` classifies every configured limit against equipment state each step, via T9-1's `Evaluator` and T9-3's `ExcursionTracker`, and publishes a sparse ISA-labeled envelope map in the snapshot (`{"V-101.level": {"band": "hi", "since": 12.0}}`) - `NORMAL` points are simply absent. `since` is seeded at construction against the design point; instruments are wired before limits resolve, so a pre-biased transmitter seeds correctly (a roborev-caught construction-order bug, now a regression test). A limit that doesn't resolve against the equipment section (`K-101.discharge_pressure`, `P-101.flow` - both solved node/branch values) warns once at construction and is skipped rather than crashing - the same "instruments are not in C3" gap below, now also hit from the `limits` side. Frees the spine lock (`app/engine/snapshot.py`); unblocks nothing directly (no task depends on T9-4) |
| **T8-4** | `f3cc8b6` | Loop execution in the engine step. `Engine.step()` is now clock -> control -> integrate -> couple/solve -> publish: each loop reads its PV from the indicated view the previous step published and posts its output to `Engine.arbiter` (T7-4) as a controller demand, so a published measurement strokes the valve within the very next step and no loop can see a mid-solve value. Config order, order-independent by construction; a stopped step runs no loop; a loop is primed on first execution so AUTO starts bumpless. The snapshot's `controllers` section now carries `pv`/`sp`/`out`/`mode`. `load_loops()` seeds a loop from its valve's current command, and `Plant.passthrough()` replaces a `to_config()` round-trip. **PIC-101 must stay MANUAL** - the PID is wrong-acting for a vent valve; T8-6 was added to the build plan to fix it. Unblocks T8-5 (V1.1-deferred) and T8-6 |
| **T9-3** | `0bb2e25` | Time-in-band and excursion tracking. `ExcursionTracker` in `app/envelope/tracker.py` accumulates per-severity time-in-band (WARNING/ALARM/TRIP) and the single worst excursion (magnitude + timestamp) for one monitored point, from severities its caller's own T9-1 `Evaluator` already classified - hysteresis stays owned by `Evaluator` alone. A roborev finding (magnitude reads `0.0` while a deadband-held severity is non-normal) was fixed by documenting the interaction and adding a regression test driving the tracker from a real `Evaluator`. Closes M9's third task; unblocks T9-4 and T14-3 on the dependency side (T9-4 separately needed the spine lock, held by T8-4 at the time) |
| **T8-3** | `619af4e` | Loop configuration and tag wiring (C3's `controllers` key). `app/controls/loader.py`'s `load_loops()` resolves each entry into a `Loop`/`PID` (T8-1/T8-2): `pv` resolves against a node id - a node has exactly one measured quantity, its pressure, so the bare tag is unambiguous with no C3 change, sidestepping the "instruments are not in C3" gap below - and `out` resolves against an explicit per-class allowlist of settable devices (`ControlValve.set_position_target` only, mirroring `app.disturbances.malfunction.WRITABLE`). Two loops claiming one `out` tag is rejected (roborev), and an entry with a bad `pv` cannot itself "claim" an `out` tag it never actually got to drive (also roborev). Wires `PIC-101` (`N-201` -> `PV-101`) into `config/plants/olefins_lite.yaml`, configured MANUAL since nothing reads `controllers` at engine-step time yet. Rebased over T9-2/T10-2's own `olefins_lite.yaml` additions (trivial conflict, both top-level keys kept). Unblocks T8-4 |

**ADRs on `main`:** ADR 0001 ([flow-domain separation](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md))
with Amendment 1, and ADR 0002 ([typed ports](../../docs/ADR_0002_TYPED_PORTS.md)) with
Amendments 1–3 (the last now implemented by T5-7 and consumed by T5-5, closing
its sequencing chain). **Read the ADRs themselves** — summarising them here is
what made this file 1,086 lines.

## Milestone progress

| Milestone | Status |
|---|---|
| **M0**–**M5** | **Complete.** Checkpoint A (M1) and Checkpoint B (M4) both reached |
| **M6** Energy Balance and Temperature | 5/5 - **Complete.** T6-1 through T6-5 all merged |
| **M7** Control Valves and Final Elements | 5/5 - **Complete.** T7-1 through T7-5 all merged |
| **M8** PID Controllers and Modes | 5/6 - T8-1 through T8-4 and T8-6 Complete; T8-5 startable (V1.1-deferred) |
| **M9** Operating Envelopes | 4/4 - **Complete.** T9-1 through T9-4 all merged |
| **M10** Alarms | 2/5 - T10-1, T10-2 Complete; T10-3 startable |
| **M13** Malfunctions | 2/5 - T13-1, T13-2 Complete; T13-5 Ready for Review (PR #100); T13-3, T13-4 startable |
| **M14** Scenario Engine | 1/6 - T14-1 Complete; T14-2, T14-3 startable |
| **MR** Remediation | 13/13 - **Complete.** R1-R12, R8 all merged |
| **M15** Action Log and Scoring | 1/4 - T15-1 Complete; T15-4 startable |
| **M17** Historian and Trends | 0/4 - T17-1 Ready for Review |
| **M18** Deployment and Operations | 1/5 - T18-2 Complete |
| M11, M12, M16, M19 | None Complete |

**76 of 115 tasks Complete.** Checkpoint **C** (M5 + M6 + M7) is now reached -
all three closed. M9 (Operating Envelopes) is now also fully closed. Next
checkpoint is **D** (M8), which has landed five of its six tasks (T8-5 is
V1.1-deferred). Its "loops reject an injected disturbance" gate on the
reference plant still needs PIC-101 switched to AUTO in `olefins_lite.yaml`,
which T8-6 made possible but deliberately did not do - no task owns it yet. **MR is now fully merged.**

## The next task

No startable task is Opus-assigned right now - all sixteen below default to
Sonnet (none is a `core`-category task). The spine lock
(`app/engine/snapshot.py`, `app/engine/engine.py`, `app/equipment/base.py`,
`app/plant/topology.py`) is free and no startable task needs it; T12-1 and
T18-5 each add a *new* isolated module under `app/engine/` (satellite work).
Which task to hand out next is a scheduling choice, not a dependency one.

### Startable now (16)

| Task | Name | Model | Branch |
|---|---|---|---|
| **T8-5** | Cascade control (V1.1-deferred) | Sonnet | `feature/cascade-control` |
| **T10-3** | Alarm history and acknowledge | Sonnet | `feature/alarm-history` |
| **T10-4** | Flood suppression and first-out (V1.1-deferred) | Sonnet | `feature/alarm-flood-control` |
| **T11-1** | Interlock definitions and evaluator | Sonnet | `feature/interlock-evaluator` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T13-3** | Injection profiles | Sonnet | `feature/malfunction-profiles` |
| **T13-4** | Malfunction catalogue | Sonnet | `feature/malfunction-catalogue` |
| **T14-2** | Trigger evaluator | Sonnet | `feature/scenario-triggers` |
| **T14-3** | Objective evaluator | Sonnet | `feature/scenario-objectives` |
| **T15-4** | Score persistence | Sonnet | `feature/score-store` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T18-1** | Container and WSGI serving | Sonnet · one worker process | `chore/container-and-ci` |
| **T18-3** | API input validation | Sonnet | `feature/api-validation` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |
| **T18-5** | Session lifecycle and config versioning | Sonnet | `feature/lifecycle-versioning` |

**In review, not yet startable-list eligible:** T13-5 (PR #100), T17-1 - both Ready for Review, neither merged yet.

**Scheduling notes.** The spine lock is **one global lock**, now the standing
rule (R12, [DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); it is
free. **T18-1 must run exactly one Gunicorn
worker process** — `SessionRegistry` is per-process (R7). T12-1 adds a *new* isolated module under `app/engine/`, which is
satellite work, but `rng.py` is spine: adding RNG state save/restore there
(T12-1 or T14-5) takes the spine lock.

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
   adverse gradient. Needs a check valve, which no task owns.

3. **`get_state()` on a device is slow state only.** Flow and the two pressures
   are not device attributes; a page's row is assembled from the snapshot by
   `Session.compressor_state()` / `pump_state()`. The compressor's `temperature`
   became `temperature_at(suction, discharge)` because suction and discharge
   pressure are solver outputs. **Do not put a flow or a pressure back on a
   device.**

## Known technical debt (recorded, not scheduled)

- **Boundary temperatures are not in C3.** A config-loaded plant supplies at
  60 °F everywhere unless the caller passes `boundary_temperatures`; a node
  `temperature` field is a C3 change that no task owns yet. Related: the
  compressor page's `temperature` still reads `temperature_at` from the 75 °F
  design suction, not the transported stream, and C4 has no field for a
  transport that did not settle.

- **Instruments are not in C3.** `Engine(instruments=...)` is the only way to
  give a plant a transmitter; a config key for them is a C3 change no task owns
  yet. T8-3 (`app/controls/loader.py`) sidestepped it rather than closing it:
  a `controllers.pv` entry resolves against a node id, not a transmitter tag,
  since a node has exactly one measured quantity (its pressure) and needs no
  new key. That only covers pressure loops - a level, flow or temperature PV
  still has nothing to bind to. Transmitters only have `bias` - no stuck or
  range-clamped reading (T13-2). T9-4 (`app/engine/engine.py`) hit the same
  gap from the `limits` side: a `(tag, variable)` resolves only against a
  field the device's own `get_state()` publishes, so `V-101.level` classifies
  live but `K-101.discharge_pressure` and `P-101.flow` - both solved
  node/branch values - are configured in `olefins_lite.yaml` and warned about
  once, at Engine construction, then never evaluated. No task owns the general
  device-to-point resolver either loops or limits would need to close this for
  good.
- **A stopped machine keeps a small residual flow.** Never-run reads exactly
  `0.0`; *stopped* settles just off zero and stays: **0.055 GPM** (pump),
  **0.004 SCFM** (compressor). At shutoff the branch curve is flat, so the root
  is a double root and the 1e-7 psia of slack maps to `sqrt(tolerance /
  resistance)` of flow. The solver reports `converged` at `iterations: 0`. Not a
  defect, and **not a reason to retune the tolerance**. **Do not assert an idle
  flow of exactly zero.** That residual reaches vessel level with no clamp and
  no deadband — 3.3 gal/hour against a 1000 gal vessel — and that is decided
  behaviour, not a leak to plug.
- **The `Equipment.characteristic` docstring overstates the Jacobian** —
  `base.py` is frozen, so this hasn't been corrected in place. Full explanation
  in [.claude/rules/engine.md](../../.claude/rules/engine.md).
- **A resistance-only valve cannot stop reverse flow, and it absorbs most of the
  drop.** Against 50 → 180 psia a cold plant backflows through a wide-open valve
  (−1000 GPM at Cv 100), and closing it trims that only by the square root of
  the added resistance. With the valve as the only resistance it takes 107–139
  psi of the 150 psi the pumps make — 70–93% of system drop against 10–30% in a
  real plant. A pipe or resistance device is needed for line loss; **no task
  owns one yet.**
- **Contract-test discovery is import-order dependent.** `REGISTERED` in
  `tests/test_equipment_contract.py` is computed at import time, so an
  `Equipment` subclass defined in a later-imported test module silently escapes
  the parametrized contract tests. Passes either way today.
- **`app/init.py` is a misnamed empty file** (not `__init__.py`). `app` resolves
  as a namespace package, so imports work. Harmless; never had a task.
- **`static/style.css` is 15 lines** and defines almost none of the classes the
  templates use. The pages are largely unstyled — intentional, rebuilt at M16.
- **History carries T1-5 twice** (`d41949a` + `5ea07cb`, identical) from a branch
  race. Already pushed; deliberately not rewritten. This is why the one-worktree
  rule exists.
- **`package.json` / `node_modules/`** exist only for a TypeScript dev
  dependency and are not part of the app.

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

**The mass-balance conservation identity is now recorded, not open.** T5-4
(`tests/test_mass_balance.py`) wrote down ADR 0002 §7.1's identity —
`Δinventory = Σ(n=0…N-1) q_n·dt/60`, with `q₀` read before the first `step()` —
and checked it against T5-5's cold-start transient, both cumulatively (no
drift to 20,000 steps) and per-step. A later task touching conservation should
read that file before re-deriving the identity.

**Near zero vapour flow, explicit Euler limit-cycles.** Where `dε/dt ∝ −√ε` it
settles into a stable period-2 cycle, measured at ±0.0615 SCFM and ±7.5e-6 psi,
still bounded at step 12,000. Owned by nobody; it gets a numerical-stability
task *if and when* M8 controller testing needs one. Until then **assert a
pressure asymptote rather than a final flow**, which is phase-dependent.

**Do not assert a cold-start operating point in T3-4's reference fixtures.**
With no check valve, line resistance or control valve, a stopped machine
backflows if the boundaries are sized for running and a started one runs away if
they are sized for cold.

Test-writing traps that apply repo-wide — backflow hiding inside a "flow rises"
assertion, asserting mid-ramp states, and golden-trace policy — live in
[.claude/rules/testing.md](../../.claude/rules/testing.md).

## Where the rest lives

| Question | Source |
|---|---|
| What must I never break? | [AGENTS.md](../../AGENTS.md) |
| How do current and target architecture differ? What is in which module? | [ARCHITECTURE.md](../../docs/ARCHITECTURE.md) |
| Why was a decision made? | ADR [0001](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md), ADR [0002](../../docs/ADR_0002_TYPED_PORTS.md) |
| What did task T*n* actually deliver? | [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json) — search the task ID |
| What is the task list and schedule? | [BUILD_PLAN.html](../../docs/BUILD_PLAN.html) — search your task ID, never read it whole |
| How do I branch, test and merge? | [DEVELOPMENT.md](../../DEVELOPMENT.md) |
| What units does a number carry? | [UNITS_CONVENTION.md](../../docs/UNITS_CONVENTION.md) |
| How many tests, and where? | `python -m pytest --collect-only -q` — never a table in a doc |
