---
name: ready-for-review
description: Run the pre-review checklist on the current task branch - full suite, mypy, rebase, golden-trace and scope checks, then open the PR. Invoke explicitly when a task's implementation is done; never auto-triggered.
disable-model-invocation: true
---

Run this from inside the task's own worktree, on its own branch — not the
primary checkout. If `git worktree list` doesn't show the current directory as
a worktree, stop and say so rather than proceeding.

**Ready for Review** means all of the following are true, not some of them —
see [CLAUDE.md's Status vocabulary](../../../CLAUDE.md#status-vocabulary). The
steps below mirror
[DEVELOPMENT.md's "Before review" section](../../../DEVELOPMENT.md#before-review);
if the two disagree, DEVELOPMENT.md is authoritative and this skill is stale
and needs updating to match — do not follow this skill over DEVELOPMENT.md.

## Steps

1. Run the **full** suite, not just the task's new tests, and the type
   checker:

   ```bash
   python -m pytest -q
   python -m mypy
   ```

   Both must be green. `mypy` checks `app/` only, per `pyproject.toml`.

2. Confirm the task's own required tests (from the build plan entry) are
   present and passing — not just that the suite as a whole is green.

3. **Check for golden-trace movement**: `git diff origin/main -- tests/fixtures/golden/`.
   If anything changed, **stop**. Per
   [CLAUDE.md's golden-regression policy](../../../CLAUDE.md#critical-architectural-invariants),
   a moved trace means behaviour changed and needs explicit justification from
   the user before this skill continues — never regenerate a trace to make a
   test pass and keep going silently.

4. **Check the diff for scope creep**: `git diff origin/main --stat`. Compare
   the file list against the task's expected files on the build plan. Flag
   anything unrelated rather than including it — "do not modify unrelated
   files" is a standing rule, not a suggestion.

5. Rebase onto current `main` and re-run both checks:

   ```bash
   git fetch origin && git rebase origin/main
   python -m pytest -q && python -m mypy
   ```

   If the rebase moves anything from steps 1-4, redo them.

6. **Pause and confirm with the user before opening the PR** — show the
   proposed title (`T{TASK-ID}: Brief description`) and a one-line summary.
   Opening a PR is visible to others; don't do it without a checkpoint.

7. On confirmation, open the PR. Then set the task's status to **Ready for
   Review** — **in the live build plan artifact first**
   (`https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9`), never by
   hand-editing `docs/BUILD_PLAN_STATUS.json` directly. That file is derived
   from the artifact; editing it by hand is exactly the drift the build plan
   exists to prevent. Regenerate it from the artifact afterward per the
   project's build-plan-status-regeneration memory, and confirm the diff shows
   only the intended fields.

8. Report: PR URL, final test count, mypy result, and confirmation that no
   golden trace moved.
