"""Alarm banner and summary (T10-5): static/js/alarms.js and static/css/alarms.css.

There is no JavaScript test runner in this repo, so the pure half of alarms.js
runs under Node (a subprocess, JSON in and out) and is fed history recorded
from the real AlarmManager and AlarmHistory through the real alarm blueprint.
Acknowledging is checked end to end: the request alarms.js builds is sent to
the Flask endpoint, and the re-fetched history is rendered again.

Neither the blueprint nor this page is wired into the live plant (Snapshot.alarms
is empty by design), so every history here is built by the test.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent, _alarm_id
from app.api.alarms import create_alarm_blueprint
from app.envelope.evaluator import Severity

ROOT = Path(__file__).resolve().parent.parent
ALARMS_JS = ROOT / "static" / "js" / "alarms.js"
ALARMS_CSS = ROOT / "static" / "css" / "alarms.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(body, **data):
    """Evaluate `body` (the body of an async function with `A`, the alarms.js
    exports, and `data` in scope) under Node and return its JSON result."""
    script = (
        f"const A = require({json.dumps(str(ALARMS_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class Plant:
    """A real manager, history and alarm blueprint, driven by the test."""

    def __init__(self):
        self.manager = AlarmManager()
        self.history = AlarmHistory(capacity=100)
        self.sim_time = 0.0
        app = Flask(__name__)
        app.register_blueprint(
            create_alarm_blueprint(
                lambda: self.manager, lambda: self.history, lambda: self.sim_time
            )
        )
        self.client = app.test_client()

    def set_band(self, tag, pv, severity, side="hi", at=None):
        if at is not None:
            self.sim_time = at
        point = EnvelopeEvent(tag=tag, pv=pv, severity=severity, side=side)
        self.history.record_events(self.manager.evaluate([point], self.sim_time))
        self.history.record_clears(self.manager.last_cleared(), self.sim_time)
        return _alarm_id(tag, pv)

    def entries(self):
        response = self.client.get("/api/alarms/history")
        assert response.status_code == 200
        return response.get_json()


@pytest.fixture
def plant():
    return Plant()


def derive(entries):
    return run_js("return A.deriveAlarms(data.entries)", entries=entries)


def states(entries):
    return {alarm["tag"]: alarm["state"] for alarm in derive(entries)}


@needs_node
def test_a_raised_alarm_is_unacknowledged_until_acknowledged(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)

    assert states(plant.entries()) == {"K-101": "unack"}

    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert states(plant.entries()) == {"K-101": "acked"}


@needs_node
def test_clearing_an_unacknowledged_alarm_keeps_it_as_return_to_normal_unack(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    plant.set_band("K-101", "discharge pressure", Severity.NORMAL, at=9.0)

    assert states(plant.entries()) == {"K-101": "rtn_unack"}

    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})

    assert states(plant.entries()) == {}


@needs_node
def test_clearing_an_acknowledged_alarm_removes_it(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})
    plant.set_band("K-101", "discharge pressure", Severity.NORMAL, at=9.0)

    assert states(plant.entries()) == {}


@needs_node
def test_reactivating_while_return_to_normal_unack_is_one_unacknowledged_alarm(plant):
    plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    plant.set_band("K-101", "discharge pressure", Severity.NORMAL, at=9.0)
    plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=12.0)

    alarms = derive(plant.entries())

    assert [(a["tag"], a["state"], a["raisedAt"]) for a in alarms] == [("K-101", "unack", 5.0)]


@needs_node
def test_escalation_updates_the_priority_of_the_same_alarm(plant):
    plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=8.0)

    alarms = derive(plant.entries())

    assert [(a["priority"], a["raisedAt"]) for a in alarms] == [("critical", 5.0)]


@needs_node
def test_an_acknowledge_whose_raising_event_was_evicted_is_ignored():
    entries = [{"type": "acknowledge", "id": "gone", "tag": "K-101", "sim_time": 3.0}]

    assert derive(entries) == []


def mixed_plant(plant):
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=1.0)
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=2.0)
    plant.set_band("E-101", "outlet temperature", Severity.ALARM, at=3.0)
    plant.set_band("K-102", "vibration", Severity.TRIP, at=4.0)
    return plant


@needs_node
def test_banner_shows_the_most_urgent_alarm_with_a_tally_per_priority(plant):
    mixed_plant(plant)

    html = run_js("return A.renderBanner(A.deriveAlarms(data.entries))", entries=plant.entries())

    assert 'data-priority="critical"' in html.split("alarm-banner-tallies")[0]
    assert 'data-state="unack"' in html.split("alarm-banner-tallies")[0]
    # The earliest critical alarm leads the banner.
    assert "K-101 discharge pressure" in html.split("alarm-banner-tallies")[0]
    assert "CRIT 2" in html and "HIGH 1" in html and "LOW 1" in html


@needs_node
def test_banner_names_every_priority_in_text_not_colour_alone(plant):
    mixed_plant(plant)

    html = run_js("return A.renderBanner(A.deriveAlarms(data.entries))", entries=plant.entries())

    for label in ("CRIT", "HIGH", "LOW"):
        assert label in html


@needs_node
def test_banner_with_no_alarms_says_so():
    html = run_js("return A.renderBanner([])")

    assert "No active alarms" in html
    assert "data-priority" not in html


@needs_node
def test_banner_leads_with_an_unacknowledged_alarm_over_an_acknowledged_one_of_equal_priority(plant):
    first = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("K-102", "vibration", Severity.TRIP, at=2.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})

    html = run_js("return A.renderBanner(A.deriveAlarms(data.entries))", entries=plant.entries())

    assert "K-102 vibration" in html.split("alarm-banner-tallies")[0]


@needs_node
def test_text_from_the_history_is_escaped():
    entries = [
        {
            "type": "alarm",
            "id": "x",
            "tag": "<b>T-1</b>",
            "sim_time": 1.0,
            "priority": "low",
            "message": '<img src=x onerror="boom()">',
            "data": {},
        }
    ]

    banner = run_js("return A.renderBanner(A.deriveAlarms(data.entries))", entries=entries)
    summary = run_js("return A.renderSummary(A.deriveAlarms(data.entries))", entries=entries)

    for html in (banner, summary):
        assert "<img" not in html and "<b>" not in html
        assert "&lt;img" in html


def summary_rows(html):
    return re.findall(r'<tr class="alarm-row" data-priority="(\w+)" data-state="(\w+)">', html)


@needs_node
def test_summary_defaults_to_most_urgent_first_and_offers_acknowledge_only_where_needed(plant):
    first = plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=1.0)
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=2.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})

    html = run_js("return A.renderSummary(A.deriveAlarms(data.entries))", entries=plant.entries())

    assert summary_rows(html) == [("critical", "unack"), ("low", "acked")]
    assert html.count('class="alarm-ack"') == 1
    assert 'aria-sort="ascending"' in html


@needs_node
@pytest.mark.parametrize(
    ("key", "descending", "tags"),
    [
        ("tag", False, ["E-101", "K-101", "K-102", "P-101"]),
        ("tag", True, ["P-101", "K-102", "K-101", "E-101"]),
        ("time", False, ["P-101", "K-101", "E-101", "K-102"]),
        ("time", True, ["K-102", "E-101", "K-101", "P-101"]),
        ("priority", False, ["K-101", "K-102", "E-101", "P-101"]),
        ("priority", True, ["P-101", "E-101", "K-101", "K-102"]),
    ],
)
def test_summary_sorts_by_any_column_in_either_direction(plant, key, descending, tags):
    mixed_plant(plant)

    sorted_alarms = run_js(
        "return A.sortAlarms(A.deriveAlarms(data.entries), data.key, data.descending)",
        entries=plant.entries(),
        key=key,
        descending=descending,
    )

    assert [alarm["tag"] for alarm in sorted_alarms] == tags


@needs_node
def test_summary_marks_the_sorted_column_with_its_direction(plant):
    mixed_plant(plant)

    html = run_js(
        "return A.renderSummary(A.deriveAlarms(data.entries), {key: 'tag', descending: true})",
        entries=plant.entries(),
    )

    assert re.findall(r'aria-sort="(\w+)"><button[^>]*data-sort="(\w+)"', html) == [
        ("none", "priority"),
        ("none", "state"),
        ("descending", "tag"),
        ("none", "message"),
        ("none", "time"),
    ]


@needs_node
def test_acknowledge_round_trips_through_the_real_endpoint(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    before = run_js(
        "return A.renderSummary(A.deriveAlarms(data.entries))", entries=plant.entries()
    )
    assert f'data-alarm-id="{alarm_id}"' in before

    request = run_js("return A.buildAcknowledgeRequest(data.id)", id=alarm_id)
    plant.sim_time = 7.0
    response = plant.client.post(
        request["url"], data=request["init"]["body"], headers=request["init"]["headers"]
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "recorded": True}
    after = run_js(
        "return A.renderSummary(A.deriveAlarms(data.entries))", entries=plant.entries()
    )
    assert 'data-state="acked"' in after
    assert "alarm-ack" not in after


@needs_node
def test_clicking_acknowledge_posts_then_refetches_and_redraws(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    plant.sim_time = 7.0

    # Node cannot call back into Python, so replay the two history snapshots
    # the real endpoint produces: before the click, and after the acknowledge.
    before = plant.entries()
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})
    after = plant.entries()

    result = run_js(
        """
        const calls = [];
        const responses = [data.before, data.after];
        const fakeFetch = async (url, init) => {
          calls.push({url, method: (init && init.method) || 'GET', body: init && init.body});
          if (init && init.method === 'POST') return {ok: true, json: async () => ({ok: true})};
          return {ok: true, json: async () => responses.shift()};
        };
        let handler;
        const banner = {innerHTML: ''};
        const summary = {
          innerHTML: '',
          addEventListener: (type, fn) => { if (type === 'click') handler = fn; },
        };
        const click = (attr, value) => handler({target: {closest: (sel) =>
          sel === '[' + attr + ']' ? {getAttribute: () => value} : null}});
        const console_ = A.mount(banner, summary, {fetch: fakeFetch});
        await console_.ready;
        const drawn = summary.innerHTML;
        await click('data-alarm-id', data.id);
        return {calls, drawn, redrawn: summary.innerHTML, banner: banner.innerHTML};
        """,
        before=before,
        after=after,
        id=alarm_id,
    )

    posts = [c for c in result["calls"] if c["method"] == "POST"]
    assert len(posts) == 1
    assert json.loads(posts[0]["body"]) == {"alarm_id": alarm_id}
    assert 'data-state="unack"' in result["drawn"]
    assert 'data-state="acked"' in result["redrawn"]
    assert 'data-state="acked"' in result["banner"]


@needs_node
def test_a_stale_history_response_does_not_overwrite_a_newer_one(plant):
    plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    older = plant.entries()
    plant.set_band("K-102", "vibration", Severity.TRIP, at=6.0)
    newer = plant.entries()

    result = run_js(
        """
        const resolvers = [];
        const fakeFetch = () => new Promise((resolve) => resolvers.push(resolve));
        const respond = (i, entries) => resolvers[i]({ok: true, json: async () => entries});
        const banner = {innerHTML: ''};
        const summary = {innerHTML: '', addEventListener: () => {}};
        const mounted = A.mount(banner, summary, {fetch: fakeFetch});
        const second = mounted.refresh();
        respond(1, data.newer);
        await second;
        respond(0, data.older);
        await mounted.ready;
        return summary.innerHTML;
        """,
        older=older,
        newer=newer,
    )

    assert "K-102" in result


def css_tokens_used(css):
    return set(re.findall(r"var\((--[\w-]+)\)", css))


def defined_tokens():
    return set(re.findall(r"^\s*(--[\w-]+):", TOKENS_CSS.read_text(), re.MULTILINE))


def test_alarm_css_uses_only_tokens_that_exist():
    css = ALARMS_CSS.read_text()
    defined = defined_tokens() | set(re.findall(r"^\s*(--alarm-(?:fill|on|mark|glyph)):", css, re.MULTILINE))

    assert css_tokens_used(css) - defined == set()


def test_alarm_css_has_no_colour_literals():
    css = ALARMS_CSS.read_text()

    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(", css)


def test_every_priority_has_a_glyph_and_colour_mapping_in_the_css():
    css = ALARMS_CSS.read_text()

    for priority in ("critical", "high", "low"):
        block = re.search(rf'\[data-priority="{priority}"\]\s*\{{(.*?)\}}', css, re.DOTALL)
        assert block is not None, priority
        for role in ("fill", "on", "mark"):
            assert f"var(--alarm-{priority}-{role})" in block.group(1)
        assert f"var(--symbol-alarm-{priority})" in block.group(1)


def test_flashing_has_a_reduced_motion_fallback():
    css = ALARMS_CSS.read_text()

    reduced = css.split("prefers-reduced-motion: reduce", 1)[1]
    assert "animation: none" in reduced
    assert "border-width" in reduced


def test_alarm_js_has_no_colour_literals():
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", ALARMS_JS.read_text())
