#!/usr/bin/env python3
"""Regenerate docs/BUILD_PLAN_STATUS.json from docs/BUILD_PLAN.html plus a
taskStatus export of the live build-plan artifact.

See DEVELOPMENT.md's "Regenerating docs/BUILD_PLAN_STATUS.json" section for
the full procedure (exporting the taskStatus collection, invoking this
script, and validating the diff before committing).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

STATUS_VOCABULARY = {
    "Not Started": "No work begun",
    "In Progress": "Implementation underway; code may exist on a branch",
    "Blocked": "Waiting on a dependency or decision",
    "Ready for Review": "Code complete, rebased on current main, suite green, PR open, NOT merged",
    "Complete": "Merged to main. Nothing else counts.",
}

COMMENT = (
    "Durable snapshot of the master build plan's per-task status. The live "
    "artifact linked from DEVELOPMENT.md is the interactive authority; this "
    "file exists so the plan's state survives if that artifact becomes "
    "unavailable. Regenerate whenever task status changes materially. "
    "'Complete' means merged to main and nothing else. "
    "'planned_files_not_on_main' flags a Complete task whose planned file "
    "paths are absent from main - usually a harmless rename (e.g. "
    "docs/units.md shipped as docs/UNITS_CONVENTION.md) or an intentional "
    "deletion (T0-1 removed app/simulator.py), but worth checking."
)

# docs/BUILD_PLAN.html's <script> block is JavaScript, not JSON - unquoted
# keys, no trailing-comma restrictions - so TASKS/MILESTONES/STATUSES are
# extracted by evaluating the slice with node rather than hand-parsed. The
# slice is bounded by the same two markers a human would use to find it: the
# start of the page's IIFE and the comment where it switches from static
# data to live UI state.
_NODE_EXTRACTOR = r"""
const fs = require('fs');
const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');
const startMarker = '(function(){';
const endMarker = '// ---------- state ----------';
const startIdx = html.indexOf(startMarker);
if (startIdx === -1) {
  throw new Error('start marker not found: ' + startMarker);
}
const endIdx = html.indexOf(endMarker, startIdx);
if (endIdx === -1) {
  throw new Error('end marker not found: ' + endMarker);
}
const body = html.slice(startIdx + startMarker.length, endIdx);
const build = new Function(
  body + '\nreturn {STATUSES: STATUSES, MILESTONES: MILESTONES, TASKS: TASKS};'
);
process.stdout.write(JSON.stringify(build()));
"""


def extract_plan(html_path: Path) -> dict[str, Any]:
    """Return {"STATUSES": [...], "MILESTONES": [...], "TASKS": [...]}."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(_NODE_EXTRACTOR)
        script_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["node", str(script_path), str(html_path)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "node is required to parse docs/BUILD_PLAN.html's TASKS array "
            "(the page's own client-side data source); install Node.js."
        ) from exc
    finally:
        script_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise SystemExit(f"failed to parse {html_path}:\n{result.stderr}")
    return json.loads(result.stdout)


def load_status_dir(status_dir: Path) -> dict[str, dict[str, Any]]:
    """Load taskStatus/<id>.json documents exported via ArtifactData.

    Each file is the document's data fields directly ({note, status,
    updated}), keyed by filename rather than an id field inside it.
    """
    statuses: dict[str, dict[str, Any]] = {}
    for path in sorted(status_dir.glob("*.json")):
        statuses[path.stem] = json.loads(path.read_text())
    return statuses


def load_previous_notes(previous_path: Path) -> dict[str, str]:
    """Carry forward each task's long-form note when the DB note is empty."""
    if not previous_path.exists():
        return {}
    previous = json.loads(previous_path.read_text())
    return {task["id"]: task.get("note", "") for task in previous.get("tasks", [])}


def files_missing_on_main(files: list[str], repo_root: Path) -> list[str]:
    """Declared files absent from disk, skipping any path containing '*'.

    A glob is never checked, so an actual rename under a globbed path
    doesn't false-flag.
    """
    return [f for f in files if "*" not in f and not (repo_root / f).exists()]


