# CLAUDE.md

Operating context for Claude Code sessions working in this repository — read
this first. Which model should be running a given task is itself a rule here:
see [Agent model guidance](#agent-model-guidance). This file is stable —
architectural invariants and working rules, not current status.

**For what is true right now** — current `main`, what to work on next, open
decisions — read [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md).

`.claude/rules/*.md` carries rules scoped to specific paths (engine, plant
config, Python style, docs ownership) — confirmed to load when a matching file
is opened with `Read` (verified empirically, not just documented). Whether a
file touched only through `Bash`/`grep`/`sed` also triggers it is untested —
treat that as unconfirmed, not as "doesn't happen." They are not listed in the
reading order below for that reason; see
[.claude/rules/docs.md](.claude/rules/docs.md) for which invariants get a
CLAUDE.md-level backstop because of it.

## Reading order for a new session

1. **This file** — invariants, contracts, and working rules.
2. **[docs/PROJECT_STATE.md](docs/PROJECT_STATE.md)** — what is true right now.
3. **The build plan task you are assigned** plus the contract it depends on
   — [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) (durable copy) or the
   [live artifact](https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9).
4. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — if you are touching a
   spine file or any interface.
5. **If this session will write any code: [DEVELOPMENT.md](DEVELOPMENT.md)
   before touching a file.** Create the worktree first — this repo has already
   lost a commit to a branch race from skipping that step.
6. **The relevant source and tests.**
7. Begin work.

## What this project is

A deterministic, modular plant-equipment simulator built to grow into a
connected **operator-training** simulation: a small process plant an operator
can run, upset, misdiagnose, trip, and be scored on.

The engineering bar is *realistic enough to teach process behaviour*, not
rigorous process simulation. Lumped-parameter models, algebraic curves, and
explicit integration are the right level. Do not add thermodynamic rigor,
compositional property packages, or numerical sophistication that the training
goal does not require.

Version 1 ships a **seven-device train**, not a complete olefins plant. The bet
is that a complete small plant proves every mechanism a large one would; more
equipment is more *instances*, not more *mechanisms*.

## Naming

- The compressor is a **"Gas Compressor"** (`GasCompressor`, tag `K-101`).
  Never "Natural Gas Compressor".
- The pump is a **"Centrifugal Pump"** (`CentrifugalPump`, tag `P-101`).
- Equipment tags are ISA-style `PREFIX-NNN`. Prefixes live in
  `app/config.py::TAG_PREFIXES`: `K` compressor, `P` pump, `E` heat exchanger,
  `V` vessel, `FV` control valve.

## Interface contracts

Eight contracts (C1–C8) are what let separate agents work without reading each
other's code. They are frozen once a branch depends on them; propose a change as
its own task, never as a side effect.

| Contract | What it is | Status | Authoritative definition |
|---|---|---|---|
| **C1** | Equipment interface | **Implemented** | `app/equipment/base.py` |
| **C2** | Topology: node, branch, stream | **Implemented** | `app/plant/topology.py` |
| **C3** | Plant configuration schema | **Implemented**, including configured-port mode (T5-7) | `config/schema/plant.schema.json`, validator `app/plant/validate.py`, loader `app/plant/loader.py` |
| **C4** | State snapshot | **Implemented** | `app/engine/snapshot.py` |
| **C5** | HTTP API (single action endpoint) | Not implemented | Build plan only |
| **C6** | Event record (alarms/trips/actions) | Not implemented | Build plan only |
| **C7** | Alarm interface | Not implemented | Build plan only |
| **C8** | Malfunction and scenario | Not implemented | Build plan only |

C5–C8 exist **only as specifications in the build plan**. Do not write code that
assumes they exist, and do not invent your own version of them — read the
contract text in the build plan first. **Do not write documentation, comments,
or code that implies controllers, alarms, envelopes, or scoring already
exist** — none of them do yet.

## Critical architectural invariants

Violating any of these is a contract break, not a style preference.

**Equipment does not own plant state.**
- A device **never reads or writes a node pressure**. It publishes a curve; the
  solver finds where the plant lands on it.
- `Port` carries *connection metadata, never process state*. Metadata is the
  node it is attached to and the four descriptors below: declared, not
  computed, and unchanged while the plant runs. Process state is a pressure, a
  flow, a temperature, a level — all solver outputs. `Port.__slots__` makes the
  line structural: you cannot stash a solved value on a port even by accident.
- A boundary pressure owned by a device is a solver output in disguise and
  belongs to the topology.

**The integrate / characteristic split is the whole point of C1.**
- `integrate(dt)` advances **slow state only** — a load ramp, a valve stroke, a
  vessel level, metal temperature. It is the only method allowed to mutate the
  device, and it never touches flow or pressure.
- `integrate(0)` **must be a no-op**. Nothing may move without simulated time
  passing, or a solver iteration would change the plant.
- `characteristic(flow)` is a **pure query**: the pressure change across the
  device at that flow, with slow state wherever `integrate` left it. Positive is
  a rise (machine), negative is a drop (valve, pipe). It mutates nothing, so the
  solver may call it as many times per timestep as its iteration needs.
- **The curve must be monotone non-increasing in flow, everywhere.** This is
  what gives the branch equation exactly one root; a device that violates it
  can break Newton-Raphson convergence for the whole plant, not just itself.
  Full convention (signed flow, `signed_square`, the residual formula) is in
  [.claude/rules/engine.md](.claude/rules/engine.md).
- `get_state()` returns flat, JSON-safe primitives — the device's row in the
  snapshot.
- `reset()` restores construction state exactly. Port wiring survives it; the
  topology owns wiring.

**A connection is described, never inferred (C1, T3-7).** A port carries
`direction`, `phase` (`liquid` | `vapor`), `purpose` (`process` | `vent` |
`drain` | `relief`) and an optional `control` (`flow` | `pressure` | `level` |
`temperature`). **Port names are identifiers only** — never behaviour; no code
branches on a port being called `suction`, `drain` or anything else, and
`tests/test_port_name_guard.py` fails the build if any does. **Only `phase` and
`direction` classify a connection or participate in conservation; `purpose` and
`control` are descriptive and never change a balance.** Typing comes from
**configuration**, never from a device class — an `isinstance(device, ...)`
check or a port-name test that decides a phase is exactly the inference this
rule exists to retire. Full mechanics, including configured-port mode (only
`Vessel` opts in), live in
[.claude/rules/plant-config.md](.claude/rules/plant-config.md).

**Time is owned, not observed.**
- **Never call `time.time()`** or any wall-clock source inside a model. Simulated
  time arrives only through the injected `dt`.
- `SimulationClock` is the single authority for simulated time, speed, and pause.
- `Engine` owns integration cadence. It calls `integrate(elapsed)` on every
  device and consults only the clock's speed — never a device's own
  `simulation_speed`.
- Determinism is a hard requirement: same config, same seed, same sequence of
  `step(dt)` calls must give bit-identical state forever.
- **Randomness comes only from a `SeededRNG`.** No other module in `app/` may
  import `random` — `tests/test_random_source_guard.py` fails the build if one
  does. Full reasoning (why no global generator) in
  [.claude/rules/engine.md](.claude/rules/engine.md).

**Snapshot is the read contract.**
- `Snapshot` (C4) is the only thing downstream consumers read — historian,
  console, trends, scoring, scenarios. It is immutable: every mapping is a
  `MappingProxyType` over a deep copy.
- `controllers`, `envelope` and `alarms` are present but empty by design —
  those subsystems don't exist yet, and the shape is frozen now so the UI and
  game layers can be built before the physics behind them exists. `nodes` and
  `streams` are **not** in that category: they carry real solved numbers for
  any `Engine` built from a plant (since T4-4) — see
  [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the current vs. target split.

**Golden regressions protect existing physics.**
- `tests/fixtures/golden/*.json` pin the current numbers. Tolerances
  (1e-5 relative, 1e-7 absolute) were chosen deliberately: ten orders above
  float noise, three below the 1% drift the harness exists to catch.
- **Do not regenerate a golden trace to make a test pass.** If a trace moves,
  stop and explain why. A moved trace means behaviour changed — either that was
  the point of the task and it needs justifying, or you have a bug.
- Regenerating is legitimate only for non-numeric changes (e.g. a recorded
  command-description string that names a renamed attribute).

## Development rules

- **One git worktree per task.** Never let parallel agents share one checkout —
  this repo has already lost a commit to a branch race, which is why the rule
  exists. Full procedure: [DEVELOPMENT.md](DEVELOPMENT.md).
- **Spine files take one branch at a time.** Satellites build against frozen
  contracts and merge independently.
- **Rebase satellites onto `main` after every spine merge.** Never merge `main`
  backwards into the spine.
- **Run the full suite and the type check before review** — `python -m pytest -q`
  and `python -m mypy` — not just the tests you added.
- **Do not modify unrelated files.** Unrelated cleanup goes in its own task.
- **Preserve contracts.** If a contract seems wrong, raise it as a task; do not
  work around it silently.
- **Status vocabulary is exact** — see below.

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

Use the smallest model that can do the work safely. This is guidance, not a
restriction — but **do not use Opus for routine mechanical work Haiku or Sonnet
can finish safely.**

| Model | Use it for |
|---|---|
| **Haiku** | Execution. Shell and Git commands, small Bash or Python scripts, simple file operations, small documentation edits, formatting, straightforward test additions, repetitive typing fixes, simple `mypy` fixes where the intended type is already clear — mechanical work with little architectural ambiguity. |
| **Sonnet** | Implementation. Feature work, debugging, API work, test development, refactors inside an established contract, most satellite tasks, moderate multi-file changes, building a design someone already decided. |
| **Opus** | Architecture and integration judgment. Contract design, spine changes, the network solver, integration reviews, build-plan changes, cross-cutting refactors, repository-wide standards, anything spanning several subsystems or milestones, and ambiguous problems where the design has to be worked out before any code is written. |

In short: **Haiku executes, Sonnet implements, Opus decides.**

A smaller model that runs into architectural ambiguity or a contract question
**stops and escalates** — record the question on the task and hand it up. Never
invent a design to get unblocked.

This section is the authoritative statement of model selection. Other documents
link here rather than restating it.

## Commit discipline

Small commit, clear purpose, short message: one commit is one clear change,
subject lines say what changed (not how), lead with the task ID when there is
one. Full convention and examples: [DEVELOPMENT.md](DEVELOPMENT.md#naming-conventions).

## File ownership and high-conflict areas

Which files are spine (one branch at a time), append-only, or frozen — check
before you start editing. Full table:
[DEVELOPMENT.md](DEVELOPMENT.md#file-ownership).

## Commands

```bash
conda activate plant-simulator
python -m pytest -q   # full suite
python -m mypy        # type check, configured over app/ in pyproject.toml
```

More commands (single-test runs, running the app) in
[DEVELOPMENT.md](DEVELOPMENT.md#environment). Do not write machine-specific
interpreter paths into documentation or scripts.

## Code style and typing

Type hints are **required** in new and modified production code under `app/`;
match the surrounding code's formatting otherwise. Full rules — what must be
typed, `Any` policy, style details — are in
[.claude/rules/python.md](.claude/rules/python.md) (`app/**/*.py`). Test
conventions, including the golden-trace policy, are
[.claude/rules/testing.md](.claude/rules/testing.md) (`tests/**/*.py`) — kept
separate so the two rules don't co-fire and repeat each other.

## Where authoritative state lives

| Question | Source |
|---|---|
| What must I never break? | This file |
| Which model should run this task? How should I commit? | This file — [Agent model guidance](#agent-model-guidance), [Commit discipline](#commit-discipline) |
| What is true right now? | [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) |
| What is the task list / schedule / contract text? | [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) + [live artifact](https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9) |
| What is each task's current status? | Live artifact; durable snapshot in [docs/BUILD_PLAN_STATUS.json](docs/BUILD_PLAN_STATUS.json) |
| How do current and target architecture differ? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| How do I branch, test, and merge? | [DEVELOPMENT.md](DEVELOPMENT.md) |
| What units does a number carry? | [docs/UNITS_CONVENTION.md](docs/UNITS_CONVENTION.md) |
| Which doc owns which fact? | [.claude/rules/docs.md](.claude/rules/docs.md) |
