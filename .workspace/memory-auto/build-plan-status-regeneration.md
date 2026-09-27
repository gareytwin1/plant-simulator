---
name: build-plan-status-regeneration
description: BUILD_PLAN_STATUS.json is derived from the live artifact; never hand-patch it, and beware the page blanking an ArtifactData note
metadata:
  node_type: memory
  type: project
  modified: 2026-09-27T00:00:00.000Z
---

`docs/BUILD_PLAN_STATUS.json` is regenerated, never hand-patched, and only on
`main` at merge close-out. The procedure and the generator
(`scripts/build_plan_status.py`, with `--check` to prove it reproduces the
committed file first) are in DEVELOPMENT.md's "Regenerating
docs/BUILD_PLAN_STATUS.json" section - follow that, not a re-derivation.

**Trap the procedure does not cover: a note you write via ArtifactData can be
silently blanked.** The page's own `write()` sends `{status, note, updated}`
from its in-memory state, and its note input is empty unless someone typed in
it, so an open browser tab touching that task overwrites the note with `""`.
Read the document back after writing and pin the rewrite to the version it
reports. The JSON keeps the long-form note either way (DB note wins only when
non-empty).

**Why:** regenerating on a task branch put every open PR behind main and made
PR #76 conflict (decided 25 Sep 2026, PR #79).

Related: [[claude-attribution]]