def build_status(
    plan: dict[str, Any],
    status_docs: dict[str, dict[str, Any]],
    previous_notes: dict[str, str],
    repo_root: Path,
    *,
    refreshed: str,
    main_sha: str,
    tests_passing: int,
) -> dict[str, Any]:
    status_labels = {s["k"]: s["label"] for s in plan["STATUSES"]}
    tasks_src = plan["TASKS"]

    def status_of(task_id: str) -> str:
        return status_docs.get(task_id, {}).get("status", "todo")

    done_ids = {t["id"] for t in tasks_src if status_of(t["id"]) == "done"}

    tasks_out = []
    for t in tasks_src:
        status_key = status_of(t["id"])
        db_note = status_docs.get(t["id"], {}).get("note", "")
        note = db_note if db_note.strip() else previous_notes.get(t["id"], "")

        entry: dict[str, Any] = {
            "id": t["id"],
            "milestone": t["m"],
            "name": t["n"],
            "category": t["cat"],
            "branch": t["b"],
            "depends_on": t["d"],
            "files": t["f"],
            "status": status_labels[status_key],
            "status_key": status_key,
        }

        if status_key != "done":
            unmet = [d for d in t["d"] if d not in done_ids]
            entry["startable"] = not unmet
            if unmet:
                entry["blocked_by"] = unmet

        entry["note"] = note

        if status_key == "done":
            missing = files_missing_on_main(t["f"], repo_root)
            if missing:
                entry["planned_files_not_on_main"] = missing

        tasks_out.append(entry)

    milestones_out: dict[str, Any] = {}
    for m in plan["MILESTONES"]:
        m_tasks = [t for t in tasks_src if t["m"] == m["id"]]
        complete = sum(1 for t in m_tasks if status_of(t["id"]) == "done")
        milestones_out[m["id"]] = {
            "name": m["name"],
            "complete": complete,
            "total": len(m_tasks),
        }

    startable_now = sum(
        1 for t in tasks_out if t["status_key"] == "todo" and t.get("startable")
    )

    return {
        "_comment": COMMENT,
        "refreshed": refreshed,
        "main_sha": main_sha,
        "tests_passing_on_main": tests_passing,
        "status_vocabulary": STATUS_VOCABULARY,
        "milestones": milestones_out,
        "totals": {
            "tasks": len(tasks_out),
            "complete": len(done_ids),
            "startable_now": startable_now,
        },
        "tasks": tasks_out,
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=Path("docs/BUILD_PLAN.html"))
    parser.add_argument(
        "--status-dir",
        type=Path,
        required=True,
        help=(
            "Directory of taskStatus/<id>.json documents, as saved by "
            "ArtifactData query/list with out_dir set - pass the taskStatus "
            "folder itself, not its parent."
        ),
    )
    parser.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="Existing status JSON to carry forward notes from. Defaults to --out.",
    )
    parser.add_argument("--out", type=Path, default=Path("docs/BUILD_PLAN_STATUS.json"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Root used to check whether a Complete task's declared files still exist.",
    )
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--tests-passing", type=int, required=True)
    parser.add_argument("--refreshed", required=True, help='e.g. "24 September 2026"')
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare against --out instead of writing it; exit 1 on any diff.",
    )
    args = parser.parse_args(argv)

    previous_path = args.previous if args.previous is not None else args.out

    plan = extract_plan(args.html)
    status_docs = load_status_dir(args.status_dir)
    previous_notes = load_previous_notes(previous_path)

    document = build_status(
        plan,
        status_docs,
        previous_notes,
        args.repo_root,
        refreshed=args.refreshed,
        main_sha=args.main_sha,
        tests_passing=args.tests_passing,
    )
    rendered = render(document)

    if args.check:
        existing = args.out.read_text() if args.out.exists() else ""
        if rendered != existing:
            sys.stderr.write(
                f"{args.out} is out of date with {args.html} / {args.status_dir}\n"
            )
            return 1
        return 0

    args.out.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
