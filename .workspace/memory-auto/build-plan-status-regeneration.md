---
name: build-plan-status-regeneration
description: How to regenerate docs/BUILD_PLAN_STATUS.json from the live build-plan artifact instead of hand-patching it
metadata: 
  node_type: memory
  type: project
  originSessionId: 9fdb1f64-a0ae-4df9-8cee-9d8bf57c5ce4
  modified: 2026-09-23T04:03:36.302Z
---

`docs/BUILD_PLAN_STATUS.json` is a derived file with three separate sources,
which is why hand-patching it drifts:

- **Task definitions** (name, category, branch, depends_on, files) come from the
  `var TASKS = [...]` array inside the single `<script>` block of
  `docs/BUILD_PLAN.html`, which is byte-identical to the live artifact
  `https://claude.ai/artifact/DXqzpwKxeKZNzZGrC3HkQ9`. Extract by slicing the
  script between `(function(){` and `// ---------- state ----------` and
  evaluating that prefix in node.
- **Per-task status** comes from the artifact's `taskStatus` collection
  (ArtifactData). Only ~43 of the 101 tasks have a document; anything absent
  defaults to `todo`. Keys map `todo/prog/block/review/done` →
  `Not Started/In Progress/Blocked/Ready for Review/Complete`.
- **Notes**: the DB note wins where non-empty; otherwise keep the long-form note
  already in the JSON, keyed by task id. Most DB documents have an empty note.

Reproduction details that matter for a clean diff: `json.dumps(..., indent=2,
ensure_ascii=True)` (the committed file escapes non-ASCII — em dashes, °F —
as `\uXXXX`; `ensure_ascii=False` was tried and tested wrong on 22 Sep,
confirmed by the byte-identical validation below only after switching) plus a
trailing newline, and per-task key order `id, milestone, name, category, branch,
depends_on, files, status, status_key, [startable], [blocked_by], note,
[planned_files_not_on_main]`. `startable` is present on every not-Complete
task; `totals.startable_now` counts only tasks that are both `todo` and
dependency-free, matching the artifact's own "startable right now" board.
`planned_files_not_on_main` (Complete tasks only) lists each declared file
that doesn't exist on disk, skipping any path containing `*` entirely — a
glob is never checked, so an actual rename under a globbed path doesn't
false-flag.

`ArtifactData`'s `list`/`query` with `out_dir` saves each `taskStatus`
document as `<out_dir>/taskStatus/<task_id>.json`, and the file is the
document's `data` fields directly (`{note, status, updated}`) — not wrapped
in `{id, data, version}`. Load it keyed by filename, not by an `id` field
inside it.

**Why:** the build plan says not to hand-patch the generated status JSON, but no
generator is checked in, so each session re-derives this. Getting encoding or
key order wrong produces a 400-line diff that hides the real change.

**How to apply:** update the live artifact (HTML for definitions, ArtifactData
for status) *first*, then regenerate the JSON from it and diff — the diff should
show only the intended fields. Publishing to the artifact requires having Read
the saved live copy in full first, or the publish is refused.

**Validate the generator before trusting it:** rebuild the *current* committed
JSON from the pre-change artifact HTML and pre-change status docs. It should come
out byte-identical. If it does, the regenerated file's diff is trustworthy.

**A note you write via ArtifactData can be silently blanked.** The page's own
`write()` sends `{status, note, updated}` from its in-memory state, and its note
input is empty unless someone typed in it — so an open browser tab touching that
task overwrites the note with `""`. Read the document back after writing, and
pin the rewrite to the version it reports. The durable JSON keeps the long-form
note either way (DB note wins only when non-empty).

Related: [[no-claude-attribution]]
