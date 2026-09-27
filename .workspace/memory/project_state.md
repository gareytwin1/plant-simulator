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

**Last state refresh:** 27 September 2026, at `f3cc8b6` (Merge T8-4: Execute
control loops in the engine step, PR #99) - **this is a snapshot,
not a live pointer.** Run `git log f3cc8b6..HEAD --oneline` to see what has
merged since.
**Full suite as of this refresh:** **1705 passed** · `python -m mypy` clean over 45 source files · compressor golden trace moved deliberately (temperature field only, on the two boundary-asymmetric scenarios - justified in T6-2's note)
**In flight:** nothing.
**No spine lock is held.** No task is Blocked. CI runs on every PR, and `main` requires its
`test` check before a merge.

**Recent merges** (full notes in [BUILD_PLAN_STATUS.json](../../docs/BUILD_PLAN_STATUS.json)):

| Task | SHA | What landed |
|---|---|---|
| **T8-4** | `f3cc8b6` | Loop execution in the engine step. `Engine.step()` is now clock -> control -> integrate -> couple/solve -> publish: each loop reads its PV from the indicated view the previous step published and posts its output to `Engine.arbiter` (T7-4) as a controller demand, so a published measurement strokes the valve within the very next step and no loop can see a mid-solve value. Config order, order-independent by construction; a stopped step runs no loop; a loop is primed on first execution so AUTO starts bumpless. The snapshot's `controllers` section now carries `pv`/`sp`/`out`/`mode`. `load_loops()` seeds a loop from its valve's current command, and `Plant.passthrough()` replaces a `to_config()` round-trip. **PIC-101 must stay MANUAL** - the PID is wrong-acting for a vent valve; T8-6 was added to the build plan to fix it. Unblocks T8-5 (V1.1-deferred) and T8-6 |
| **T9-3** | `0bb2e25` | Time-in-band and excursion tracking. `ExcursionTracker` in `app/envelope/tracker.py` accumulates per-severity time-in-band (WARNING/ALARM/TRIP) and the single worst excursion (magnitude + timestamp) for one monitored point, from severities its caller's own T9-1 `Evaluator` already classified - hysteresis stays owned by `Evaluator` alone. A roborev finding (magnitude reads `0.0` while a deadband-held severity is non-normal) was fixed by documenting the interaction and adding a regression test driving the tracker from a real `Evaluator`. Closes M9's third task; unblocks T9-4 and T14-3 on the dependency side (T9-4 separately needs the spine lock, currently held by T8-4) |
| **T8-3** | `619af4e` | Loop configuration and tag wiring (C3's `controllers` key). `app/controls/loader.py`'s `load_loops()` resolves each entry into a `Loop`/`PID` (T8-1/T8-2): `pv` resolves against a node id - a node has exactly one measured quantity, its pressure, so the bare tag is unambiguous with no C3 change, sidestepping the "instruments are not in C3" gap below - and `out` resolves against an explicit per-class allowlist of settable devices (`ControlValve.set_position_target` only, mirroring `app.disturbances.malfunction.WRITABLE`). Two loops claiming one `out` tag is rejected (roborev), and an entry with a bad `pv` cannot itself "claim" an `out` tag it never actually got to drive (also roborev). Wires `PIC-101` (`N-201` -> `PV-101`) into `config/plants/olefins_lite.yaml`, configured MANUAL since nothing reads `controllers` at engine-step time yet. Rebased over T9-2/T10-2's own `olefins_lite.yaml` additions (trivial conflict, both top-level keys kept). Unblocks T8-4 |
| **T9-2** | `dee5cd8` | Envelope limits in plant config (C3). `app/envelope/loader.py`'s `load_limits()` binds the `limits` key to T9-1's `Evaluator`/`Limits`: `lo`/`hi` become `warning_lo`/`warning_hi`, and `lo_lo`/`hi_hi` become `trip_lo`/`trip_hi` when `trip` is true or `alarm_lo`/`alarm_hi` otherwise - one severity choice per variable, not per side. Ordering violations reuse `Limits.__post_init__`, re-raised with the offending `tag.variable`; a repeated `(tag, variable)` entry is rejected rather than silently overwritten (found by roborev). `get_limit()` warns rather than crashes on a tag with no configured limits. `config/plants/olefins_lite.yaml` gains only its `limits` key (`V-101.level`, `K-101.discharge_pressure`, `P-101.flow`) at the plant's design point. Unblocks nothing on its own - T9-4 also needs T9-3 |
| **T10-2** | `d8d59c3` | Alarm manager (C7). `AlarmManager` in `app/alarms/manager.py` binds one `Alarm` (T10-1) per monitored point (tag+pv, collision-free) to severity from an `Evaluator` (T9-1). Every band change - keyed on `(severity, side)` together, not severity alone - emits a re-prioritised `Event` in the C6 shape; a reportable change on an already-ACKED alarm returns it to UNACK, since the operator signed off on the previous band, not the new one. Messages are symptom-only by construction (tag + pv + ISA-style HI/HIHI/HIHIHI suffix). Priorities come from a configurable `Severity -> Priority` mapping. Closes 2/5 of M10; unblocks T10-3, T10-4 (T10-4 is V1.1-deferred) |
| **T7-5** | `17ae66d` | Relief device. `ReliefValve` in `app/equipment/relief.py`: a resistance with hysteresis rather than a commanded position - pops open at `set_pressure`, recloses at `set_pressure - blowdown`, closed as a small leak (`CLOSED_LEAK_FRACTION`) rather than a seal, same divide-by-zero reason `ControlValve` floors at `min_position`. Registered in `DEVICE_TYPES`, the plant schema, `TAG_PREFIXES` (`PSV`) and the malfunction `WRITABLE` allowlist. Also took the spine lock for one small, deliberate `app/engine/coupling.py` change beyond its own file list: `FLOW_UNITS` now lists it unit-neutral, and `VesselCoupling.write_boundary_pressures()` senses `inlet_pressure` onto it - a relief valve beside a vessel now lifts, passes flow and recloses through a real `Engine.step()` with nothing hand-driven. Closes M7 (5/5), which closes Checkpoint C; unblocks nothing (deferred, blocks nothing per the build plan) |

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
| **M8** PID Controllers and Modes | 4/6 - T8-1 through T8-4 Complete; T8-6 startable; T8-5 startable (V1.1-deferred) |
| **M9** Operating Envelopes | 3/4 - T9-1, T9-2, T9-3 Complete; T9-4 startable (takes the spine lock) |
| **M10** Alarms | 2/5 - T10-1, T10-2 Complete; T10-3 startable |
| **M13** Malfunctions | 2/5 - T13-1, T13-2 Complete; T13-3, T13-4, T13-5 startable |
| **M14** Scenario Engine | 1/6 - T14-1 Complete; T14-2, T14-3 startable |
| **MR** Remediation | 13/13 - **Complete.** R1-R12, R8 all merged |
| **M18** Deployment and Operations | 1/5 - T18-2 Complete |
| M11, M12, M15–M17, M19 | None Complete |

**73 of 115 tasks Complete.** Checkpoint **C** (M5 + M6 + M7) is now reached -
all three closed. Next checkpoint is **D** (M8), which has landed four of its
six tasks; T8-6 is what lets its "loops reject an injected disturbance" gate
hold on the reference plant, since PIC-101 cannot run in AUTO until it lands.
**MR is now fully merged.**

## The next task

**T8-6** is the Opus task among twenty startable (C3 contract change, no spine
file). **T9-4** is the one startable task that takes the spine lock
(`app/engine/snapshot.py`), which is now free. Which Sonnet task to hand out
next is otherwise a scheduling choice, not a dependency one.

### Startable now (20)

| Task | Name | Model | Branch |
|---|---|---|---|
| **T8-6** | Controller action (direct / reverse) | **Opus** · C3 contract change | `feature/controller-action` |
| **T8-5** | Cascade control (V1.1-deferred) | Sonnet | `feature/cascade-control` |
| **T9-4** | Envelope status in the snapshot | Sonnet · takes the spine lock | `feature/envelope-in-snapshot` |
| **T10-3** | Alarm history and acknowledge | Sonnet | `feature/alarm-history` |
| **T10-4** | Flood suppression and first-out (V1.1-deferred) | Sonnet | `feature/alarm-flood-control` |
| **T11-1** | Interlock definitions and evaluator | Sonnet | `feature/interlock-evaluator` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T13-3** | Injection profiles | Sonnet | `feature/malfunction-profiles` |
| **T13-4** | Malfunction catalogue | Sonnet | `feature/malfunction-catalogue` |
| **T13-5** | Physics isolation guard | Sonnet | `test/import-direction-guard` |
| **T14-2** | Trigger evaluator | Sonnet | `feature/scenario-triggers` |
| **T14-3** | Objective evaluator | Sonnet | `feature/scenario-objectives` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T15-4** | Score persistence | Sonnet | `feature/score-store` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T17-1** | Ring-buffer historian | Sonnet | `feature/historian` |
| **T18-1** | Container and WSGI serving | Sonnet · one worker process | `chore/container-and-ci` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |
| **T18-5** | Session lifecycle and config versioning | Sonnet | `feature/lifecycle-versioning` |

**Scheduling notes.** The spine lock is **one global lock**, now the standing
rule (R12, [DEVELOPMENT.md](../../DEVELOPMENT.md#file-ownership)); it is
free since T8-4 merged. **T18-1 must run exactly one Gunicorn
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
