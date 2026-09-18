# Development Guide

This project follows a **spine / satellite** branching model: a small set of
core files (the spine) are edited sequentially by one branch at a time, while
independent modules (satellites) can be developed in parallel once they only
depend on frozen interface contracts.

The master plan — milestones, tasks, contracts, and live status — lives in
the published build plan. Treat it as the source of truth for what's done and
what's next:

- Live artifact (status tracking): https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9
- Local copy: [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html)

This file is the day-to-day workflow companion — how to pick up a task, work
on it, and merge it back.

## Quick Start

1. Open the live artifact and find a task marked **Not Started** whose
   dependencies are already **Complete**.
2. If the task depends on an interface contract (C1–C8), read that contract
   in the build plan before writing any code against it — contracts are
   frozen once a satellite branch starts depending on them.
3. Create a branch named after the task (see Naming Conventions below).
4. When the task is done and tested, open a PR titled with the task ID and
   update the task's status on the live artifact.

## Task Workflow

### Starting a Task

1. `git checkout main && git pull`
2. `git checkout -b feature/task-name`
3. Re-read the task's fields on the live artifact: objective, required
   tests, and whether it's Independent, Dependent, or Core-integration.
4. If it's Core-integration (touches spine files), check the artifact for
   any other in-progress branch touching the same file before starting.

### During Development

- Build against the interface contract, not against another satellite's
  in-progress code.
- Keep commits scoped to the task; unrelated cleanup goes in its own commit.
- If you hit a blocker (a contract seems wrong, a dependency isn't actually
  ready), note it on the task in the live artifact rather than working around
  it silently.

### Before Merge

- Run the full test suite, not just the new tests:
  `/home/garey/miniconda3/envs/plant-simulator/bin/python -m pytest -q`
- Confirm the task's own required tests (listed on the artifact) pass.
- Update the task status to **Ready for Review**.
- Open a PR titled `T{TASK-ID}: Brief description`.

### After Merge

- Update the task status to **Complete** on the live artifact.
- If other tasks were blocked on this one, they're now unblocked — no need
  to notify anyone individually, the artifact reflects it.

## File Ownership

| File / Path | Rule |
|---|---|
| `app/equipment/base.py`, `app/engine/`, `app/plant/topology.py` | Spine — one branch at a time, no satellite edits |
| `app/main.py` | Sequential — one branch at a time |
| `app/config.py` | Append-only — add a section, don't restructure |
| `config/plants/olefins_lite.yaml` | Shared — each top-level key has one owner |
| `static/compressor.js` | Frozen — retired at M16, no new dependents |

## Naming Conventions

**Branches:**
```
feature/pid-controller
refactor/remove-duplicate-simulator
test/alarm-state-machine
chore/pin-requirements
docs/update-contracts
```

**Commits** — reference the task ID, explain why, not what:
```
T8-1: Add PID block with anti-windup

Implement a standalone PID controller with anti-windup, output clamping,
and derivative on measurement. Fully testable against a fake first-order
process, no plant dependency.

Tests: step response to setpoint, windup suppression, setpoint kick handling.
```

**Pull requests:** title `T{TASK-ID}: Brief description`, body links the
task on the live artifact.

## Parallel Development

Spine files are edited one branch at a time because they're the shared
foundation everything else builds on — concurrent edits there cause the
merge conflicts and silent breakage the spine/satellite split exists to
avoid. Satellites should rebase onto `main` after any spine merge lands:

```
git fetch origin
git rebase origin/main
```

## Running Tests

```
# Full suite
/home/garey/miniconda3/envs/plant-simulator/bin/python -m pytest -q

# Single file
/home/garey/miniconda3/envs/plant-simulator/bin/python -m pytest tests/test_pump.py -q

# Single test
/home/garey/miniconda3/envs/plant-simulator/bin/python -m pytest -k "test_valve_ramp" -q
```

## Contracts

The 8 interface contracts (C1–C8: Equipment, Topology, Plant config schema,
State snapshot, HTTP API, Event record, Alarm interface, Malfunction/Scenario)
are defined in full on the live artifact. They're load-bearing — once a
satellite branch depends on one, changing the contract means updating every
dependent branch. Propose contract changes as their own task, not as a side
effect of unrelated work.

## Logging and Observability

Never call `time.time()` inside a model — simulation time must come from the
injected `dt` / `SimulationClock`, so runs stay deterministic and replayable.
Log lines should carry sim time, not wall-clock time, so behavior can be
correlated across a run.

## See Also

- [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) — local companion guide
- Live artifact: https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9
- [README.md](README.md)
