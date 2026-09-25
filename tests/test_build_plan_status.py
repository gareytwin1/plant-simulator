import json
import shutil
import textwrap

import pytest

from scripts import build_plan_status as bps

NODE_MISSING = shutil.which("node") is None


def _plan():
    return {
        "STATUSES": [
            {"k": "todo", "label": "Not Started", "v": "--st-todo"},
            {"k": "prog", "label": "In Progress", "v": "--st-prog"},
            {"k": "block", "label": "Blocked", "v": "--st-block"},
            {"k": "review", "label": "Ready for Review", "v": "--st-review"},
            {"k": "done", "label": "Complete", "v": "--st-done"},
        ],
        "MILESTONES": [
            {"id": "M0", "name": "Baseline"},
            {"id": "M1", "name": "Next Up"},
        ],
        "TASKS": [
            {"id": "A1", "m": "M0", "cat": "core", "n": "First task",
             "d": [], "f": ["app/a.py"], "b": "feature/a1"},
            {"id": "A2", "m": "M0", "cat": "ind", "n": "Second task",
             "d": ["A1"], "f": ["app/b.py", "config/*.yaml"], "b": "feature/a2"},
            {"id": "B1", "m": "M1", "cat": "dep", "n": "Third task",
             "d": ["A1", "A2"], "f": ["app/c.py"], "b": "feature/b1"},
        ],
    }


def _build(plan, status_docs=None, previous_notes=None, repo_root=None, **kw):
    from pathlib import Path

    defaults = dict(refreshed="24 September 2026", main_sha="abc123", tests_passing=42)
    defaults.update(kw)
    return bps.build_status(
        plan,
        status_docs or {},
        previous_notes or {},
        repo_root if repo_root is not None else Path("."),
        **defaults,
    )


def test_task_with_no_status_document_defaults_to_not_started():
    doc = _build(_plan())
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    assert a1["status"] == "Not Started"
    assert a1["status_key"] == "todo"


def test_startable_true_when_all_dependencies_complete():
    status_docs = {"A1": {"status": "done", "note": ""}}
    doc = _build(_plan(), status_docs)
    a2 = next(t for t in doc["tasks"] if t["id"] == "A2")
    assert a2["startable"] is True
    assert "blocked_by" not in a2


def test_blocked_by_lists_unmet_dependencies_in_declared_order():
    doc = _build(_plan())  # nothing complete
    b1 = next(t for t in doc["tasks"] if t["id"] == "B1")
    assert b1["startable"] is False
    assert b1["blocked_by"] == ["A1", "A2"]


def test_complete_task_has_no_startable_or_blocked_by_key():
    status_docs = {"A1": {"status": "done", "note": ""}}
    doc = _build(_plan(), status_docs)
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    assert "startable" not in a1
    assert "blocked_by" not in a1


def test_db_note_wins_when_non_empty():
    status_docs = {"A1": {"status": "done", "note": "fresh note from the tracker"}}
    previous_notes = {"A1": "stale note from the last regeneration"}
    doc = _build(_plan(), status_docs, previous_notes)
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    assert a1["note"] == "fresh note from the tracker"


def test_previous_note_kept_when_db_note_is_empty():
    status_docs = {"A1": {"status": "done", "note": ""}}
    previous_notes = {"A1": "long-form note carried forward"}
    doc = _build(_plan(), status_docs, previous_notes)
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    assert a1["note"] == "long-form note carried forward"


def test_previous_note_kept_when_db_note_is_whitespace_only():
    status_docs = {"A1": {"status": "done", "note": "   "}}
    previous_notes = {"A1": "long-form note carried forward"}
    doc = _build(_plan(), status_docs, previous_notes)
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    assert a1["note"] == "long-form note carried forward"


def test_planned_files_not_on_main_skips_globs_and_flags_missing_file(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "b.py").write_text("present")
    # app/a.py absent, config/*.yaml is a glob and must never be checked
    status_docs = {"A1": {"status": "done", "note": ""}, "A2": {"status": "done", "note": ""}}
    doc = _build(_plan(), status_docs, repo_root=tmp_path)
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    a2 = next(t for t in doc["tasks"] if t["id"] == "A2")
    assert a1["planned_files_not_on_main"] == ["app/a.py"]
    assert "planned_files_not_on_main" not in a2


def test_incomplete_task_never_carries_planned_files_key():
    doc = _build(_plan())  # A1 is "todo", app/a.py doesn't exist under "."
    a1 = next(t for t in doc["tasks"] if t["id"] == "A1")
    assert "planned_files_not_on_main" not in a1


def test_milestone_totals_count_only_that_milestones_tasks():
    status_docs = {"A1": {"status": "done", "note": ""}}
    doc = _build(_plan(), status_docs)
    assert doc["milestones"]["M0"] == {"name": "Baseline", "complete": 1, "total": 2}
    assert doc["milestones"]["M1"] == {"name": "Next Up", "complete": 0, "total": 1}


