---
name: start-task
description: Pick up a build-plan task - checks dependencies and locks, confirms the right model, and creates its worktree. Invoke explicitly (e.g. "/start-task T5-4"); never auto-triggered.
disable-model-invocation: true
argument-hint: [task-id]
---

Set up to work on task `$1`. Do not begin implementing the task itself — this
skill ends at "environment ready," and hands back for the actual work.

## Steps

1. `git fetch origin`.

2. Read [docs/PROJECT_STATE.md](../../../docs/PROJECT_STATE.md) for the
   current milestone/startable-tasks picture, then find `$1` in
   `docs/BUILD_PLAN_STATUS.json` (search the task ID; do not read
   `docs/BUILD_PLAN.html` in full). Pull: its dependencies, its assigned
   model, its file list, and its acceptance criteria.

3. **Confirm every dependency is Complete** — merged to `main`, per
   `BUILD_PLAN_STATUS.json`, not merely "Ready for Review." If any dependency
   isn't Complete, stop and report which one; do not start anyway.

4. **Check the model assignment.** Cross-check the build plan's assigned model
   against [CLAUDE.md's Agent model guidance](../../../CLAUDE.md#agent-model-guidance)
   table. If this session isn't running as that model, say so plainly and let
   the user switch — a skill cannot switch its own model.

5. **Check for lock conflicts.** If `$1` touches a file in
   [DEVELOPMENT.md's file-ownership table](../../../DEVELOPMENT.md#file-ownership)
   marked Spine or Highest-conflict, check `BUILD_PLAN_STATUS.json` and
   PROJECT_STATE.md's "In flight" line for another branch already touching it.
   A new isolated module under `app/engine/` is satellite work even if the
   directory is listed as spine — see
   [.claude/rules/engine.md](../../rules/engine.md).

6. **Create the worktree** — never do this task's work in the primary
   checkout:

   ```bash
   git worktree add ../plant-simulator-<short-name> -b <type>/<name> origin/main
   ```

   Pick `<type>` from the branch-naming convention in
   [DEVELOPMENT.md](../../../DEVELOPMENT.md#naming-conventions)
   (`feature/`, `refactor/`, `test/`, `chore/`, `docs/`) and confirm the exact
   branch name with the user if the task doesn't make it obvious.

7. Report back: worktree path, branch name, confirmed model, current baseline
   (`python -m pytest -q` test count on the fresh worktree), and the task's
   acceptance criteria pulled from the build plan — so the session about to
   implement has everything without re-deriving it.
