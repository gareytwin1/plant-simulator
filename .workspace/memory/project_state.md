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

**Last state refresh:** 25 September 2026, at `e4357f3` (Merge R11: Check in
the status generator, PR #68) — **this is a snapshot, not a live pointer.**
Run `git log e4357f3..HEAD --oneline` to see what has merged since.
**Full suite as of this refresh:** **1067 passed** · `python -m mypy` clean over 26 source files · compressor golden trace moved deliberately (temperature field only, on the two boundary-asymmetric scenarios — justified in T6-2's note)
**In flight:** R5 is Ready for Review (not yet merged) — its
`docs/BUILD_PLAN_STATUS.json` needs regenerating against current `main`
before it merges cleanly (a derived-file conflict, not a code one). **No
spine lock is held** — R2 released it; R10b is next in the queue. No task is
Blocked.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **R11** | `e4357f3` | Check in the status generator. `scripts/build_plan_status.py` regenerates `docs/BUILD_PLAN_STATUS.json` from `BUILD_PLAN.html`'s `TASKS` array plus a `taskStatus` export, via `node`; validated to reproduce the current file byte-for-byte before use, and used for real for the first time on this merge and R4's. `DEVELOPMENT.md` carries the authoritative procedure now; `merge-task`/`ready-for-review` skills cite the script instead of a memory-only note. Found, not fixed: 3 Complete tasks still declare `docs/PROJECT_STATE.md`, renamed to `.workspace/memory/project_state.md` by an earlier docs commit — worth a follow-up regeneration pass |
| **R4** | `993a25d` | Refuse structural design keys. `STRUCTURAL_ATTRIBUTES` denylist (`tag`, `ports`) plus `_`-prefixed rejection in `_apply_design`, so a plant config can't smuggle a structural or private attribute in through `design`. `app/plant/loader.py`, which R1 also touched — R1 merged first, so no sequencing was needed |
| **R2** | `6cffddd` | Solver refuses non-finite curves. A residual that is non-finite, or overflows its tolerance scaling, on entry raises `SolverError` naming the branch or node row, with the plant restored bit-identically; `SolverResult` refuses a non-finite residual or a converged result with `residual > 1.0`; `_norm` treats a non-finite trial row as infinitely far from converged, since `max()` was skipping a NaN that wasn't the first row. 11 defect-reproduction tests, each confirmed to fail against the merge-base before the fix. Releases the global remediation spine lock — R10b is next. Unblocks T6-5 (its other three dependencies were already Complete) |
| — | `99a2f75` | (non-task) Enabled roborev continuous review: `.roborev.toml` pins `agent = 'claude-code'` (the only review agent installed on this machine); `roborev@local` plugin enabled in `.claude/settings.json` so open reviews surface at session start; new "Continuous review (roborev)" section in `DEVELOPMENT.md` (`roborev show HEAD`, `roborev tui`, `/roborev-refine` before opening a PR). Post-commit/post-rewrite/pre-push git hooks installed locally (machine state, not in the diff — each contributor runs `roborev init` themselves). Verified end-to-end: the setup commit's own post-commit review ran and passed. Docs and config only — no `app/` code changed. PR #64 |
| — | `b1ba496` | (non-task) Adopted the coding-agent-toolkit layout: `CLAUDE.md` content moved to `AGENTS.md` (`CLAUDE.md` is now `@AGENTS.md`); `docs/PROJECT_STATE.md` moved to `.workspace/memory/project_state.md`; added `.workspace/{memory,memory-auto,transitions,work}`, a seeded `MEMORY_INDEX.md`, and `.claude/settings.json` enabling the system/workflow/memory/development plugins; copied the three Claude auto-memories into `.workspace/memory-auto/`; adopted the toolkit author's unrelated-fixes rule (fix unrelated lint/test failures as you go, own commit each, except spine files and contracts). Docs and config only — no `app/` code changed. PR #63 |
| **R9** | `b84767d` | Immutable composition. `StreamState.composition` is now a `MappingProxyType` over a private dict copy, so item assignment raises `TypeError`; `__copy__`/`__deepcopy__` return `self`. No custom `Mapping` class. Pickle unsupported (noted for T12-1); hashing already was not. 3 new tests, each confirmed to fail against the merge-base before the fix. Rebased onto main after R3 (conflict in the derived status file), which also caught and fixed a stale `startable=true` flag R3's own regeneration had left on R9's entry. Unblocks T6-5 alongside R2 |

**ADRs on `main`:** ADR 0001 ([flow-domain separation](../../docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md))
with Amendment 1, and ADR 0002 ([typed ports](../../docs/ADR_0002_TYPED_PORTS.md)) with
Amendments 1–3 (the last now implemented by T5-7 and consumed by T5-5, closing
its sequencing chain). **Read the ADRs themselves** — summarising them here is
what made this file 1,086 lines.

## Milestone progress

| Milestone | Status |
|---|---|
| **M0**–**M5** | **Complete.** Checkpoint A (M1) and Checkpoint B (M4) both reached |
| **M6** Energy Balance and Temperature | 2/5 — T6-1, T6-2 Complete; T6-3, T6-4, T6-5 all startable |
| **M7** Control Valves and Final Elements | 2/5 — T7-1, T7-2 Complete; T7-3, T7-4, T7-5 startable |
| **MR** Remediation | 6/13 — R1, R2, R3, R4, R9, R11 Complete; R5 Ready for Review; the rest of the no-spine set is startable, plus R10b at the head of the spine queue |
| M8–M19 | Not started |

**47 of 114 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7); M5 is
closed, M6 has landed two tasks and M7 two. MR is scheduled to merge by
Checkpoint C; its remaining spine work is R10b → R6 → R7.

## The next task

**No single task is "the" next one.** Twenty-four tasks are startable in
parallel (table below); which to hand out next is a scheduling choice, not a
dependency one. R10b, R12, T6-5, T7-4 and T13-1 are the Opus-level tasks in
that list. R5 is Ready for Review, not startable — it's already claimed and
awaiting merge.

### Startable now (24 tasks)

| Task | Name | Model | Branch |
|---|---|---|---|
| **R8** | Flow unit label | Sonnet | `fix/flow-unit-label` |
| **R10a** | Documentation corrections | Sonnet | `docs/solved-state-wording` |
| **R10b** | C1/C4 docstring corrections | Opus · spine lock | `docs/c1-c4-docstrings` |
| **R12** | Reconcile spine rules | Opus | `docs/spine-rules` |
| **T6-3** | Heat exchanger model | Sonnet | `feature/heat-exchanger` |
| **T6-4** | Furnace model | Sonnet | `feature/furnace` |
| **T6-5** | Energy propagation through the network | Opus · spine lock | `feature/energy-balance` |
| **T7-3** | Valve fault modes | Sonnet | `feature/valve-faults` |
| **T7-4** | Command arbitration | Opus | `feature/command-arbitration` |
| **T7-5** | Relief device | Sonnet | `feature/relief-valve` |
| **T8-1** | PID block | Sonnet | `feature/pid-block` |
| **T9-1** | Envelope evaluator | Sonnet | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | Sonnet | `feature/alarm-state-machine` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T13-1** | Malfunction model and registry | Opus | `feature/malfunction-model` |
| **T13-5** | Physics isolation guard | Sonnet | `test/import-direction-guard` |
| **T14-1** | Scenario file schema | Sonnet | `feature/scenario-schema` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T15-4** | Score persistence | Sonnet | `feature/score-store` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T17-1** | Ring-buffer historian | Sonnet | `feature/historian` |
| **T18-2** | CI pipeline | Sonnet | `chore/ci-pipeline` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |

**Still waiting:** T8-3, T9-2 and T11-1 — on T8-2 and T9-1, unchanged by T6-1.
From MR: **T18-1** on R7; R6 on R5; R7 on R6.

**Scheduling notes.** MR runs under **one global spine lock** (U1 in the
approved design treats `network.py` and `scheduler.py` as spine too). The spine
queue is now R10b → R6 → R7, one at a time — **R2 released the lock on
merge, and T6-5 runs last in it**, per the approved remediation design,
alongside whichever of R10b/R6/R7 is current. The `app/main.py` lock is held
by R5 (Ready for Review, awaiting merge); R6 is next in that queue once R5
merges. T7-3 edits `app/equipment/valve.py` and should not run beside another
valve change. T12-1 adds a *new* isolated module under `app/engine/`, which
is satellite work under the `app/engine/` rule in AGENTS.md.

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
