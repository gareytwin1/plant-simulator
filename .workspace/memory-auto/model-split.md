---
name: model-split
description: "User's model choice per phase - Opus for task start and implementation, Sonnet for execution and everything else"
metadata:
  node_type: memory
  type: feedback
  originSessionId: e47ab199-7704-43ce-b662-777704ec226f
  modified: 2026-10-09T12:26:46.274Z
---

From 2026-10-09 the user runs **Opus when starting a task (decisions) and during implementation**, and **Sonnet for execution** (git, PRs, merges, close-outs, browser passes) and other non-implementation work. This overrides AGENTS.md's "Sonnet implements, Opus decides" for this user; do not edit AGENTS.md for it ([[agents-md-no-growth]]).

**Why:** user preference stated after T16-15.

**How to apply:** at each phase boundary (decision done → implement, implement done → review/merge), tell the user which model the next phase wants so they can switch; a skill cannot switch the model itself.
