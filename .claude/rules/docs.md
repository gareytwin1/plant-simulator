---
paths:
  - "docs/**"
  - "CLAUDE.md"
  - "DEVELOPMENT.md"
  - "README.md"
---

# Which doc owns what

This repository lost hours twice to documentation drift: CLAUDE.md once
claimed both that snapshot `nodes`/`streams` were empty *and* that they carried
solved numbers, and PROJECT_STATE.md grew to 1,086 lines by re-summarizing
history that BUILD_PLAN_STATUS.json and the ADRs already held. Each fact below
has exactly **one** authoritative home. If you are about to write a sentence
that restates something another file already owns, link to it instead.

| Fact | Owner | Never restated in |
|---|---|---|
| Architectural invariants, contracts, model/commit/status rules | **CLAUDE.md** | PROJECT_STATE.md, DEVELOPMENT.md |
| What is true *right now* — current `main`, next task, open decisions | **PROJECT_STATE.md** | CLAUDE.md |
| Current vs. target architecture, module map, state ownership | **ARCHITECTURE.md** | CLAUDE.md, PROJECT_STATE.md |
| Why a decision was made | **The ADR itself** | CLAUDE.md, PROJECT_STATE.md — link, do not summarize |
| A task's full completion note | **BUILD_PLAN_STATUS.json** | PROJECT_STATE.md — one line only |
| Branch / worktree / merge procedure | **DEVELOPMENT.md** | CLAUDE.md — link, do not restate |
| Test count | **`pytest --collect-only`**, run live | Nowhere — a hand-maintained count has drifted twice already |

## PROJECT_STATE.md regrowth rule

A merged task gets **one line** in PROJECT_STATE.md's recent-merges table. The
full note — files touched, numbers, what it deliberately did not do — goes in
BUILD_PLAN_STATUS.json, where the build-plan tooling already expects it. Do not
add a per-task handoff section to PROJECT_STATE.md; that is exactly the pattern
that grew it past 1,000 lines once.

## Before adding a new stable rule

A repo-wide invariant that applies regardless of which file is open belongs in
CLAUDE.md. A rule that only matters while editing a specific area belongs in a
new or existing `.claude/rules/*.md` file, scoped with `paths` frontmatter to
where it applies. The exception is anything whose failure mode is reaching for
the *wrong tool* rather than editing the *wrong file* — such as the golden-trace
policy or the worktree-per-task rule — which stays in CLAUDE.md even though a
scoped rule also covers it, because a path-scoped rule only loads when a
matching file is *read*, and the session that most needs the warning may never
open one.
