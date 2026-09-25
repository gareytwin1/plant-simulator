---
name: merge-task
description: Merge an approved PR, verify main, refresh project_state.md's current-state fields, and clean up the task's own worktree and branch. Invoke explicitly with a PR number (e.g. "/merge-task 52"); never auto-triggered, and always pauses before the merge itself.
disable-model-invocation: true
argument-hint: [pr-number]
---

Merge PR `$0` and close out its task. This skill has a hard checkpoint before
the merge and never touches another task's branches, worktrees, or docs. Steps
1-4 and 7 mirror
[DEVELOPMENT.md's "Merging" section](../../../DEVELOPMENT.md#merging); if the
two disagree, DEVELOPMENT.md is authoritative and this skill is stale.

## Steps

1. `gh pr view $0` — confirm it's open, checks are green, and it's actually
   been reviewed. Show the title, files changed, and check status.

2. **Stop and get explicit confirmation before merging.** Merging to `main`
   and deleting branches is hard to reverse and visible to every other agent
   and collaborator working in this repo — this is not a step to run through
   unattended. State the merge commit subject you're about to use and wait.

3. On confirmation, merge with this repo's convention (a merge commit, not a
   squash):

   ```bash
   gh pr merge $0 --merge --subject "Merge T{TASK-ID}: Brief description"
   ```

4. Verify on `main`:

   ```bash
   git switch main && git pull --ff-only origin main && python -m pytest -q
   ```

   If the suite doesn't pass on `main` post-merge, stop and report — do not
   proceed to cleanup on a broken `main`.

5. Set the task's status to **Complete** — **in the live build plan artifact
   first** (`https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9`), with the
   merge SHA and post-merge test count in the note. **Never hand-patch
   `docs/BUILD_PLAN_STATUS.json` directly** — it's a derived file with three
   separate sources (task definitions from `BUILD_PLAN.html`, status and notes
   from the artifact's `ArtifactData`), and hand-editing it is exactly the
   drift the build plan exists to prevent. Regenerate it from the artifact
   afterward per the project's build-plan-status-regeneration memory, and
   confirm the diff shows only the intended fields before committing it.

6. **Refresh `.workspace/memory/project_state.md` — current-state fields only.** Per
   [.claude/rules/docs.md](../../rules/docs.md), this skill never edits
   AGENTS.md, ARCHITECTURE.md, DEVELOPMENT.md, or the ADRs; a change to any of
   those is its own reviewed task, not a side effect of a merge.

   - Update "Last state refresh" to the new date and merge SHA.
   - Add one line to the recent-merges table; if it now has more than ~6
     rows, drop the oldest (it's already in `BUILD_PLAN_STATUS.json`).
   - Update milestone progress and the startable-tasks list — cross-check
     against `BUILD_PLAN_STATUS.json`'s actual dependency graph, don't just
     append.
   - Do not add a per-task handoff section. One line in the table is the
     whole entry — that pattern is exactly what grew this file to 1,086
     lines once before.

7. **Clean up — this task's own branch and worktree only, never a
   repo-wide sweep:**

   ```bash
   git worktree remove ../plant-simulator-<task>
   git branch -d <type>/<name>
   git push origin --delete <type>/<name>
   ```

   Use `-d`, never `-D`. If any of these three commands refuses (uncommitted
   changes in the worktree, branch not fully merged), **stop and report** —
   do not force past git's own safety check. Do not touch any other branch or
   worktree, even ones that look obviously stale; that's a separate,
   deliberately-not-automated audit, not this skill's job.

8. Report: merge SHA, post-merge test count, what's now unblocked (cross-check
   the build plan's dependents of this task), and confirmation that cleanup
   completed or exactly what it refused to do and why.
