---
name: start-task
description: Pick up a build-plan task - checks dependencies and locks, confirms the right model, and creates its worktree. Invoke explicitly (e.g. "/start-task T5-4"); never auto-triggered.
disable-model-invocation: true
argument-hint: [task-id]
---

Set up to work on task `$0`. Do not begin implementing the task itself — this
skill ends at "environment ready," and hands back for the actual work. Steps
1-6 mirror
[DEVELOPMENT.md's "Starting a task" section](../../../DEVELOPMENT.md#starting-a-task);
if the two disagree, DEVELOPMENT.md is authoritative and this skill is stale.

## Steps

1. `git fetch origin`.

2. Read [docs/PROJECT_STATE.md](../../../docs/PROJECT_STATE.md) for the
   current milestone/startable-tasks picture, then find `$0` in
   `docs/BUILD_PLAN_STATUS.json` (search the task ID; do not read
   `docs/BUILD_PLAN.html` in full). Pull: its dependencies, its branch name,
   its file list, its status, and its acceptance criteria.

3. **Confirm every dependency is Complete** — merged to `main`, per
   `BUILD_PLAN_STATUS.json`, not merely "Ready for Review." If any dependency
   isn't Complete, stop and report which one; do not start anyway.

4. **Check the model assignment.** `BUILD_PLAN_STATUS.json` does not carry one
   — the assignment lives in the `AGENT` map in `docs/BUILD_PLAN.html` (grep
   the task ID), which defaults to Opus for a `core` task and Sonnet for
   everything else. PROJECT_STATE.md's startable-tasks table repeats it for
   the tasks listed there. Sanity-check the result against
   [AGENTS.md's Agent model guidance](../../../AGENTS.md#agent-model-guidance):
   Sonnet implements and executes, Opus decides. If this session isn't running
   as that model, say so plainly and let the user switch — a skill cannot
   switch its own model.

5. **Check for lock conflicts.** If `$0` touches a file in
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

   **Use the `branch` field from the task's own entry in
   `BUILD_PLAN_STATUS.json`** — nearly every task defines one, and inventing a
   different name strands the task's own record. Only if it has none, pick
   `<type>` from the branch-naming convention in
   [DEVELOPMENT.md](../../../DEVELOPMENT.md#naming-conventions)
   (`feature/`, `refactor/`, `test/`, `chore/`, `docs/`) and confirm the exact
   name with the user.

7. Report back: worktree path, branch name, confirmed model, current baseline
   (`python -m pytest -q` test count on the fresh worktree), and the task's
   acceptance criteria pulled from the build plan — so the session about to
   implement has everything without re-deriving it.
