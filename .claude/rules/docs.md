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
| Architectural invariants, contracts, model guidance, status vocabulary | **CLAUDE.md** | PROJECT_STATE.md, DEVELOPMENT.md |
| What is true *right now* — current `main`, next task, open decisions | **PROJECT_STATE.md** | CLAUDE.md |
| Current vs. target architecture, module map, state ownership | **ARCHITECTURE.md** | CLAUDE.md, PROJECT_STATE.md |
| Why a decision was made | **The ADR itself** | CLAUDE.md, PROJECT_STATE.md — link, do not summarize |
| A task's full completion note | **BUILD_PLAN_STATUS.json** | PROJECT_STATE.md — one line only |
| Branch / worktree / merge procedure, commit conventions and examples, file ownership table | **DEVELOPMENT.md** | CLAUDE.md — one-line summary and a link, never the full table or examples |
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
where it applies.

**Exception: give a scoped rule a one-line CLAUDE.md backstop when getting it
wrong is silent and expensive.** A path-scoped rule only loads when a matching
file is opened with `Read`, and not at all through `Bash`/`grep`/`sed` — so the
session that most needs the warning may never see it. Three rules get this
backstop even though a `.claude/rules/*.md` file also covers them in full:
the golden-trace policy and the worktree-per-task rule (wrong *tool*, not
wrong *file* — a session can violate either without opening anything the
scope would catch), and the branch-characteristic monotonicity requirement
(a single non-conforming device can silently break the solver's convergence
for the *entire* plant, not just its own branch). A rule whose violation is
caught by the code itself — a rejected config, a failing guard test — does
not need this; the failure mode there is a clear error, not silence.
