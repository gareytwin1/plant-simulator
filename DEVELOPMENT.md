# Development Guide

Day-to-day workflow: how to pick up a task, work on it, and merge it back.

This project follows a **spine / satellite** branching model: a small set of core
files (the spine) are edited sequentially by one branch at a time, while
independent modules (satellites) can be developed in parallel once they only
depend on frozen interface contracts.

## Where things live

| Document | Purpose |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Architectural invariants and agent operating rules — **read first** |
| [.claude/rules/](.claude/rules/) | Path-scoped rules (engine, plant config, Python style, docs ownership) — load automatically when you open a matching file |
| [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) | Current `main`, active branches, what to work on next |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current runtime vs. target architecture; state ownership |
| [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) | Full master plan: tasks, milestones, contracts C1–C8, dependencies, schedule |
| [docs/BUILD_PLAN_STATUS.json](docs/BUILD_PLAN_STATUS.json) | Durable snapshot of per-task status |
| [Live build plan artifact](https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9) | Interactive status tracking, shared across agents |

The **live artifact is the interactive status authority**; the repository copies
are the durable, recoverable representation. If the artifact is unavailable,
`docs/BUILD_PLAN.html` plus `docs/BUILD_PLAN_STATUS.json` are enough to
reconstruct the plan and where it stands. Refresh both when status changes
materially.

## Environment

The project runs in a conda environment named `plant-simulator`. Activate it
first, then use portable commands — **never write machine-specific interpreter
paths into documentation, scripts or CI**:

```bash
conda activate plant-simulator
pip install -r requirements.txt       # runtime
pip install -r requirements-dev.txt   # tests and mypy

# Single file / single test
python -m pytest tests/test_pump.py -q
python -m pytest -k "test_pump_half_speed_operating_point" -q

# Run the app
flask --app app.main run     # http://127.0.0.1:5000/compressor and /pump
```

`conftest.py` only customizes pytest's status glyphs (✓ / ✗ / ○); it defines no
fixtures.

## Status vocabulary

