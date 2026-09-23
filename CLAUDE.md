# CLAUDE.md

Operating context for Claude Code sessions here — read this first. It holds
what does not change: invariants, contracts and working discipline.

**Read in this order:** this file → [PROJECT_STATE.md](docs/PROJECT_STATE.md)
(what is true right now) → your build plan task and the contract it depends on
([BUILD_PLAN.html](docs/BUILD_PLAN.html), or the
[live artifact](https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9)) →
[ARCHITECTURE.md](docs/ARCHITECTURE.md) if you touch a spine file or interface.
**If this session will write code, read [DEVELOPMENT.md](DEVELOPMENT.md) and
create the worktree before touching a file** — this repo has already lost a
commit to a branch race from skipping that step.

`.claude/rules/*.md` holds rules scoped to specific paths (engine, plant
config, Python style, testing, docs ownership). They apply when Claude works
with files matching their configured `paths`, not as unconditional startup
context — so where a silent violation would be expensive, the rule also gets a
one-line backstop here. Details belong in the scoped rule, never here.

## What this project is

A deterministic, modular plant-equipment simulator built to grow into a
connected **operator-training** simulation: a small process plant an operator
can run, upset, misdiagnose, trip, and be scored on. The bar is *realistic
enough to teach process behaviour*, not rigorous process simulation —
lumped-parameter models, algebraic curves and explicit integration are the
right level. Do not add thermodynamic rigor, compositional property packages,
or numerical sophistication the training goal does not require. Version 1 ships
a **seven-device train**: more equipment is more *instances*, not *mechanisms*.

**Naming.** The compressor is a **"Gas Compressor"** (`GasCompressor`, `K-101`)
— never "Natural Gas Compressor"; the pump is a **"Centrifugal Pump"**
(`CentrifugalPump`, `P-101`). Tags are ISA-style `PREFIX-NNN`, with prefixes in
`app/config.py::TAG_PREFIXES`.

## Interface contracts

Eight contracts (C1–C8) are what let separate agents work without reading each
other's code. They are frozen once a branch depends on one; propose a change as
its own task, never as a side effect.

| Contract | What it is | Status | Authoritative definition |
|---|---|---|---|
| **C1** | Equipment interface | **Implemented** | `app/equipment/base.py` |
| **C2** | Topology: node, branch, stream | **Implemented** | `app/plant/topology.py` |
| **C3** | Plant configuration schema | **Implemented**, including configured-port mode | `config/schema/plant.schema.json`, `app/plant/validate.py`, `app/plant/loader.py` |
| **C4** | State snapshot | **Implemented** | `app/engine/snapshot.py` |
| **C5**–**C8** | HTTP API (single action endpoint); event record; alarm interface; malfunction and scenario | Not implemented | Build plan only |

C5–C8 exist **only as specifications in the build plan** — do not write code
assuming they exist or invent your own version. **Do not write code, comments
or docs implying controllers, alarms, envelopes or scoring exist.** None do.

## Critical architectural invariants

Violating any of these is a contract break, not a style preference.

**Equipment does not own solved plant state.** A device **never reads or writes
a node pressure**; it publishes a curve and the solver finds where the plant
lands on it. `Port` carries *connection metadata, never process state* — the
node it attaches to plus the descriptors below — and `Port.__slots__` makes
that structural. A pressure, flow, temperature or level is a solver output; a
boundary pressure owned by a device is one in disguise, and belongs to the
topology.

**The integrate / characteristic split is the whole point of C1.**

- `integrate(dt)` advances **slow state only** — a load ramp, a valve stroke, a
  vessel level, metal temperature. It is the only method allowed to mutate the
  device, and it never touches flow or pressure. **`integrate(0)` must be a
  no-op**, or a solver iteration would change the plant.
- `characteristic(flow)` is a **pure query**: the pressure change across the
  device at that flow, with slow state wherever `integrate` left it. Positive
  is a rise (machine), negative a drop (valve, pipe). It mutates nothing, so
  the solver may call it as often per timestep as convergence needs.
- **The curve must be monotone non-increasing in flow, everywhere.** This is
  what gives the branch equation exactly one root; a device that violates it
  can break Newton-Raphson convergence for the whole plant, not just its own
  branch. Full convention: [.claude/rules/engine.md](.claude/rules/engine.md).

**A connection is described, never inferred (C1, T3-7).** A port declares
`direction`, `phase`, `purpose` and an optional `control`. **Port names are
identifiers only, never behaviour** — no code branches on a port being called
`suction`, `drain` or anything else, and `tests/test_port_name_guard.py` fails
the build if any does. **Only `phase` and `direction` classify a connection or
participate in conservation**; `purpose` and `control` never change a balance.
Typing comes from **configuration**, never from a device class — an
`isinstance(device, ...)` check or a port-name test deciding a phase is the
inference this rule retires. Full mechanics:
[.claude/rules/plant-config.md](.claude/rules/plant-config.md).

