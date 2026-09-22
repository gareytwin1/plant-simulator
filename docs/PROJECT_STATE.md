# Project State

What is true **right now**. This file goes stale by design; the stable rules are
in [CLAUDE.md](../CLAUDE.md) and the architecture is in
[ARCHITECTURE.md](ARCHITECTURE.md).

**Refresh this file when a task merges.** Keep it under ~200 lines: a merged
task gets its **one-line** entry here, and its full completion note goes in
[BUILD_PLAN_STATUS.json](BUILD_PLAN_STATUS.json). Per-task handoff sections do
not accumulate here — that is what grew this file to 1,086 lines once already.

---

## Right now

**Last refreshed:** 22 September 2026
**Current `main`:** `6a3fb50` (code `c7bd412`, T5-5 merged as `37fbc36`, PR #49)
**Full suite:** **933 passed** · `python -m mypy` clean over 24 source files · no golden trace moved
**In flight:** nothing. **No spine lock is held.** No task is Blocked.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T5-5** | `37fbc36` | Integrated reference plant. `olefins_lite.yaml`: two domains (liquid P-101, gas K-101) coupled only through V-101 inventory, loaded at its own closed-form design equilibrium. Config + tests only |
| T5-7 | `4db428d` | Vessel configured-port mode via `accepts_configured_ports` |
| T5-6 | `7643281` | Coupling aggregates over hydraulic nodes; `DOMAIN_UNITS` retired |
| T3-7 | `8733b1a` | Typed ports: `phase` / `purpose` / `control` + port-name guard |
| T2-6 | `4e48949` | Scheduler owns simulated time |
| T5-3 | `42dc227` | Gas-phase pressure accumulation |
| T7-1 | `fefa841` | Control valve model |
| T5-2 | `154385c` | Level-to-hydraulics coupling; multi-domain Engine |
| T4-4 | `0efa5be` | Solver wired into the Engine (Checkpoint B) |

**ADRs on `main`:** ADR 0001 ([flow-domain separation](ADR_0001_FLOW_DOMAIN_SEPARATION.md))
with Amendment 1 · ADR 0002 ([typed ports](ADR_0002_TYPED_PORTS.md)) with
Amendments 1–3, the last of which is now fully implemented (T5-7) and consumed
(T5-5), closing its sequencing chain. **Read the ADRs themselves** — they are the authority, and
summarising them here is what made this file 1,086 lines.

## Milestone progress

| Milestone | Status |
|---|---|
| **M0**–**M4** | **Complete.** Checkpoint A (M1) and Checkpoint B (M4) both reached |
| **M5** Inventory and Mass Balance | **6/7** — only T5-4 remains, and it is startable |
| **M7** Control Valves | 1/5 — T7-1 Complete; T7-2, T7-3, T7-4, T7-5 startable |
| M6, M8–M19 | Not started |

**37 of 101 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7), and it is
not close: M6 has not started.

## The next task

**T5-4, the mass balance conservation suite** (Sonnet) — T5-5 was its last
blocker. See *Traps for the next tasks* below before writing a line of it.

### Startable now (21 tasks)

| Task | Name | Model | Branch |
|---|---|---|---|
| **T5-4** | Mass balance conservation suite | Sonnet | `test/mass-balance` |
| **T7-2** | Extract valve logic from the compressor | Opus | `refactor/extract-compressor-valve` |
| **T6-1** | Stream enthalpy and mixing | Opus | `feature/stream-enthalpy` |
| **T7-3** | Valve fault modes | Sonnet | `feature/valve-faults` |
| **T7-4** | Command arbitration | Sonnet | `feature/command-arbitration` |
| **T7-5** | Relief device | Sonnet | `feature/relief-valve` |
| **T13-1** | Malfunction model and registry | Opus | `feature/malfunction-model` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T8-1** | PID block | Sonnet | `feature/pid-block` |
| **T9-1** | Envelope evaluator | Sonnet | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | Sonnet | `feature/alarm-state-machine` |
| **T14-1** | Scenario file schema | Sonnet | `feature/scenario-schema` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T18-1** | Container and WSGI serving | Sonnet | `chore/container-and-ci` |
| **T18-2** | CI pipeline | Haiku | `chore/ci-pipeline` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |
| **T13-5** | Physics isolation guard | Haiku | `test/import-direction-guard` |
| **T15-4** | Score persistence | Haiku | `feature/score-store` |
| **T17-1** | Ring-buffer historian | Haiku | `feature/historian` |

**Still waiting:** T8-3, T9-2 and T11-1 — on T8-2 and T9-1, no longer on T5-5.

**Scheduling notes.** T5-4 and T7-2 both edit files T5-5 just created; T7-3 edits
`app/equipment/valve.py` and should not run beside another valve change. T12-1
adds a *new* isolated module under `app/engine/`, which is satellite work under
the `app/engine/` rule in CLAUDE.md.

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
- **The `Equipment.characteristic` docstring overstates the Jacobian.** It
  claims a non-increasing curve "keeps the Jacobian from going singular", but
  for `-K * signed_square(q)` the derivative is `-2K|q|`, **zero at q = 0**. The
  honest claim is monotone non-increasing with exactly one root. `base.py` is
  frozen; whoever next opens C1 should correct the wording.
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

**T5-4's "10,000 steps with no drift" criterion is only now a genuine one.** It
was written against a vessel that fills monotonically: with no liquid draw, the
only way to pass was to stop the run before level clamped at 1.0 — a test-horizon
trick standing in for missing physics, which is why ADR 0002 §8.2 re-sequenced
T5-4 after T5-5. T5-5's plant reaches a real steady state. **Do not shorten a
horizon to make a conservation test pass**; that is the exact defect the
re-sequencing removed.

**T5-4 owns writing down the conservation identity** (ADR 0002 §7.1).
`Engine._couple()` runs in the constructor, so a flow exists before the first
`step()` and each `integrate` consumes the flow solved on the *previous* pass:

```text
Δinventory  =  Σ (n = 0 … N−1)  q_n · dt / 60
```

with `q₀` read from `engine.snapshot()` **before** the first step. Summing the N
*published* flows instead is wrong by `(q_N − q₀)·dt/60` — measured at **2.36
scf over 10,000 gas steps, 0.09%**, four orders outside any sensible tolerance,
and it reads as a physics bug. It hides completely on a constant-flow domain.
**Record it; do not paper over it in a fixture.**

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