The five values and what each means exactly are the **Status vocabulary**
section in [CLAUDE.md](CLAUDE.md#status-vocabulary) — authoritative there, not
restated here. If you find a task marked Complete whose files are not on
`main`, correct the status — do not build on it.

## Task workflow

### Starting a task

Tasks run in parallel across agents that share one clone, so more than one
`git checkout` can be in flight at once. If each task checks out its branch in
the same working directory, one agent's checkout can land between another's
`git checkout -b` and its first commit, and the commit ends up on the wrong
branch. **This has already happened in this repository** (T1-5's first commit
landed on another session's branch). A worktree gives each task its own working
directory against the same repo, which removes the race entirely.

1. `git fetch origin`
2. Read [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) and the task on the build
   plan.
3. Confirm every dependency is **Complete** — merged to `main`, not merely
   written.
4. Create the worktree — **do all work inside it, not in the main checkout**:

   ```bash
   git worktree add ../plant-simulator-<task> -b <type>/<name> origin/main
   ```

5. If the task is Core/spine (edits an existing module among `app/equipment/base.py`,
   `app/engine/` or `app/plant/topology.py` — a new isolated module under
   `app/engine/` does not count; see
   [.claude/rules/engine.md](.claude/rules/engine.md)), check the build plan
   for any other in-progress branch touching the same file before starting.
   Spine files take one branch at a time.

### Picking a model

Haiku executes, Sonnet implements, Opus decides. The full table — what each
one is for, and the rule that a smaller model escalates rather than inventing
a design — is the **Agent model guidance** section in [CLAUDE.md](CLAUDE.md).

### During development

- Build against the **frozen interface contract**, not against another
  satellite's in-progress code.
- Keep commits scoped to the task; unrelated cleanup goes in its own task.
- New and modified production code under `app/` carries type hints — see the
  typing rules in [CLAUDE.md](CLAUDE.md).
- If you hit a blocker (a contract seems wrong, a dependency isn't actually
  ready), note it on the task rather than working around it silently.

### Before review

**Ready for Review** means all four of these, not some of them: rebased on
current `main`, full pytest suite green, static type check green over the
configured production scope, PR open.

- Run the **full** suite, not just your new tests, and the type checker:

  ```bash
  python -m pytest -q
  python -m mypy
  ```

  `mypy` reads its configuration from `pyproject.toml` and checks `app/`.
  Tests are outside that scope by design.

- Confirm the task's own required tests (listed on the build plan) pass.
- Rebase onto current `main` and run both again:

  ```bash
  git fetch origin && git rebase origin/main
  python -m pytest -q && python -m mypy
  ```

- Open a PR titled `T{TASK-ID}: Brief description`.
- Set the task status to **Ready for Review**.

### Merging

- Merge convention is a **merge commit** titled
  `Merge T{TASK-ID}: Brief description`:

  ```bash
  gh pr merge <N> --merge --subject "Merge T1-4: Refactor the pump onto the equipment contract"
  ```

- After the merge, verify on `main`:

  ```bash
  git switch main && git pull --ff-only origin main && python -m pytest -q
  ```

- Set the task status to **Complete**, with the merge SHA and the post-merge test
  count in the note.
- Delete the merged branch (local and remote) and remove the worktree:

  ```bash
  git worktree remove ../plant-simulator-<task>
  git branch -d <type>/<name>
  git push origin --delete <type>/<name>
  ```

- Refresh [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) if the merge changed
  milestone progress, unblocked tasks, or the recommended next task.

## File ownership

Check this before starting step 5 above.

| Path | Rule |
|---|---|
| `app/equipment/base.py` | **Spine** — one branch at a time, no satellite edits |
| `app/engine/` | **Spine** for its existing modules — one branch at a time. A new isolated module here can be satellite work; see [.claude/rules/engine.md](.claude/rules/engine.md). |
| `app/plant/topology.py` | **Spine** — one branch at a time |
| `app/main.py` | **Highest-conflict file.** Exactly one branch at a time until the C5 single action endpoint lands. Release it immediately after merging. |
| `app/config.py` | **Append-only** — add a clearly-headed section, never reorder |
| `config/schema/plant.schema.json` | Shared — each top-level key has one owner |
| `config/plants/*.yaml` | Shared — each top-level key has one owner |
| `tests/fixtures/golden/*.json` | Regenerate only with explicit justification |
| `static/compressor.js`, `static/pump.js` | **Frozen** — replaced wholesale at M16. Do not invest in them. |

## Naming conventions

**Branches:**

```text
feature/pid-block
refactor/pump-onto-base
test/import-direction-guard
chore/ci-pipeline
docs/project-handoff-refresh
```

**Commits** — small, focused, and concisely described:

- One commit is one clear change, or one coherent part of a task. Do not
  bundle unrelated cleanup, formatting, refactoring, documentation and feature
  work together unless they genuinely cannot be separated.
- Prefer several small understandable commits to one "everything changed"
  commit.
- Subject lines say what changed, not how — lead with the task ID when there
  is one.
- Write a body only when the reason, tradeoff, migration concern or important
  test information isn't already obvious from the subject and the diff.

```text
T8-1: Add PID block with anti-windup

Anti-windup, output clamping and derivative on measurement. Testable
against a fake first-order process, so it carries no plant dependency.
```

Good: `T4-1: Add branch characteristic interface` ·
`T3-3: Validate topology references` · `Typing: Annotate Equipment contract`

Bad: `update files` · `fixes` · `misc changes` · `work in progress`

**Pull requests:** title `T{TASK-ID}: Brief description`; body summarises the
change and its test plan.

## Parallel development

Spine files are edited one branch at a time because they are the shared
foundation everything else builds on. Satellites rebase onto `main` after any
spine merge lands:

```bash
git fetch origin
git rebase origin/main
```

Never merge `main` backwards into the spine.

## Contracts

The eight interface contracts (C1–C8: Equipment, Topology, Plant config schema,
State snapshot, HTTP API, Event record, Alarm interface, Malfunction/Scenario)
are defined in full on the build plan. C1–C4 are implemented in code; C5–C8
exist as specifications only — see the contract table in [CLAUDE.md](CLAUDE.md).

Contracts are load-bearing: once a satellite branch depends on one, changing it
means updating every dependent branch. Propose contract changes as their own
task, never as a side effect of unrelated work.

## Determinism and observability

**Time is owned, not observed** and **golden regressions protect existing
physics** are both in [CLAUDE.md](CLAUDE.md#critical-architectural-invariants)
— the full rules, including the golden-trace tolerance rationale, live there.
One workflow note not covered there: log lines should carry sim time, not
wall-clock time, so behaviour can be correlated across a run.