**Time is owned, not observed.** **Never call `time.time()`** or any wall-clock
source inside a model; simulated time arrives only through the injected `dt`.
`SimulationClock` is the single authority for simulated time, speed and pause,
and `Engine` owns integration cadence, consulting only the clock's speed.
Determinism is a hard requirement: same config, same seed, same sequence of
`step(dt)` calls gives bit-identical state forever. **Randomness comes only
from a `SeededRNG`** — no other module in `app/` may import `random`, and
`tests/test_random_source_guard.py` fails the build if one does. Why no global
generator: [.claude/rules/engine.md](.claude/rules/engine.md).

**Snapshot is the read boundary.** `Snapshot` (C4) is the only thing downstream
consumers read — historian, console, trends, scoring, scenarios — and it is
immutable. `controllers`, `envelope` and `alarms` are present but empty by
design, so the UI and game layers can be built against a frozen shape first.
`nodes` and `streams` are **not** in that category: they carry real solved
numbers for any `Engine` built from a plant.

**Golden regressions protect existing physics. Do not regenerate a golden trace
to make a test pass.** If a trace moves, stop and explain why: either that was
the point of the task and it needs justifying, or you have a bug. Regenerating
is legitimate only for non-numeric changes. Tolerances, rationale and the full
policy: [.claude/rules/testing.md](.claude/rules/testing.md).

## Development rules

- **One git worktree per task.** Never let parallel agents share one checkout —
  this repo has already lost a commit to a branch race. Procedure:
  [DEVELOPMENT.md](DEVELOPMENT.md).
- **Spine files take one branch at a time.** Satellites build against frozen
  contracts, merge independently, and rebase onto `main` after every spine
  merge; never merge `main` backwards into the spine. Which files are spine,
  append-only or frozen: [DEVELOPMENT.md](DEVELOPMENT.md#file-ownership).
- **Run the full suite and the type check before review** — `python -m pytest -q`
  and `python -m mypy`, not just the tests you added.
- **Do not modify unrelated files;** unrelated cleanup goes in its own task.
- **Preserve contracts.** If a contract seems wrong, raise it as a task rather
  than working around it silently.
- **Commits are small and singular** — one clear change, a subject line saying
  what changed, task ID first. Examples:
  [DEVELOPMENT.md](DEVELOPMENT.md#naming-conventions).

### Status vocabulary

| Status | Meaning |
|---|---|
| Not Started | No work begun |
| In Progress | Implementation underway |
| Blocked | Waiting on a dependency or a decision; record why in the note |
| **Ready for Review** | Code complete, rebased on current `main`, full suite green, type check green, **not merged** |
| **Complete** | **Merged to `main`.** Nothing else counts. |

A task is never Complete because code exists on a local or pushed branch. The
note on a Complete task should name the merge SHA.

## Agent model guidance

Use the smallest model that can do the work safely — **do not use Opus for
routine work Sonnet can finish safely.**

| Model | Use it for |
|---|---|
| **Sonnet** | Implementation and execution. Feature work, debugging, tests, documentation, Git and shell work, routine refactors, straightforward scripts, mechanical changes, and any work inside an already-decided contract. |
| **Opus** | Architecture and integration judgment. Contract design, spine changes, solver changes, cross-cutting refactors, repository-wide standards, build-plan changes, ambiguous design problems, and work spanning several subsystems. |

In short: **Sonnet implements and executes. Opus decides.** A Sonnet session
that reaches an architectural ambiguity or a contract question **stops and
escalates** — record the question on the task and hand it up; never invent a
design to get unblocked. This section is the authoritative statement of model
selection, and other documents link here rather than restating it.

## Commands

```bash
conda activate plant-simulator
python -m pytest -q   # full suite
python -m mypy        # type check, configured over app/ in pyproject.toml
```

Type hints are **required** in new and modified production code under `app/` —
full rules in [.claude/rules/python.md](.claude/rules/python.md), test
conventions in [.claude/rules/testing.md](.claude/rules/testing.md). More
commands: [DEVELOPMENT.md](DEVELOPMENT.md#environment). Never write
machine-specific interpreter paths into documentation or scripts.

## Where authoritative state lives

| Question | Source |
|---|---|
| What must I never break? Which model runs this? How do I commit? | This file |
| What is true right now? | [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) |
| What is the task list / schedule / contract text? | [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) + [live artifact](https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9) |
| What is each task's current status? | Live artifact; durable snapshot in [docs/BUILD_PLAN_STATUS.json](docs/BUILD_PLAN_STATUS.json) |
| How do current and target architecture differ? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| How do I branch, test and merge? | [DEVELOPMENT.md](DEVELOPMENT.md) |
| What units does a number carry? | [docs/UNITS_CONVENTION.md](docs/UNITS_CONVENTION.md) |
| Which doc owns which fact? | [.claude/rules/docs.md](.claude/rules/docs.md) |
