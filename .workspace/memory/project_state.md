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

**Last state refresh:** 26 September 2026, at `3e75af7` (Merge R12: Reconcile
spine rules, PR #84) - **this is a snapshot, not a live pointer.** Run
`git log 3e75af7..HEAD --oneline` to see what has merged since.
**Full suite as of this refresh:** **1313 passed** · `python -m mypy` clean over 31 source files · compressor golden trace moved deliberately (temperature field only, on the two boundary-asymmetric scenarios - justified in T6-2's note)
**In flight:** T8-2 (PR #86) and T13-1 (PR #85), both Ready for Review and
neither on a spine file. **No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **R12** | `3e75af7` | Spine rules reconciled. One global spine lock over `base.py`, `topology.py` and every `app/engine/` module on `main` (U1 made standing), held by whichever task is In Progress or Ready for Review on a spine file; `app/main.py` keeps its own lock. Rule lives in DEVELOPMENT.md's File ownership; `engine.md` and the start-task skill link to it. `rng.py` is now spine |
| **T8-1** | `df1f91f` | PID block. `PID` in `app/controls/pid.py`: conditional-integration anti-windup that freezes the integral only when the current error pushes further into whichever bound is saturated (not merely because the output happens to be clamped, which could latch it there forever - roborev caught this after the first commit), output clamping, derivative on measurement. Constructor rejects `output_min > output_max` and `ki < 0` (a negative `ki` would flip the saturation-direction inference). Standalone, no plant dependency. T8-2 binds to it |
| **T7-4** | `96f888e` | Command arbitration. `CommandArbiter` in `app/controls/arbitration.py`: standing demands per bound output keyed by `(source, requester)`, resolved by fixed precedence interlock > operator > controller, independent of arrival order. `apply()` writes every output even if one raises, then raises an `ExceptionGroup`. Not wired to anything yet - `app/main.py`'s operator routes still call device setters directly and must be rewired through the arbiter before M11 interlocks go live. T8-4 and T11-2 bind to it |
| **T6-5** | `71e2b99` | Energy propagation. New spine module `app/engine/transport.py` writes node and stream temperatures upwind of each converged domain's flows; boundaries supply at `Engine(boundary_temperatures=...)`, default 60 °F. Devices change temperature through `thermo.ThermalDevice.leaving_temperature`, which `GasCompressor` implements. A domain with no steady temperature holds and reports on `transport.settled`/`failure` rather than raising. Snapshot node rows carry `temperature`. Releases the spine lock |
| **T6-3** | `332ca8c` | Heat exchanger model. `HeatExchanger` implements `thermo.ThermalDevice.leaving_temperature` via the fixed-coolant effectiveness formula `T_out = T_cold + (T_in - T_cold) * exp(-UA_eff / |q*Cp|)`, provably bounded between the coolant and arriving temperatures at every flow, in either direction. The metal wall's lag (`_move_toward`, C1) scales `UA_eff` through a `[0,1]` capability fraction rather than substituting into the `T_in`/`T_cold` pair - that broke the zero-capability case. `duty` is now `duty(arriving)`, derived from `leaving_temperature`. Known limitation: `inlet_temperature` is written by nothing in a running plant, so the metal chases a constructor default rather than the live arriving stream, until a follow-up wires the engine's arriving temperature into a device's slow state |
| **T18-2** | `e8c4d2b` | CI pipeline. `.github/workflows/ci.yml` runs `pytest` and `mypy` on Python 3.12 for every push to `main` and every PR; `main`'s branch protection requires the `test` check, strict (a PR must be up to date with `main`) |

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
| **M8** PID Controllers and Modes | 1/5 - T8-1 Complete; T8-2 Ready for Review (PR #86) |
| **MR** Remediation | 12/13 - R1-R7, R9, R10a, R10b, R11, R12 Complete; R8 remains, no-spine and startable |
| **M18** Deployment and Operations | 1/5 - T18-2 Complete |
| M9–M17, M19 | Not started |

**58 of 114 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7); M5 is
closed, M6 has landed four of its five tasks and M7 three. MR is scheduled to
merge by Checkpoint C; its spine work is done.

## The next task

**No single task is "the" next one.** Sixteen tasks are startable. Which to
hand out next is a scheduling choice, not a dependency one. All are Sonnet
tasks; T13-1, the remaining Opus task, is in review.

### Startable now (16)

| Task | Name | Model | Branch |
|---|---|---|---|
| **R8** | Flow unit label | Sonnet | `fix/flow-unit-label` |
| **T6-4** | Furnace model | Sonnet | `feature/furnace` |
| **T7-3** | Valve fault modes | Sonnet | `feature/valve-faults` |
| **T7-5** | Relief device | Sonnet | `feature/relief-valve` |
| **T9-1** | Envelope evaluator | Sonnet | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | Sonnet | `feature/alarm-state-machine` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T13-5** | Physics isolation guard | Sonnet | `test/import-direction-guard` |
| **T14-1** | Scenario file schema | Sonnet | `feature/scenario-schema` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T15-4** | Score persistence | Sonnet | `feature/score-store` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T17-1** | Ring-buffer historian | Sonnet | `feature/historian` |
| **T18-1** | Container and WSGI serving | Sonnet · one worker process | `chore/container-and-ci` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |

**Still waiting:** T8-3 and T9-2 - on T8-2 (in review, PR #86) and T9-1. T11-1 still waits on T9-1 too.

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
