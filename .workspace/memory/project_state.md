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

**Last state refresh:** 25 September 2026, at `33aa68f` (Merge R7: Atomic
admission and permanent closure, PR #72) — **this is a snapshot, not a live
pointer.** Run `git log 33aa68f..HEAD --oneline` to see what has merged since.
**Full suite as of this refresh:** **1106 passed** · `python -m mypy` clean over 26 source files · compressor golden trace moved deliberately (temperature field only, on the two boundary-asymmetric scenarios — justified in T6-2's note)
**In flight:** nothing. **No spine lock is held** — R7 released it; T6-5 is
the last task in the remediation spine queue and takes it next. No task is
Blocked.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **R7** | `33aa68f` | Atomic admission and permanent closure. `SessionRegistry` builds a `Session` outside a new registry `_lock`, then under it returns an already-admitted entry (ending the unstarted loser) or evicts the LRU session, closing and joining it, before inserting. `get`, `end` and `__len__` take the lock. `Scheduler.start()` is a no-op once `close()`d, and `Session.end()` closes both schedulers, so a request holding an evicted session cannot start an uncounted worker. Eviction stalls lookups for the victim's current step or command, by D6's design. T18-1 must run one Gunicorn worker process. Releases the spine lock — T6-5 is next |
| **R6** | `617bc9e` | Coherent commands and atomic manual step. `Scheduler.command()` holds `step_lock`, applies an operator action, republishes `engine.snapshot()` without advancing time. `step_once()` takes `_lifecycle` then `step_lock`; refused while running or once `close()`d, but still allowed after a worker stopped on an error. Every command route and both manual-step routes in `app/main.py` go through these two methods; a closed session answers 409 "session ended". `Session.step_compressor`/`step_pump` (the unlocked, unpublished bypass behind the bug) are removed. Releases the spine lock and the `app/main.py` lock — R7 is next |
| **R10b** | `70d301d` | C1/C4 docstring corrections. `app/equipment/base.py`'s `Equipment.characteristic` docstring mirrors R10a's AGENTS.md wording verbatim; `app/engine/snapshot.py`'s module docstring stops calling `nodes`/`streams` "present but empty" — they carry real solved numbers for any `Engine` built from a plant, since T4-4. Docstrings only, no code change |
| **R10a** | `07e69df` | Documentation corrections. AGENTS.md's solved-state paragraph replaced in place: inventory is device slow state, reaching the plant only as a boundary the coupling writes; the `integrate(dt)` bullet now says it never touches a solved flow or a node pressure. D9 golden-regeneration wording in AGENTS.md and `.claude/rules/testing.md` now allows an approved numeric change that is the task's own point, with a field-level old/new comparison in the PR — reconciling the written policy with the T6-2 precedent |
| **R5** | `1467dcc` | Request validation. `_number_field()` helper in `app/main.py`, used by `/api/load` and `/api/pump/speed`: requires a JSON object with a finite int/float at the named key, rejects missing/null/string/bool/array/NaN/non-JSON body with 400. 16 new defect-reproduction tests, each confirmed to fail against the merge-base before the fix |
| **R11** | `e4357f3` | Check in the status generator. `scripts/build_plan_status.py` regenerates `docs/BUILD_PLAN_STATUS.json` from `BUILD_PLAN.html`'s `TASKS` array plus a `taskStatus` export, via `node`; validated to reproduce the current file byte-for-byte before use |

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
| **MR** Remediation | 11/13 — R1–R7, R9, R10a, R10b, R11 Complete; R8 and R12 remain, both no-spine and startable |
| M8–M19 | Not started |

**52 of 114 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7); M5 is
closed, M6 has landed two tasks and M7 two. MR is scheduled to merge by
Checkpoint C; its spine work is done, and T6-5 closes the spine queue.

## The next task

**No single task is "the" next one.** Twenty-three tasks are startable, and
with R7 merged that now includes T6-5 for real. Which to hand out next is a
scheduling choice, not a dependency one. T6-5, R12, T7-4 and T13-1 are the
Opus-level tasks among them.

### Startable now (23)

| Task | Name | Model | Branch |
|---|---|---|---|
| **R8** | Flow unit label | Sonnet | `fix/flow-unit-label` |
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
| **T18-1** | Container and WSGI serving | Sonnet · one worker process | `chore/container-and-ci` |
| **T18-2** | CI pipeline | Sonnet | `chore/ci-pipeline` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |

**Still waiting:** T8-3, T9-2 and T11-1 — on T8-2 and T9-1, unchanged by T6-1.

**Scheduling notes.** MR runs under **one global spine lock** (U1 in the
approved design treats `network.py` and `scheduler.py` as spine too). Only
**T6-5** is left in the spine queue. **T18-1 must run exactly one Gunicorn
worker process** — `SessionRegistry` is per-process (R7). T7-3 edits
`app/equipment/valve.py` and should not run beside another valve change. T12-1 adds a *new* isolated module under `app/engine/`, which is
satellite work under the `app/engine/` rule in AGENTS.md.

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
