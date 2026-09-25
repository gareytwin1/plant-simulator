# Development Guide

Day-to-day workflow: how to pick up a task, work on it, and merge it back.

This project follows a **spine / satellite** branching model: a small set of core
files (the spine) are edited sequentially by one branch at a time, while
independent modules (satellites) can be developed in parallel once they only
depend on frozen interface contracts.

## Where things live

| Document | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Architectural invariants and agent operating rules — **read first** |
| [.claude/rules/](.claude/rules/) | Path-scoped rules (engine, plant config, Python style, testing, docs ownership) — they apply when Claude works with files matching their configured `paths` |
| [.workspace/memory/project_state.md](.workspace/memory/project_state.md) | Current `main`, active branches, what to work on next |
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
section in [AGENTS.md](AGENTS.md#status-vocabulary) — authoritative there, not
restated here. If you find a task marked Complete whose files are not on
`main`, correct the status — do not build on it.

## Task workflow

### Starting a task

The `start-task` skill runs these steps; keep the two in step when either
changes.

Tasks run in parallel across agents that share one clone, so more than one
`git checkout` can be in flight at once. If each task checks out its branch in
the same working directory, one agent's checkout can land between another's
`git checkout -b` and its first commit, and the commit ends up on the wrong
branch. **This has already happened in this repository** (T1-5's first commit
landed on another session's branch). A worktree gives each task its own working
directory against the same repo, which removes the race entirely.

1. `git fetch origin`
2. Read [.workspace/memory/project_state.md](.workspace/memory/project_state.md) and the task on the build
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

Sonnet implements and executes; Opus decides. The full table — what each one is
for, and the rule that a Sonnet session escalates rather than inventing a
design — is the **Agent model guidance** section in
[AGENTS.md](AGENTS.md#agent-model-guidance).

### During development

- Build against the **frozen interface contract**, not against another
  satellite's in-progress code.
- Fix unrelated lint, test failures and flakiness you come across, each in its
  own commit. A fix that would touch a spine file or a contract is its own
  task instead.
- New and modified production code under `app/` carries type hints — full
  rules in [.claude/rules/python.md](.claude/rules/python.md).
- If you hit a blocker (a contract seems wrong, a dependency isn't actually
  ready), note it on the task rather than working around it silently.

### Continuous review (roborev)

A git post-commit hook sends every commit to [roborev](https://roborev.io) for
an automated review (agent: `claude-code`, pinned in `.roborev.toml`).
Findings surface at the next Claude Code session start and via:

```bash
roborev show HEAD    # review for the most recent commit
roborev tui           # interactive queue across all commits
```

Address findings before opening a PR — `/roborev-refine` reviews every commit
on the branch, applies fixes, and re-reviews until clean. This runs alongside
`pytest`/`mypy`, not instead of them; a review-clean branch can still fail the
suite.

### Before review

**Ready for Review** is defined in
[AGENTS.md's Status vocabulary](AGENTS.md#status-vocabulary), and it means all
of it, not some of it. The steps below are how you get there; the
`ready-for-review` skill runs the same checklist.

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
- Set the task status to **Ready for Review** in the live artifact only. Do
  not regenerate `docs/BUILD_PLAN_STATUS.json` for it, on the branch or on
  `main`: every task shares that file, so a branch commit to it conflicts
  with the next one, and a `main` commit puts every open PR behind. The next
  merge's regeneration picks the status up.

### Merging

The `merge-task` skill runs these steps; keep the two in step when either
changes.

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
  count in the note — in the live artifact first, then regenerate
  `docs/BUILD_PLAN_STATUS.json` on `main`, in one commit with the
  project_state.md refresh below; see
  [below](#regenerating-docsbuild_plan_statusjson).
- Delete the merged branch (local and remote) and remove the worktree:

  ```bash
  git worktree remove ../plant-simulator-<task>
  git branch -d <type>/<name>
  git push origin --delete <type>/<name>
  ```

- Refresh [.workspace/memory/project_state.md](.workspace/memory/project_state.md) if the merge changed
  milestone progress, unblocked tasks, or the recommended next task.

### Regenerating docs/BUILD_PLAN_STATUS.json

`docs/BUILD_PLAN_STATUS.json` is a derived file with two independent sources —
never hand-patch it, which is exactly the drift the build plan exists to
prevent. It is regenerated only on `main` when closing out a merge, never on
a task branch and never just to record Ready for Review; it is a durable
snapshot, and lagging the live artifact between merges is expected. Update the **live artifact first**
(`https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9`), then rebuild the JSON
with `scripts/build_plan_status.py`:

```bash
python scripts/build_plan_status.py \
  --status-dir <taskStatus export dir> \
  --main-sha <current main SHA> \
  --tests-passing <post-merge test count> \
  --refreshed "<today, e.g. 24 September 2026>"
```

- Task definitions (name, category, branch, dependencies, files) come from
  `docs/BUILD_PLAN.html`'s `TASKS` array (`--html`, default
  `docs/BUILD_PLAN.html`); the script parses it with `node`, since it's
  JavaScript, not JSON.
- Per-task status and notes come from `--status-dir`, a local export of the
  artifact's `taskStatus` collection: `ArtifactData` `query` or `list` on
  `taskStatus` with `out_dir` set saves each document as
  `<out_dir>/taskStatus/<task-id>.json` — pass that `taskStatus` folder as
  `--status-dir`. A task with no exported document defaults to Not Started.
- A task's long-form note is carried forward from `--previous` (default: the
  `--out` file, i.e. the file being replaced) whenever the artifact's own note
  is empty — most `taskStatus` documents only set `status`.
- `--out` defaults to `docs/BUILD_PLAN_STATUS.json`, overwriting it in place.
  Pass `--check` instead to compare against `--out` without writing (exits 1
  on any drift) — useful to confirm the script reproduces the current file
  before trusting a change to it.
- Confirm the diff shows only the intended fields before committing it.

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
  work together unless they genuinely cannot be separated. Split a task with
  independent stages into independent commits.
- Prefer several small understandable commits to one "everything changed"
  commit, and leave the branch in a sensible state at each one where
  practical. roborev reviews each commit separately (see below), so this also
  keeps its feedback focused.
- Subject lines say what changed, not how — lead with the task ID when there
  is one.
- Write a body only when the reason, tradeoff, migration concern or important
  test information isn't already obvious from the subject and the diff.
  Routine changes do not get essays, and no commit needs a list of the files
  it touched.

```text
T8-1: Add PID block with anti-windup

Anti-windup, output clamping and derivative on measurement. Testable
against a fake first-order process, so it carries no plant dependency.
```

Good: `T4-1: Add branch characteristic interface` ·
`T3-3: Validate topology references` · `Typing: Annotate Equipment contract` ·
`Docs: Add agent model guidance`

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
exist as specifications only — see the contract table in [AGENTS.md](AGENTS.md).

Contracts are load-bearing: once a satellite branch depends on one, changing it
means updating every dependent branch. Propose contract changes as their own
task, never as a side effect of unrelated work.

## Determinism and observability

**Time is owned, not observed** and **golden regressions protect existing
physics** are both in [AGENTS.md](AGENTS.md#critical-architectural-invariants)
— the full rules, including the golden-trace tolerance rationale, live there.
One workflow note not covered there: log lines should carry sim time, not
wall-clock time, so behaviour can be correlated across a run.
