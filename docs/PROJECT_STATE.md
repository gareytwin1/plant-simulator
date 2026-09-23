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

**Last state refresh:** 23 September 2026, at `f9ce10e` (T7-2, PR #54; plus a
skills bug fix, PR #56, non-task) —
**this is a snapshot, not a live pointer.** Run `git log f9ce10e..HEAD --oneline`
to see what has merged since.
**Full suite as of this refresh:** **969 passed** · `python -m mypy` clean over 25 source files · no golden trace moved
**In flight:** nothing. **No spine lock is held.** No task is Blocked.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T7-2** | `537f206` | Extract valve logic from the compressor. `GasCompressor` loses `discharge_valve_position`/`target`/`rate`; `FV-201` (`ControlValve`) now sits on K-101's discharge in `olefins_lite.yaml`, over a new internal node N-204. Resistance re-split 0.04 machine + 0.01 valve so every T5-5 design value is unmoved. Dead `/api/valve` endpoint and page slider removed |
| — | `f9ce10e` | (non-task) Fixed `start-task`/`merge-task` skills reading `$1` instead of `$0` for their own argument — Claude Code's positional substitution is zero-based, so both were silently reading an empty string. PR #56 |
| T6-1 | `bb0b9ec` | Stream enthalpy and mixing. `app/plant/thermo.py`: Cp per unit of native flow, flow-weighted mixing at nodes (temperature by flow·Cp, composition by flow — closes energy identically). Imports nothing from `app.plant`, so equipment can import it. Unblocks T6-2 through T6-5 |
| T5-4 | `e54e9df` | Mass balance conservation suite. Writes down ADR 0002 section 7.1's identity against `olefins_lite.yaml`'s cold-start transient; closes tightly cumulatively and per-step, proves the naive published-flow ledger wrong by one step. Tests only — **M5 is now Complete** |
| T5-5 | `37fbc36` | Integrated reference plant. `olefins_lite.yaml`: two domains (liquid P-101, gas K-101) coupled only through V-101 inventory, loaded at its own closed-form design equilibrium. Config + tests only |
| T5-7 | `4db428d` | Vessel configured-port mode via `accepts_configured_ports` |

**ADRs on `main`:** ADR 0001 ([flow-domain separation](ADR_0001_FLOW_DOMAIN_SEPARATION.md))
with Amendment 1, and ADR 0002 ([typed ports](ADR_0002_TYPED_PORTS.md)) with
Amendments 1–3 (the last now implemented by T5-7 and consumed by T5-5, closing
its sequencing chain). **Read the ADRs themselves** — summarising them here is
what made this file 1,086 lines.

## Milestone progress

| Milestone | Status |
|---|---|
| **M0**–**M5** | **Complete.** Checkpoint A (M1) and Checkpoint B (M4) both reached |
| **M6** Energy Balance and Temperature | 1/5 — T6-1 Complete; T6-2, T6-3, T6-4, T6-5 all startable |
| **M7** Control Valves and Final Elements | 2/5 — T7-1, T7-2 Complete; T7-3, T7-4, T7-5 startable |
| M8–M19 | Not started |

**40 of 101 tasks Complete.** Next checkpoint is **C** (M5 + M6 + M7); M5 is
closed, M6 has landed one task and M7 two.

## The next task

**No single task is "the" next one.** Twenty-two tasks are startable in
parallel (table below); which to hand out next is a scheduling choice, not a
dependency one. T6-5, T7-4 and T13-1 are the Opus-level tasks in that list.

### Startable now (22 tasks)

| Task | Name | Model | Branch |
|---|---|---|---|
| **T6-2** | Polytropic compression temperature | Sonnet | `feature/polytropic-temperature` |
| **T6-3** | Heat exchanger model | Sonnet | `feature/heat-exchanger` |
| **T6-4** | Furnace model | Sonnet | `feature/furnace` |
| **T6-5** | Energy propagation through the network | Opus | `feature/energy-balance` |
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
| **T18-1** | Container and WSGI serving | Sonnet | `chore/container-and-ci` |
| **T18-2** | CI pipeline | Haiku | `chore/ci-pipeline` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |
| **T13-5** | Physics isolation guard | Haiku | `test/import-direction-guard` |
| **T15-4** | Score persistence | Haiku | `feature/score-store` |
| **T17-1** | Ring-buffer historian | Haiku | `feature/historian` |

**Still waiting:** T8-3, T9-2 and T11-1 — on T8-2 and T9-1, unchanged by T6-1.

**Scheduling notes.** T6-2 now starts from the `compressor.py` T7-2 left
behind — no discharge-valve state on the device, load/temperature only; the
piecewise temperature table it replaces is unaffected. T7-3 edits
`app/equipment/valve.py` and should not run beside another valve change.
T12-1 adds a *new* isolated module under `app/engine/`, which is satellite
work under the `app/engine/` rule in CLAUDE.md.

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
