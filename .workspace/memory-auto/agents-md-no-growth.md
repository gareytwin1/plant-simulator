---
name: agents-md-no-growth
description: "Never add content to AGENTS.md (formerly CLAUDE.md) going forward - it must not grow, even for small additions"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ac92d1bf-9edf-43e5-b89d-ed6f2ca79440
  modified: 2026-09-24T19:30:00.000Z
---

Do not add new content to AGENTS.md, and do not let its line count grow, even
for a seemingly small addition (a new invariant, a new cross-reference, a
one-line note about a task). AGENTS.md holds what used to be CLAUDE.md;
CLAUDE.md is now just `@AGENTS.md`.

**Why:** CLAUDE.md was deliberately cut from 473 to 263 lines across two
reviewed PRs (#51, #52) because Anthropic's docs
(code.claude.com/docs/en/memory) state files over 200 lines "consume more
context and may reduce adherence," and it's loaded into every session
unconditionally. The number is meant to hold, not creep back up one small
addition at a time - the pattern that once grew PROJECT_STATE.md to 1,086
lines. The one exception so far: on 2026-09-24 the user asked to adopt the
coding-agent-toolkit layout, which appended the toolkit's standard memory,
session-progress and layout sections.

**How to apply:** when a task seems to call for a new rule or note, put it
somewhere else instead, per the ownership table in `.claude/rules/docs.md`:
- A rule scoped to specific files → a `.claude/rules/*.md` file (new or
  existing), with `paths` frontmatter.
- Day-to-day workflow detail → `DEVELOPMENT.md`.
- Current-state or task-specific info → `.workspace/memory/project_state.md`,
  one line if it's a merge, or a paragraph under an existing section if it's a
  trap/decision.
- A genuinely new repo-wide invariant that must be in AGENTS.md regardless (the
  golden-trace-tier case) → escalate to the user and confirm before adding,
  rather than adding it unilaterally.