def test_startable_now_counts_only_todo_tasks_with_satisfied_dependencies():
    status_docs = {"A1": {"status": "done", "note": ""}}
    doc = _build(_plan(), status_docs)
    # A1 done (not counted), A2 todo+startable (counted), B1 todo+blocked (not counted)
    assert doc["totals"]["startable_now"] == 1
    assert doc["totals"]["complete"] == 1
    assert doc["totals"]["tasks"] == 3


def test_render_escapes_non_ascii_and_ends_with_newline():
    text = bps.render({"note": "an em dash — in text"})
    assert text.endswith("\n")
    assert "—" not in text
    assert "\\u2014" in text


def test_render_key_order_matches_insertion_order():
    doc = _build(_plan())
    rendered = bps.render(doc)
    reparsed = json.loads(rendered)
    assert list(reparsed.keys()) == [
        "_comment", "refreshed", "main_sha", "tests_passing_on_main",
        "status_vocabulary", "milestones", "totals", "tasks",
    ]
    a1 = next(t for t in reparsed["tasks"] if t["id"] == "A1")
    assert list(a1.keys())[:9] == [
        "id", "milestone", "name", "category", "branch",
        "depends_on", "files", "status", "status_key",
    ]


def test_load_previous_notes_missing_file_returns_empty_dict(tmp_path):
    assert bps.load_previous_notes(tmp_path / "does-not-exist.json") == {}


def test_load_previous_notes_reads_notes_keyed_by_id(tmp_path):
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"tasks": [{"id": "A1", "note": "old note"}]}))
    assert bps.load_previous_notes(previous) == {"A1": "old note"}


def test_load_status_dir_reads_each_document_keyed_by_filename(tmp_path):
    status_dir = tmp_path / "taskStatus"
    status_dir.mkdir()
    (status_dir / "A1.json").write_text(json.dumps({"status": "done", "note": "", "updated": "x"}))
    (status_dir / "B1.json").write_text(json.dumps({"status": "prog", "note": "wip", "updated": "y"}))
    statuses = bps.load_status_dir(status_dir)
    assert statuses["A1"]["status"] == "done"
    assert statuses["B1"]["note"] == "wip"


_SAMPLE_HTML = textwrap.dedent(
    """\
    <html><body>
    <script>
    (function(){
      "use strict";
      var STATUSES = [
        {k:"todo", label:"Not Started", v:"--st-todo"},
        {k:"done", label:"Complete", v:"--st-done"}
      ];
      var MILESTONES = [
        {id:"M0", name:"Baseline", phase:["core"], obj:"Sample milestone."}
      ];
      var TASKS = [
        {id:"A1", m:"M0", cat:"core", n:"First task",
         p:"desc", d:[], f:["app/a.py"], b:"feature/a1",
         w:["works"], t:["tested"], x:"None."}
      ];
      // ---------- state ----------
      var state = {};
    })();
    </script>
    </body></html>
    """
)


@pytest.mark.skipif(NODE_MISSING, reason="node is required to parse BUILD_PLAN.html")
def test_extract_plan_parses_the_js_data_block(tmp_path):
    html_path = tmp_path / "plan.html"
    html_path.write_text(_SAMPLE_HTML)
    plan = bps.extract_plan(html_path)
    assert [s["k"] for s in plan["STATUSES"]] == ["todo", "done"]
    assert [m["id"] for m in plan["MILESTONES"]] == ["M0"]
    assert plan["TASKS"] == [
        {"id": "A1", "m": "M0", "cat": "core", "n": "First task",
         "p": "desc", "d": [], "f": ["app/a.py"], "b": "feature/a1",
         "w": ["works"], "t": ["tested"], "x": "None."}
    ]


@pytest.mark.skipif(NODE_MISSING, reason="node is required to parse BUILD_PLAN.html")
def test_main_check_mode_reports_drift_then_passes_after_writing(tmp_path, monkeypatch):
    html_path = tmp_path / "plan.html"
    html_path.write_text(_SAMPLE_HTML)
    status_dir = tmp_path / "taskStatus"
    status_dir.mkdir()
    out_path = tmp_path / "status.json"

    monkeypatch.chdir(tmp_path)
    common_args = [
        "--html", str(html_path),
        "--status-dir", str(status_dir),
        "--out", str(out_path),
        "--main-sha", "abc123",
        "--tests-passing", "7",
        "--refreshed", "24 September 2026",
    ]

    assert bps.main([*common_args, "--check"]) == 1
    assert not out_path.exists()

    assert bps.main(common_args) == 0
    assert out_path.exists()

    assert bps.main([*common_args, "--check"]) == 0
