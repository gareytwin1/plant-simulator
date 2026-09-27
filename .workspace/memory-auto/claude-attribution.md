---
name: claude-attribution
description: Do add Co-Authored-By Claude lines to commits going forward; existing history stays as-is
metadata:
  node_type: memory
  type: feedback
  originSessionId: 7ae8dbf1-c284-4be7-aec2-3b3de96b57a8
  modified: 2026-09-25T12:05:18.241Z
---

Add Claude attribution to commit messages going forward: a `Co-Authored-By:
Claude <model> <noreply@anthropic.com>` trailer, matching whatever model name
the session's own attribution reminder gives that turn (it changes when the
user switches model with `/model`).

**Why:** the user reversed an earlier "no attribution" preference (25 Sep
2026): "for now on we need to acknowledge your contribution." That earlier
preference (from 19 Sep 2026) is superseded, not layered on top of.

**How to apply:** include the trailer on every new commit. Do **not**
retroactively add it to commits already on `main` — the user explicitly said
to leave history alone, and rewriting merged/shared history is a separate,
much riskier action (force-push over a branch other sessions build from)
than just fixing it going forward. The commits made 19 Sep-25 Sep 2026 while
the old preference was active (e.g. PR #15's, and several `plant-simulator`
merge/status commits around PRs #65-70) stay unattributed.
