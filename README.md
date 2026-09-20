# Plant Simulator

A deterministic, modular plant-equipment simulator built with Flask, growing
into a connected **operator-training** simulation — a small process plant an
operator can run, upset, misdiagnose, trip, and be scored on.

It is built to be *realistic enough to teach process behaviour and cause and
effect*, not to be a process-design tool. Lumped-parameter models, algebraic
curves and explicit integration are the right level. Composition, flash
calculations and property packages are deliberately out of scope.

Equipment models are plain Python classes. There is no database and no build
step; the frontend is hand-written JS served statically.

## What a Version 1 training session should look like

An operator starts and stabilises the connected train, holds separator level
and pressure, watches a disturbance propagate from one machine to another,
reads the alarms and trends, responds through the normal controls, recognises
when protective action is required, and is scored afterwards on process
stability, safety limits and the actions they took.

Most of that is still ahead. What exists today is the physics underneath it.

## Current equipment

| Device | Tag | Model |
|---|---|---|
| Gas Compressor | `K-101` | Load ramp, head curve, discharge temperature from spread |
| Centrifugal Pump | `P-101` | Speed ramp, head curve |
| Control Valve | `FV-101` | Linear / equal-percentage characteristic, rate-limited stroke, fail-open or fail-closed |
| Vessel | `V-101` | Integrated liquid level and gas-phase pressure; a coupling device, on no branch |

All four implement the shared **Equipment contract (C1)**, so the engine can
drive equipment it has never seen before.

## What works today

- **Equipment contract (C1)** — `integrate(dt)` for slow state, pure
  `characteristic(flow)` for the device's curve, `reset()`, JSON-safe
  `get_state()`, and `Port` objects that carry wiring and nothing else.
- **Plant topology (C2)** — `Node`, `Branch`, `Stream` and a `Topology`
  container, with solver-owned values write-protected.
- **Plant configuration schema (C3)** — JSON Schema plus a validator, and a
  loader that reads `.json`, `.yaml` and `.yml`, wires multi-port devices
  through `ports` and `paths`, and rejects unsolvable graphs at load time
  naming the config path.
- **Pressure-flow network solver** — a Newton-Raphson solve over the plant
  graph. Devices publish curves; the solver finds where the plant lands on
  them. Non-convergence is reported, never guessed at, and a failed solve
  leaves every pressure and flow as it found them.
- **Multi-domain solving** — `Engine.from_plant()` wires one solver per flow
  domain, so a plant spanning a liquid and a gas side runs. No topology ever
  mixes GPM with SCFM.
- **Inventory coupling** — what joins two domains is a vessel's inventory,
  never a shared flow variable. Liquid level adds head to the boundary its
  outlet attaches to; gas-phase pressure replaces the boundary at every gas
  attachment, so the machine feeding a drum sees its back-pressure. Flows are
  read back as the net signed flow at each attachment.
- **Background scheduler** — each `Session` owns a `Scheduler` per `Engine`,
  started by the page render and stopped by `Session.end()` or by
  capacity-bounded LRU eviction. It advances the plant on its own cadence
  whether or not a browser is polling; the frontend is a pure consumer of
  published snapshots and steps nothing.
- **State snapshot (C4)** — one frozen, JSON-safe read contract for every
  downstream consumer.
- **Session registry** — each browser gets its own plant instance, so
  concurrent users never see each other's state.
- **Equipment registry** — ISA-style tags resolve to devices.
- **Simulation clock** — simulated time with speed multiplier and pause. No
  wall-clock source anywhere in a model.
- **Seeded RNG** — `SeededRNG` is the only permitted source of randomness, and
  a test fails if any other module imports `random`. Nothing draws random
  numbers yet.
- **Golden-value regression harness** — pins current numerical behaviour and
  fails loudly on drift.
- **Flask API and browser pages** for the compressor and the pump.
- **753 passing tests**, and a clean `mypy` over `app/` (24 source files).

### Not built yet

Controllers and PID loops, operating envelopes, alarms, trips and interlocks,
malfunctions, scenarios, scoring, the historian and the operator console are
all specified in the build plan and **none of them are implemented**. The
snapshot's `controllers`, `envelope` and `alarms` sections are present and
empty by design, so those layers can be built against a frozen shape.

The integrated `olefins_lite.yaml` train does not exist yet either — the
reference plants on `main` are the single-domain fixtures and the valve train
under `config/plants/`. The single-device browser pages still run one machine
between two fixed battery limits with no valve between them, which is a known
interim state, not a bug.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how current and target
architecture differ, and [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) for
what is true right now.

## Project structure

```text
app/
  main.py            Flask routes (per-equipment, session-scoped)
  config.py          Timing constants and equipment tag prefixes
  statetypes.py      StateRow — the JSON-safe row every get_state() returns
  equipment/         base.py (C1) · compressor.py · pump.py · valve.py
                     vessel.py · registry.py
  engine/            clock.py · engine.py · snapshot.py (C4) · sessions.py
                     network.py (solver) · coupling.py (inventory) ·
                     scheduler.py · rng.py
  plant/             topology.py (C2) · loader.py · validate.py (C3 validator)
config/schema/       plant.schema.json (C3)
config/plants/       Reference plant fixtures
templates/, static/  Per-equipment pages (replaced wholesale at M16)
tests/               pytest suite + golden trace fixtures
docs/                Architecture, project state, build plan, ADRs, units
```

## Run the application

The project uses a conda environment named `plant-simulator`:

```bash
conda activate plant-simulator
pip install -r requirements.txt

flask --app app.main run
```

Then open <http://127.0.0.1:5000/compressor> or <http://127.0.0.1:5000/pump>.

## Run tests

```bash
conda activate plant-simulator
pip install -r requirements-dev.txt

python -m pytest -q
python -m mypy      # static type check over app/
```

## Documentation

| Document | What it covers |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Operating context for coding agents: invariants, contracts, working rules |
| [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) | What is true right now — current `main`, test count, what to work on next |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current runtime vs. target architecture, and state ownership |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Branching, worktrees, testing and merge procedure |
| [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) | Full master build plan: 101 tasks, 20 milestones, contracts C1–C8, schedule |
| [docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md](docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md) | Why domains are separate and what couples them |
| [docs/ADR_0002_TYPED_PORTS.md](docs/ADR_0002_TYPED_PORTS.md) | Typed ports and the vessel connection model |
| [docs/UNITS_CONVENTION.md](docs/UNITS_CONVENTION.md) | Frozen dimensional units for every numeric value |

## Goal

Build a plant simulator realistic enough to teach process behaviour, expandable
one piece of equipment at a time, without ever letting the game layer reach into
the physics. Version 1 ships a seven-device train, because a complete small
plant proves every mechanism a large one would — more equipment is more
*instances*, not more *mechanisms*.
