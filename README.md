# Plant Simulator

A deterministic, modular plant-equipment simulator built with Flask, growing
into a connected **operator-training** simulation — a small process plant an
operator can run, upset, misdiagnose, trip, and be scored on.

Equipment models are plain Python classes that step a lumped-parameter process
model forward in discrete time. There is no database and no build step; the
frontend is hand-written JS served statically.

## Current equipment

| Device | Tag | Model |
|---|---|---|
| Gas Compressor | `K-101` | Load ramp, head curve, discharge valve, temperature from spread |
| Centrifugal Pump | `P-101` | Speed ramp, head curve, suction/discharge hydraulics |

Both implement the shared **Equipment contract (C1)**, so the engine can drive
equipment it has never seen before.

## What works today

- **Equipment contract (C1)** — `integrate(dt)` for slow state, pure
  `characteristic(flow)` for the device's curve, `reset()`, JSON-safe
  `get_state()`, and typed `Port` objects for plant wiring.
- **Equipment registry** — ISA-style tags (`K-101`, `P-101`) resolve to devices.
- **Simulation clock** — simulated time with speed multiplier and pause. No
  wall-clock source anywhere in a model.
- **Simulation engine** — integrates every registered device by the elapsed
  simulated time and publishes an immutable snapshot.
- **State snapshot (C4)** — one frozen, JSON-safe read contract for every
  downstream consumer.
- **Session registry** — each browser gets its own plant instance, so concurrent
  users never see each other's state.
- **Plant topology (C2)** — `Node`, `Branch`, `Stream` and a `Topology`
  container, with solver-owned values write-protected.
- **Plant configuration schema (C3)** — JSON Schema plus a validator.
- **Golden-value regression harness** — pins current numerical behaviour for
  both devices and fails loudly on drift.
- **Flask API and browser pages** for the compressor and the pump.
- **202 passing tests.**

### Not built yet

The plant-wide **pressure-flow network solver** does not exist. Devices still
solve their own operating point through a legacy `step()` path, and the Flask
routes still call it. Controllers, envelopes, alarms, trips, scenarios, scoring
and the operator console are specified in the build plan but not implemented.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how current and target
architecture differ.

## Project structure

```text
app/
  main.py            Flask routes (per-equipment, session-scoped)
  config.py          Timing constants and equipment tag prefixes
  equipment/         base.py (C1) · compressor.py · pump.py · registry.py
  engine/            clock.py · engine.py · snapshot.py (C4) · sessions.py
  plant/             topology.py (C2) · validate.py (C3 validator)
config/schema/       plant.schema.json (C3)
templates/, static/  Per-equipment pages (replaced wholesale at M16)
tests/               pytest suite + golden trace fixtures
docs/                Architecture, project state, build plan, unit convention
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
| [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) | Full master build plan: 93 tasks, 20 milestones, contracts C1–C8, schedule |
| [docs/UNITS_CONVENTION.md](docs/UNITS_CONVENTION.md) | Frozen dimensional units for every numeric value |

## Goal

Build a plant simulator realistic enough to teach process behaviour, expandable
one piece of equipment at a time, without ever letting the game layer reach into
the physics.
