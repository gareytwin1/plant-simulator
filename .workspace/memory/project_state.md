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

**Last state refresh:** 26 September 2026, at `3cb05dc` (Merge T10-1: Alarm
state machine, PR #87) - **this is a snapshot, not a live
pointer.** Run `git log 3cb05dc..HEAD --oneline` to see what has merged since.
**Full suite as of this refresh:** **1499 passed** · `python -m mypy` clean over 39 source files · compressor golden trace moved deliberately (temperature field only, on the two boundary-asymmetric scenarios - justified in T6-2's note)
**In flight:** T14-1 (PR #88), Ready for Review; not spine.
**No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T10-1** | `3cb05dc` | Alarm state machine (C7). Pure ISA-style lifecycle in `app/alarms/state.py`: `NORMAL`/`UNACK`/`ACKED`/`RTN_UNACK`, with clear-before-acknowledge handled explicitly - a condition that clears before acknowledgement holds in `RTN_UNACK` rather than returning to `NORMAL`, and a re-alarm from there returns to `UNACK` on the same `Alarm` instance rather than duplicating it. No plant dependency. T10-2 binds to it |
| **T9-1** | `89ed2fc` | Envelope evaluator. `Evaluator`/`Limits`/`Severity` in `app/envelope/evaluator.py`: boundary-inclusive classification against six optional ordered thresholds (normal/warning/alarm/trip), deadband gating de-escalation only, on-delay gating escalation only. Roborev caught two real edge cases pre-merge: a same-severity side flip (`warning_lo` held, `warning_hi` reached) silently bypassing both guards, and the on-delay pending timer not resetting when an uncommitted escalation flips side - both fixed and locked in with tests. No plant dependency. Unblocks T9-2, T9-3, T11-1 |
| **T13-2** | `462b313` | True and indicated values. `Instrument(tag, section, source, variable, bias)` in `app/engine/instruments.py`; `Snapshot.equipment/nodes/streams` are now the indicated view and `Snapshot.truth` the physics, excluded from `as_dict()` and guarded by `tests/test_truth_isolation.py` (allowlist empty). `Engine(instruments=...)`; `Instrument.bias` is malfunction-writable, so instrument drift is a ramped bias. Releases the spine lock |
| **T13-1** | `e009883` | Malfunction model and registry (C8). `Malfunction(target_tag, parameter, value, profile, start_condition)` in `app/disturbances/malfunction.py`; `WRITABLE` allowlists design parameters per exact device class, so solver outputs and slow state raise `NotWritable`. `MalfunctionRegistry.update(snapshot)` latches onset and captures the original; `revert` restores it exactly. `Step`/`AtTime` minimal, T13-3 extends. Open for T13-4: trips are commands, not parameters (needs an Opus decision); `stroke_rate` is unvalidated so not allowlisted (T7-3) |
| **T8-2** | `6e68db0` | Control modes and bumpless transfer. `Loop`/`Mode` in `app/controls/modes.py`: MANUAL/AUTO/CASCADE wrapping a `PID`, bumpless in both directions via a new `PID.track()` (back-calculation preload, `app/controls/pid.py`). Roborev caught two real bugs pre-merge: `track()` baking a transient derivative into the preloaded integral that the next call's guaranteed-zero derivative couldn't reproduce (bump on any `kd != 0` loop with a drifting measurement), and engaging `CASCADE` with the master already in `AUTO` skipping the preload entirely (a full setpoint step on the single most common "engage cascade" action). Both fixed with regression tests. T8-3 binds to it |
| **R12** | `3e75af7` | Spine rules reconciled. One global spine lock over `base.py`, `topology.py` and every `app/engine/` module on `main` (U1 made standing), held by whichever task is In Progress or Ready for Review on a spine file; `app/main.py` keeps its own lock. Rule lives in DEVELOPMENT.md's File ownership; `engine.md` and the start-task skill link to it. `rng.py` is now spine |

**ADRs on `main`:** ADR 0001 ([flow-domain separation](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md))
with Amendment 1, and ADR 0002 ([typed ports](../../docs/ADR_0002_TYPED_PORTS.md)) with
Amendments 1–3 (the last now implemented by T5-7 and consumed by T5-5, closing
its sequencing chain). **Read the ADRs themselves** — summarising them here is
what made this file 1,086 lines.

## Milestone progress

| Milestone | Status |
|---|---|
| **M0**–**M5** | **Complete.** Checkpoint A (M1) and Checkpoint B (M4) both reached |
| **M6** Energy Balance and Temperature | 4/5 - T6-1, T6-2, T6-3, T6-5 Complete; T6-4 startable |
| **M7** Control Valves and Final Elements | 3/5 - T7-1, T7-2, T7-4 Complete; T7-3, T7-5 startable |
| **M8** PID Controllers and Modes | 2/5 - T8-1, T8-2 Complete; T8-3 startable |
| **M9** Operating Envelopes | 1/4 - T9-1 Complete; T9-2, T9-3 startable |
| **M10** Alarms | 1/5 - T10-1 Complete; T10-2 startable |
| **M13** Malfunctions | 2/5 - T13-1, T13-2 Complete; T13-3, T13-5 startable; T13-4 waits on T7-3 |
| **MR** Remediation | 12/13 - R1-R7, R9, R10a, R10b, R11, R12 Complete; R8 remains, no-spine and startable |
| **M18** Deployment and Operations | 1/5 - T18-2 Complete |
| M11, M12, M14–M17, M19 | None Complete; T14-1 is Ready for Review |

**63 of 114 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7); M5 is
closed, M6 has landed four of its five tasks and M7 three. MR is scheduled to
merge by Checkpoint C; its spine work is done.

## The next task

**No single task is "the" next one.** Nineteen tasks are startable, all Sonnet.
Which to hand out next is a scheduling choice, not a dependency one.

### Startable now (19)

| Task | Name | Model | Branch |
|---|---|---|---|
| **R8** | Flow unit label | Sonnet | `fix/flow-unit-label` |
| **T6-4** | Furnace model | Sonnet | `feature/furnace` |
| **T7-3** | Valve fault modes | Sonnet | `feature/valve-faults` |
| **T7-5** | Relief device | Sonnet | `feature/relief-valve` |
| **T8-3** | Loop configuration and tag wiring | Sonnet | `feature/loop-config` |
| **T9-2** | Limit definitions in plant config | Sonnet | `feature/envelope-limits` |
| **T9-3** | Time-in-band and excursion tracking | Sonnet | `feature/excursion-tracking` |
| **T10-2** | Alarm manager over envelope events | Sonnet | `feature/alarm-manager` |
| **T11-1** | Interlock definitions and evaluator | Sonnet | `feature/interlock-evaluator` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T13-3** | Injection profiles | Sonnet | `feature/malfunction-profiles` |
| **T13-5** | Physics isolation guard | Sonnet | `test/import-direction-guard` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T15-4** | Score persistence | Sonnet | `feature/score-store` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T17-1** | Ring-buffer historian | Sonnet | `feature/historian` |
| **T18-1** | Container and WSGI serving | Sonnet · one worker process | `chore/container-and-ci` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |

**Still waiting:** T9-4 - on T9-2 and T9-3. T13-4 waits on T7-3.

**Scheduling notes.** The spine lock is **one global lock**, now the standing
rule (R12, [DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); the spine
queue is empty since T6-5. **T18-1 must run exactly one Gunicorn
worker process** — `SessionRegistry` is per-process (R7). T7-3 edits
`app/equipment/valve.py` and should not run beside another valve change. T12-1 adds a *new* isolated module under `app/engine/`, which is
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
  yet, and T8-3's PV binding will want one. Transmitters only have `bias` - no
  stuck or range-clamped reading (T13-2).
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
