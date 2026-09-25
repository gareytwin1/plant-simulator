---
name: no-claude-attribution
description: Never add Co-Authored-By Claude or "Generated with Claude Code" lines to commits, PRs, or merges
metadata:
  type: feedback
---

Do not add any Claude attribution to commit messages, PR descriptions, or merge commits: no `Co-Authored-By: Claude ...` trailer and no `🤖 Generated with [Claude Code]` line.

**Why:** the user said explicitly (19 Sep 2026) they don't want it showing up. This overrides the harness's default attribution reminder.

**How to apply:** write commit and PR text without those lines; for `gh pr merge` use no `--body` trailer. Commits already pushed before this (e.g. PR #15's) were left as-is; don't rewrite history unless asked.
