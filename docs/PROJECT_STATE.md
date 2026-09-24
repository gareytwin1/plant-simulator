# Project State

What is true **right now**. This file goes stale by design; the stable rules are
in [CLAUDE.md](../CLAUDE.md) and the architecture is in
[ARCHITECTURE.md](ARCHITECTURE.md).

**Refresh this file when a task merges, and keep it lean:** a merged task gets
its **one-line** entry here, and its full completion note goes in
[BUILD_PLAN_STATUS.json](BUILD_PLAN_STATUS.json). Per-task handoff sections do
not accumulate here — that is what grew this file to 1,086 lines once already.
When the recent-merges table below passes ~6 rows, drop the oldest — it is
already in BUILD_PLAN_STATUS.json and does not need a second home.

---

## Right now

**Last state refresh:** 24 September 2026, at `17330bc` (R3, PR #61) —
**this is a snapshot, not a live pointer.** Run
`git log 17330bc..HEAD --oneline` to see what has merged since.
**Full suite as of this refresh:** **1022 passed** · `python -m mypy` clean over 26 source files · compressor golden trace moved deliberately (temperature field only, on the two boundary-asymmetric scenarios — justified in T6-2's note)
**In flight:** nothing. **No spine lock is held.** No task is Blocked.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **R3** | `17330bc` | Machine design ranges. Property setters enforce D3 on `GasCompressor` and `CentrifugalPump`: `1 < isentropic_exponent <= 5/3`, `0 < polytropic_efficiency <= 1`, every `*_resistance > 0`, `shutoff_pressure_rise >= 0`, `base_temperature > -459.67`°F; `temperature_at` rejects a non-finite or non-positive pressure with `ValueError`. Vessel's numeric range-check helper lifted into `app/equipment/ranges.py`, shared by all three devices. 18 new tests, each confirmed to fail against the merge-base before the fix. `app/equipment/base.py` untouched — no spine lock |
| **R1** | `0a4af4f` | Reject non-finite numbers at load. Finite numbers enforced in layers (D1): `validate.py`'s generic `"number"` branch now rejects NaN/±inf ahead of the `minimum` check, covering node pressure, limit fields, controller gains and interlock `delay_s` in one change; `loader.py`'s `_apply_design` rejects a non-finite design number directly, since `design` has no per-key schema for the validator to see; the loader's own boundary-pressure check also rejects non-finite pressure explicitly, extended to internal nodes too. 25 new tests, each confirmed to fail against the merge-base before the fix. Unblocks R4 |
| — | `300659b` | (non-task) Added milestone **MR Remediation** (R1–R12, 13 tasks) to the build plan from the approved post-T6-2 audit. Puts **R9 and R2 in front of T6-5** and **R7 in front of T18-1**. Docs only — no code changed. PR #59 |
| **T6-2** | `20a504a` | Polytropic compression temperature. `GasCompressor.temperature_at` replaces the piecewise table with `T2 = T1·(P2/P1)^((k-1)/(k·η))`; `isentropic_exponent`/`polytropic_efficiency` are named design attributes. Takes the actual solved suction/discharge pressures via `Session.compressor_state()`, not a fixed design target — an initial revision used the design target to avoid touching the `app/engine/` spine and was caught in review as physically wrong |
| T7-2 | `537f206` | Extract valve logic from the compressor. `GasCompressor` loses `discharge_valve_position`/`target`/`rate`; `FV-201` (`ControlValve`) now sits on K-101's discharge in `olefins_lite.yaml`, over a new internal node N-204. Resistance re-split 0.04 machine + 0.01 valve so every T5-5 design value is unmoved. Dead `/api/valve` endpoint and page slider removed |
| — | `f9ce10e` | (non-task) Fixed `start-task`/`merge-task` skills reading `$1` instead of `$0` for their own argument — Claude Code's positional substitution is zero-based, so both were silently reading an empty string. PR #56 |

**ADRs on `main`:** ADR 0001 ([flow-domain separation](ADR_0001_FLOW_DOMAIN_SEPARATION.md))
with Amendment 1, and ADR 0002 ([typed ports](ADR_0002_TYPED_PORTS.md)) with
Amendments 1–3 (the last now implemented by T5-7 and consumed by T5-5, closing
its sequencing chain). **Read the ADRs themselves** — summarising them here is
what made this file 1,086 lines.

## Milestone progress

| Milestone | Status |
|---|---|
| **M0**–**M5** | **Complete.** Checkpoint A (M1) and Checkpoint B (M4) both reached |
| **M6** Energy Balance and Temperature | 2/5 — T6-1, T6-2 Complete; T6-3, T6-4 startable; T6-5 waits on R2 and R9 |
| **M7** Control Valves and Final Elements | 2/5 — T7-1, T7-2 Complete; T7-3, T7-4, T7-5 startable |
| **MR** Remediation | 2/13 — R1, R3 Complete; R9 Ready for Review (PR #62); the rest of the no-spine set is startable, plus R2 and R10b at the head of the spine queue |
| M8–M19 | Not started |

**43 of 114 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7); M5 is
closed, M6 has landed two tasks and M7 two. MR is scheduled to merge by
Checkpoint C, because T6-5 waits on two of its tasks.

## The next task

**No single task is "the" next one.** Twenty-seven tasks are startable in
parallel (table below); which to hand out next is a scheduling choice, not a
dependency one. R2, R10b, R12, T7-4 and T13-1 are the Opus-level tasks in
that list. R9 is no longer in this list — it's on its own branch, Ready for
Review, not yet merged.

### Startable now (27 tasks)

| Task | Name | Model | Branch |
|---|---|---|---|
| **R2** | Solver refuses non-finite curves | Opus · spine lock | `fix/solver-nonfinite` |
| **R4** | Refuse structural design keys | Sonnet | `fix/structural-design-keys` |
| **R5** | Request validation | Sonnet · `app/main.py` lock | `fix/request-validation` |
| **R8** | Flow unit label | Sonnet | `fix/flow-unit-label` |
| **R10a** | Documentation corrections | Sonnet | `docs/solved-state-wording` |
| **R10b** | C1/C4 docstring corrections | Opus · spine lock | `docs/c1-c4-docstrings` |
| **R11** | Check in the status generator | Sonnet | `chore/status-generator` |
| **R12** | Reconcile spine rules | Opus | `docs/spine-rules` |
| **T6-3** | Heat exchanger model | Sonnet | `feature/heat-exchanger` |
| **T6-4** | Furnace model | Sonnet | `feature/furnace` |
| **T7-3** | Valve fault modes | Sonnet | `feature/valve-faults` |
| **T7-4** | Command arbitration | Opus | `feature/command-arbitration` |
| **T7-5** | Relief device | Sonnet | `feature/relief-valve` |
| **T13-1** | Malfunction model and registry | Opus | `feature/malfunction-model` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T8-1** | PID block | Sonnet | `feature/pid-block` |
| **T9-1** | Envelope evaluator | Sonnet | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | Sonnet | `feature/alarm-state-machine` |
| **T14-1** | Scenario file schema | Sonnet | `feature/scenario-schema` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T18-2** | CI pipeline | Sonnet | `chore/ci-pipeline` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |
| **T13-5** | Physics isolation guard | Sonnet | `test/import-direction-guard` |
| **T15-4** | Score persistence | Sonnet | `feature/score-store` |
| **T17-1** | Ring-buffer historian | Sonnet | `feature/historian` |

**Still waiting:** T8-3, T9-2 and T11-1 — on T8-2 and T9-1, unchanged by T6-1.
From MR: **T6-5** on R2 and R9; **T18-1** on R7; R6 on R5; R7 on R6.

**Scheduling notes.** MR runs under **one global spine lock** (U1 in the
approved design treats `network.py` and `scheduler.py` as spine too). The spine
queue is R2 → R10b → R6 → R7 → T6-5, one at a time. The `app/main.py` lock
goes R5 → R6. R4 also edits `app/plant/loader.py`, which R1 (merged) touched
too, but R1 is done so R4 is simply startable now, no sequencing left. T7-3 edits `app/equipment/valve.py` and should not run
beside another valve change. T12-1 adds a *new* isolated module under
`app/engine/`, which is satellite work under the `app/engine/` rule in
CLAUDE.md.

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
   became `temperature_at(spread)` because spread is a solver output. **Do not
   put a flow or a pressure back on a device.**

## Known technical debt (recorded, not scheduled)

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
  in [.claude/rules/engine.md](../.claude/rules/engine.md).
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
[.claude/rules/testing.md](../.claude/rules/testing.md).

## Where the rest lives

| Question | Source |
|---|---|
| What must I never break? | [CLAUDE.md](../CLAUDE.md) |
| How do current and target architecture differ? What is in which module? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Why was a decision made? | ADR [0001](ADR_0001_FLOW_DOMAIN_SEPARATION.md), ADR [0002](ADR_0002_TYPED_PORTS.md) |
| What did task T*n* actually deliver? | [BUILD_PLAN_STATUS.json](BUILD_PLAN_STATUS.json) — search the task ID |
| What is the task list and schedule? | [BUILD_PLAN.html](BUILD_PLAN.html) — search your task ID, never read it whole |
| How do I branch, test and merge? | [DEVELOPMENT.md](../DEVELOPMENT.md) |
| What units does a number carry? | [UNITS_CONVENTION.md](UNITS_CONVENTION.md) |
| How many tests, and where? | `python -m pytest --collect-only -q` — never a table in a doc |
