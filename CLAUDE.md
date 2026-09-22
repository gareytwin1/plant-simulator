# CLAUDE.md

Operating context for Claude Code sessions working in this repository — read
this first. Which model should be running a given task is itself a rule here:
see [Agent model guidance](#agent-model-guidance). It is stable: architectural invariants and working
rules, not current status.

**For what is true right now** — current `main`, test count, which branches are
in flight, what to work on next — read [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md).

## Reading order for a new session

1. **This file** — invariants, contracts, and working rules.
2. **[docs/PROJECT_STATE.md](docs/PROJECT_STATE.md)** — what is true right now.
3. **The build plan task you are assigned** plus the contract it depends on
   — [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) (durable copy) or the
   [live artifact](https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9).
4. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — if you are touching a
   spine file or any interface.
5. **[DEVELOPMENT.md](DEVELOPMENT.md)** — branch, worktree, and merge procedure.
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
contract text in the build plan first.

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
- `get_state()` returns flat, JSON-safe primitives — the device's row in the
  snapshot.
- `reset()` restores construction state exactly. Port wiring survives it; the
  topology owns wiring.

**A connection is described, never inferred (C1, T3-7).**
- A port carries `direction`, `phase` (`liquid` | `vapor`), `purpose`
  (`process` | `vent` | `drain` | `relief`) and an optional `control` (`flow` |
  `pressure` | `level` | `temperature`). `phase` `mixed` is reserved and
  refused at load. See [docs/ADR_0002_TYPED_PORTS.md](docs/ADR_0002_TYPED_PORTS.md),
  Amendment 1, which supersedes that record's single `service` axis.
- **Port names are identifiers only.** A name exists for humans, configuration
  and diagnostics and **never drives engineering behaviour** — no code branches
  on a port being called `suction`, `drain` or anything else.
  `tests/test_port_name_guard.py` fails the build if any does.
- **`phase` and `direction` own conservation classification.** They, and
  nothing else, decide which balance a connection belongs to.
- **`purpose` never changes a balance.** It is descriptive physical-role
  metadata. A vapor withdrawal is a vapor withdrawal whether it is labelled
  `process`, `vent` or `relief`.
- **`control` never changes a balance.** It is descriptive control-role
  metadata, and it is deliberately not called `controller`. A controller may
  later read it to find the final element it manipulates, but controller
  execution is M8 and **does not exist yet**.
- `purpose` and `control` are **orthogonal**: a level-controlled drain declares
  both, a manual drain declares only `purpose`. Never collapse them back into
  one field.
- Typing comes from **configuration**, never from a device class. **Fixed-port
  equipment** — every device except `Vessel` — declares its ports by name and
  direction in its class, and the C3 loader puts the descriptors on the
  runtime `Port`. An `isinstance(device, ...)` or a port-name test that
  decides a phase is the inference T3-7 exists to retire.
- **Configured-port mode (ADR 0002 Amendment 3, T3-7).** A device class may
  instead opt in — only `Vessel` does, through a class-level
  `accepts_configured_ports` marker — to receiving its structural port set
  from C3. In that mode configuration supplies `direction` on every port, as
  a typed entry, and the loader builds the device's ports from them, in
  config order, replacing its default set. No `direction` anywhere keeps the
  fixed ports. `direction` on only some entries, or on fixed-port equipment,
  is rejected, naming every offending path. `accepts_configured_ports` is an
  internal capability marker, not a design value: `design` may not set it, on
  any device.
- T3-7 built this vocabulary; **T5-6 consumes it.** The coupling classifies a
  connection from its declared `phase`, and aggregates the exchange of each
  hydraulic **node** exactly once — not once per port. See
  [docs/ADR_0002_TYPED_PORTS.md](docs/ADR_0002_TYPED_PORTS.md), Amendment 2.
  `purpose` and `control` are read nowhere on that path.

**Time is owned, not observed.**
- **Never call `time.time()`** or any wall-clock source inside a model. Simulated
  time arrives only through the injected `dt`.
- `SimulationClock` is the single authority for simulated time, speed, and pause.
- `Engine` owns integration cadence. It calls `integrate(elapsed)` on every
  device and consults only the clock's speed — never a device's own
  `simulation_speed`.
- Determinism is a hard requirement: same config, same seed, same sequence of
  `step(dt)` calls must give bit-identical state forever.
- **Randomness comes only from a `SeededRNG`** (`app/engine/rng.py`, T2-2). No
  other module in `app/` may import `random` —
  `tests/test_random_source_guard.py` fails the build if one does. There is
  deliberately no global generator: each session owns its own plant, so a
  process-wide stream would leak draws between sessions. Which object owns a
  plant's RNG is undecided; the first task that needs randomness decides it.

**Snapshot is the read contract.**
- `Snapshot` (C4) is the only thing downstream consumers read — historian,
  console, trends, scoring, scenarios. It is immutable: every mapping is a
  `MappingProxyType` over a deep copy.
- Sections with no subsystem yet (`nodes`, `streams`, `controllers`, `envelope`,
  `alarms`) are present but empty by design. The shape is frozen now so the UI
  and game layers can be built before the physics behind them exists.

**Golden regressions protect existing physics.**
- `tests/fixtures/golden/*.json` pin the current numbers. Tolerances
  (1e-5 relative, 1e-7 absolute) were chosen deliberately: ten orders above
  float noise, three below the 1% drift the harness exists to catch.
- **Do not regenerate a golden trace to make a test pass.** If a trace moves,
  stop and explain why. A moved trace means behaviour changed — either that was
  the point of the task and it needs justifying, or you have a bug.
- Regenerating is legitimate only for non-numeric changes (e.g. a recorded
  command-description string that names a renamed attribute).

## Current runtime vs. target architecture

**This distinction matters more than anything else in this file.** Documentation
that blurs it has repeatedly misled sessions into assuming the solver exists.

**What the application actually does today (since T4-4, Checkpoint B; scheduler
ownership since T2-6):**

The solver is on the request path, but a browser no longer drives it.
`Session` holds one `Engine` per page over a small single-device plant built
through the C3 loader, and now also one `Scheduler` per Engine
(`compressor_scheduler`, `pump_scheduler`), constructed inert and started only
by the route that renders the page displaying that machine — `/compressor`
starts `compressor_scheduler`, `/pump` starts `pump_scheduler`, never both for
one Session. Once started, that worker steps its Engine on its own cadence
(`app/engine/scheduler.py`) regardless of whether anything is polling it: it
advances the clock, integrates every device by the elapsed simulated time,
solves the plant's network, and publishes a `Snapshot`. `/api/state` and
`/api/pump/state` only read the latest published `Snapshot`; the frontend
polls them and no longer steps anything itself. `/api/step` and
`/api/pump/step` remain as manual, test-facing controls and step exactly as
before while the matching scheduler is stopped, but return HTTP 409 while it
is running rather than racing a manual step against the background worker. A
Session's schedulers run until `Session.end()` stops and joins both — called
directly, by `SessionRegistry.end()`, or by capacity-bounded LRU eviction
(`config.MAX_SESSIONS`) when a new session is created with the registry full.
There is deliberately no idle-age expiry yet; that is T18-5's scope, not
T2-6's. `SessionRegistry` still gives each browser its own instance.

**The legacy per-device operating point is gone.** `step()`,
`_calculate_operating_point()` and the `upstream_boundary_pressure` /
`downstream_boundary_pressure` attributes were removed from both
`GasCompressor` and `CentrifugalPump` at T4-4. A device publishes a curve and
nothing else: **flow, suction pressure and discharge pressure are solver
outputs**, held on the branch and its nodes, and they do not exist as device
attributes. `get_state()` returns slow state only; the snapshot's `nodes` and
`streams` sections carry the solved numbers.

**What this bought and what it cost.** The single-device pages lost the suction
and discharge line resistances and the `max_flow` clamp, which lived only in
the retired standalone solve. Until T7-1 gives the control valve a branch of
its own, a page's machine runs against two fixed battery limits with nothing
between them: flow reads well above `max_flow`, the discharge valve strokes
but changes nothing hydraulically, and the process spread sits at the boundary
difference. That is a known interim state, not a bug to fix in passing.

**Inventory is coupled to the hydraulics (since T5-2).** `Engine.from_plant`
wires one `NetworkSolver` per entry in `Plant.topologies`, so a multi-domain
plant runs. What joins two domains is a coupling device's inventory, never a
shared flow variable: a vessel's `head_at_full * level` is *added to* the
`configured_pressure` of the boundary node an OUTLET port attaches to, and
its flows are read back as the signed exchange at each attachment node. A
step is clock → integrate → write boundaries → solve every domain → read
flows back → snapshot. That is explicit Euler with one step of lag on the
vessel's flows; nothing iterates between the integrator and the solver.

`app/engine/coupling.py` owns that join and is the only place it lives. A
device still never reads a node or a branch, and `Node.set_boundary_pressure`
(C2, added at T5-2) refuses on an internal node, so the solver's writer and
the coupling's writer partition the graph between them. Gas-phase
accumulation (T5-3) puts a gas vessel's pressure on the same footing: it
replaces the boundary at every SCFM attachment node.

**The node is the unit of account, not the port (T5-6, ADR 0002 Amendment
2).** A port has no hydraulic flow of its own — the exchange belongs to the
node — so each node is counted **exactly once** however many nozzles a
vessel declares on it, for reading a flow and for writing a boundary
pressure alike. Distinct nodes are independent and **sum**: two vapor
withdrawals on two nodes are two withdrawals. A node where the device
declares both an inlet and an outlet keeps its gross components — signed
`arrivals` into the inlet aggregate, signed `departures` into the outlet one
— so a vessel fed and drained through one node keeps its throughput at a net
of zero. A node declaring one direction keeps its net. Both are signed sums
over branch orientation; nothing is clamped, so a reversed feed arrives
negatively. If any node feeding an aggregate sits in a domain that did not
converge, that **whole** aggregate holds its previous value.

**Phase classifies; machines confirm; domain names mean nothing.** A port's
declared `phase` picks the unit (`liquid` GPM, `vapor` SCFM) and the machines
at its node are checked against it, so `DOMAIN_UNITS` is retired and a domain
may be called `vent`, `flare` or anything else. **A new device model on a
branch must be added to `FLOW_UNITS`**, as a unit or as `UNIT_NEUTRAL` — a
resistance confirms nothing, but an *unrecognised* device still refuses at
Engine construction, and so does a declared phase the machines contradict. A
legacy untyped port is still read off the machines beside it, and is refused
where they settle nothing. A typed attachment couples even with no branch at
the node. One node has at most one inventory device: two vessels on a node
refuse.

**Still not on the request path:** `EquipmentRegistry`, `SeededRNG` (T2-2).

**Do not write documentation, comments, or code that implies controllers,
alarms, envelopes, or scoring already exist.**

## Development rules

- **One git worktree per task.** Never let parallel agents share one checkout —
  this repo has already lost a commit to a branch race, which is why the rule
  exists.
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

Small commit, clear purpose, short message.

- One commit is one clear change, or one coherent part of a task.
- Do not bundle unrelated cleanup, formatting, refactoring, documentation and
  feature work together unless they genuinely cannot be separated. Split a task
  with independent stages into independent commits.
- Prefer several small understandable commits to one "everything changed"
  commit, and leave the branch in a sensible state at each one where practical.
- Subject lines are short and say what changed, not how it was implemented.
  Lead with the build plan task ID when the work has one.
- Write a body only when the reason, the tradeoff, a migration concern or
  important test information is not already obvious from the subject and the
  diff. Routine changes do not get essays, and no commit needs a list of the
  files it touched.

Good: `T4-1: Add branch characteristic interface` ·
`T3-3: Validate topology references` · `Typing: Annotate Equipment contract` ·
`Docs: Add agent model guidance`

Bad: `update files` · `fixes` · `misc changes` · `work in progress`

## File ownership and high-conflict areas

| Path | Rule |
|---|---|
| `app/equipment/base.py` | **Spine** — one branch at a time, no satellite edits |
| `app/engine/` | **Spine** for its existing modules — one branch at a time. A new isolated module here can be satellite work; see the rule below. |
| `app/plant/topology.py` | **Spine** — one branch at a time |
| `app/main.py` | **Highest-conflict file.** Exactly one branch at a time until the C5 single action endpoint lands. Release it immediately after merging. |
| `app/config.py` | **Append-only** — add a clearly-headed section, never reorder |
| `config/schema/plant.schema.json` | Shared — each top-level key has one owner |
| `tests/fixtures/golden/*.json` | Regenerate only with explicit justification |
| `static/compressor.js`, `static/pump.js` | **Frozen** — replaced wholesale at M16. Do not invest in them. |

### The `app/engine/` rule

Existing spine modules and interfaces require the spine lock. A **new isolated
module** under `app/engine/` may be developed as a satellite when the build-plan
task explicitly owns that new file and does not modify an existing spine
interface or module. That is how `rng.py` (T2-2) and `network.py` (T4-2)
landed, and it is what the build plan intends for `scheduler.py` (T2-5) and
`persistence.py` (T12-1).

The existing spine modules there are `clock.py`, `engine.py`, `snapshot.py` and
`sessions.py`. `coupling.py` (T5-2) is a new module but landed inside a spine
task, because the Engine change and the coupling are one design. **If a satellite task discovers that it must modify one of them,
it stops and escalates or acquires the spine lock; it does not expand scope
silently.** T4-3 (`snapshot.py`) and T4-4 (`engine.py`) are spine tasks for
exactly this reason.

## How to start a task

1. `git fetch origin`
2. Read [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) and the build plan task.
3. Confirm every dependency is **Complete** (merged to `main`), not merely written.
4. Create a worktree:
   `git worktree add ../plant-simulator-<task> -b <type>/<name> origin/main`
5. Implement against the **frozen contract**, not against another branch's
   in-progress code.
6. Run the full suite and the type check: `python -m pytest -q && python -m mypy`
7. `git fetch origin && git rebase origin/main`
8. Run both again.
9. Open a PR titled `T{TASK-ID}: Brief description`.
10. Update the build plan: **Ready for Review** at PR time, **Complete** with the
    merge SHA only after it is merged to `main`.

## Commands

The project runs in a conda environment named `plant-simulator`. Activate it
first, then use portable commands:

```bash
conda activate plant-simulator

# Full suite
python -m pytest -q

# Static type check (configured over app/ in pyproject.toml)
python -m mypy

# Single file / single test
python -m pytest tests/test_pump.py -q
python -m pytest -k "test_pump_half_speed_operating_point" -q

# Run the app
flask --app app.main run     # http://127.0.0.1:5000/compressor and /pump
```

Runtime dependencies are in `requirements.txt`; test/dev dependencies are in
`requirements-dev.txt`. Do not write machine-specific interpreter paths into
documentation or scripts.

`conftest.py` only customizes pytest's status glyphs (✓ / ✗ / ○). It defines no
fixtures.

## Code style

Match the surrounding code:

- 4-space indent, no docstrings on equipment methods.
- Multi-line call formatting with trailing commas.
- Blank lines between logical blocks.
- Comments are rare — naming carries the explanation. Module-level docstrings
  explaining *why* a module exists are the exception and are welcome on spine
  files.
- Tests are flat `def test_*` functions, no classes, `pytest.approx` for every
  float comparison.

### Typing

**Type hints are required in new and modified production code under `app/`.**
This replaces the project's earlier "no type hints" rule: the interfaces the
solver milestones build against are worth stating explicitly, and they were
brought under a checker before M4 grew the architectural surface further.

- Public functions, methods, constructors and return values are typed. `-> None`
  counts.
- Type the attributes that carry the interface — `Equipment.tag`,
  `Equipment.ports`, a collection that holds devices. Not every attribute.
- Prefer Python 3.12 built-ins and unions: `list[str]`, `dict[str, float]`,
  `str | None`. Never `typing.List` or `Optional`.
- Do not annotate obvious locals to raise coverage. Annotate one only where
  inference genuinely needs help, e.g. `errors: list[str] = []`.
- `Any` needs a reason stated beside it. Decoded JSON of a shape nothing knows
  yet is a reason; silencing the checker is not.
- The JSON-safe row every `get_state()` returns is `StateRow` in
  `app/statetypes.py`. Use it rather than a hand-rolled dict type — a dict
  return type is invariant, so a device narrowing its row to
  `dict[str, float]` would not be a valid override.
- Tests may stay lightly typed; annotate one only where it makes the test
  clearer. `mypy` is not configured over `tests/`.
- New code passes `python -m mypy` before review. The configuration lives in
  `pyproject.toml` — do not loosen it to land a change; propose a change to it
  as its own task.

Existing production code is typed; new modules join the checked scope by
default. `app/config.py` carries no annotations because its constants infer
exactly.

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
