"""Alarm overlay (T10-5, T20-12): static/js/alarms.js and static/css/alarms.css.

There is no JavaScript test runner in this repo, so the pure half of alarms.js
runs under Node (a subprocess, JSON in and out) and is fed history recorded
from the real AlarmManager and AlarmHistory through the real alarm blueprint.
Acknowledging is checked end to end: the request alarms.js builds is sent to
the Flask endpoint, and the re-fetched history is rendered again. The mounted
overlay runs under Node too, against a stand-in page.

Every history here is built by the test, from the real manager and history.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from app.alarms.acknowledge import acknowledge_alarm
from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent, _alarm_id
from app.api.alarms import create_alarm_blueprint
from app.envelope.evaluator import Severity

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_CSS = ROOT / "static" / "css" / "console.css"
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
                self.history.entries,
                lambda alarm_id: acknowledge_alarm(
                    self.manager, self.history, alarm_id, self.sim_time
                ),
            )
        )
        self.client = app.test_client()

    def set_band(self, tag, pv, severity, side="hi", at=None, point=None):
        if at is not None:
            self.sim_time = at
        point = EnvelopeEvent(tag=tag, pv=pv, severity=severity, side=side, point=point)
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
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=1.0, point="P-101.suction_pressure")
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=2.0, point="K-101.discharge_pressure")
    plant.set_band("E-101", "outlet temperature", Severity.ALARM, at=3.0)
    plant.set_band("K-102", "vibration", Severity.TRIP, at=4.0, point="K-102.vibration")
    return plant


def shown(entries, **filters):
    return run_js(
        "return A.shownAlarms(A.deriveAlarms(data.entries), data.filters)", entries=entries, filters=filters
    )


def tags(alarms):
    return [alarm["tag"] for alarm in alarms]


@needs_node
def test_an_alarm_carries_the_point_its_event_names_and_none_when_it_names_none(plant):
    mixed_plant(plant)

    points = {alarm["tag"]: alarm["point"] for alarm in derive(plant.entries())}

    assert points == {
        "P-101": "P-101.suction_pressure",
        "K-101": "K-101.discharge_pressure",
        "E-101": None,
        "K-102": "K-102.vibration",
    }


@needs_node
def test_the_overlay_lists_the_most_urgent_alarm_first(plant):
    mixed_plant(plant)

    assert tags(shown(plant.entries())) == ["K-101", "K-102", "E-101", "P-101"]


@needs_node
def test_priority_filter_keeps_only_the_checked_priorities(plant):
    mixed_plant(plant)

    assert tags(shown(plant.entries(), priorities=["critical"])) == ["K-101", "K-102"]
    assert tags(shown(plant.entries(), priorities=["high", "low"])) == ["E-101", "P-101"]
    assert shown(plant.entries(), priorities=[]) == []


@needs_node
def test_acknowledgement_filter_separates_what_still_wants_the_operator(plant):
    first = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=2.0)
    plant.set_band("E-101", "outlet temperature", Severity.ALARM, at=3.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})
    plant.set_band("E-101", "outlet temperature", Severity.NORMAL, at=4.0)

    assert tags(shown(plant.entries(), acknowledgement="unacked")) == ["E-101", "P-101"]
    assert tags(shown(plant.entries(), acknowledgement="acked")) == ["K-101"]
    assert tags(shown(plant.entries(), acknowledgement="all")) == ["K-101", "E-101", "P-101"]


@needs_node
def test_equipment_and_text_filters_match_tag_and_message_without_case(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("K-101", "vibration", Severity.ALARM, at=2.0)
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=3.0)

    assert tags(shown(plant.entries(), tag="K-101")) == ["K-101", "K-101"]
    assert tags(shown(plant.entries(), text="  PRESSURE ")) == ["K-101", "P-101"]
    assert tags(shown(plant.entries(), text="p-101")) == ["P-101"]
    assert shown(plant.entries(), text="no such thing") == []


@needs_node
def test_filters_combine(plant):
    mixed_plant(plant)

    result = shown(
        plant.entries(), priorities=["critical", "low"], acknowledgement="unacked", tag="K-101", text="discharge"
    )

    assert tags(result) == ["K-101"]
    assert shown(plant.entries(), priorities=["low"], tag="K-101") == []


@needs_node
def test_the_tag_filter_keeps_a_chosen_tag_whose_alarms_have_cleared(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=2.0)

    got = run_js(
        "const a = A.deriveAlarms(data.entries); return [A.equipmentTags(a, 'all'), A.equipmentTags(a, 'Z-9')]",
        entries=plant.entries(),
    )

    assert got == [["K-101", "P-101"], ["K-101", "P-101", "Z-9"]]


@needs_node
def test_the_summary_line_counts_active_unacknowledged_and_shown(plant):
    first = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=2.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})

    line = run_js(
        "const a = A.deriveAlarms(data.entries); return A.summaryLine(a, A.shownAlarms(a, {tag: 'K-101'}))",
        entries=plant.entries(),
    )

    assert line == "2 active \u00b7 1 not acknowledged \u00b7 showing 1"


def rows_of(html):
    return re.findall(r'<tr class="alarm-row" data-priority="(\w+)" data-state="(\w+)">', html)


def render_rows(entries, **filters):
    return run_js(
        "return A.renderRows(A.shownAlarms(A.deriveAlarms(data.entries), data.filters))",
        entries=entries,
        filters=filters,
    )


@needs_node
def test_rows_offer_acknowledge_only_where_needed_and_show_trend_only_with_a_point(plant):
    first = plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=1.0, point="P-101.suction_pressure")
    plant.set_band("E-101", "outlet temperature", Severity.ALARM, at=2.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})

    html = render_rows(plant.entries())

    assert rows_of(html) == [("high", "unack"), ("low", "acked")]
    assert html.count('data-row-action="ack"') == 1
    assert html.count('data-row-action="trend"') == 1
    assert html.count('data-row-action="locate"') == 2
    first_row, second_row = html.split("</tr>")[:2]
    assert 'data-row-action="ack"' in first_row and 'data-row-action="trend"' not in first_row
    assert 'data-row-action="trend"' in second_row and 'data-row-action="ack"' not in second_row


@needs_node
def test_a_returned_to_normal_alarm_not_yet_acknowledged_reads_returned_to_normal(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=2.0)
    plant.set_band("K-101", "discharge pressure", Severity.NORMAL, at=3.0)

    html = render_rows(plant.entries())
    returned, active = html.split("</tr>")[:2]

    assert "Returned to normal" in returned and 'data-state="rtn_unack"' in returned
    assert "Active" in active and "Returned to normal" not in active
    # Not acknowledged yet, so it still offers the acknowledgement.
    assert 'data-row-action="ack"' in returned and 'data-acknowledged="no"' in returned


@needs_node
def test_rows_name_every_priority_in_text_not_colour_alone(plant):
    mixed_plant(plant)

    html = render_rows(plant.entries())

    for label in ("CRIT", "HIGH", "LOW"):
        assert label in html


@needs_node
def test_with_no_alarms_the_overlay_says_so_and_a_filter_that_hides_all_says_that(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)

    got = run_js(
        "return [A.emptyText([]), A.emptyText(A.deriveAlarms(data.entries)), A.renderRows([])]",
        entries=plant.entries(),
    )

    assert got == ["No active alarms.", "No alarms match these filters.", ""]


@needs_node
def test_text_from_the_history_is_escaped():
    entries = [
        {
            "type": "alarm",
            "id": '"><x>',
            "tag": "<b>T-1</b>",
            "sim_time": 1.0,
            "priority": "low",
            "message": '<img src=x onerror="boom()">',
            "data": {"point": "T-1.level"},
        }
    ]

    html = render_rows(entries)
    options = run_js("return A.renderTagOptions(['<i>'], '<i>')")

    for markup in (html, options):
        assert "<img" not in markup and "<b>" not in markup and "<i>" not in markup and "<x>" not in markup
    assert "&lt;img" in html


@needs_node
@pytest.mark.parametrize(
    ("key", "descending", "expected"),
    [
        ("tag", False, ["E-101", "K-101", "K-102", "P-101"]),
        ("tag", True, ["P-101", "K-102", "K-101", "E-101"]),
        ("time", False, ["P-101", "K-101", "E-101", "K-102"]),
        ("time", True, ["K-102", "E-101", "K-101", "P-101"]),
        ("priority", False, ["K-101", "K-102", "E-101", "P-101"]),
        ("priority", True, ["P-101", "E-101", "K-101", "K-102"]),
    ],
)
def test_alarms_sort_by_any_key_in_either_direction(plant, key, descending, expected):
    mixed_plant(plant)

    sorted_alarms = run_js(
        "return A.sortAlarms(A.deriveAlarms(data.entries), data.key, data.descending)",
        entries=plant.entries(),
        key=key,
        descending=descending,
    )

    assert tags(sorted_alarms) == expected


@needs_node
def test_acknowledge_round_trips_through_the_real_endpoint(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    before = render_rows(plant.entries())
    assert f'data-alarm-id="{alarm_id}"' in before and 'data-row-action="ack"' in before

    request = run_js("return A.buildAcknowledgeRequest(data.id)", id=alarm_id)
    plant.sim_time = 7.0
    response = plant.client.post(
        request["url"], data=request["init"]["body"], headers=request["init"]["headers"]
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "recorded": True}
    after = render_rows(plant.entries())
    assert 'data-state="acked"' in after
    assert 'data-row-action="ack"' not in after


# ---- The mounted overlay, against a stand-in page ---------------------------

# Every element the overlay touches, a document that takes key listeners, and
# focus that is recorded.
STUB = """
const focusLog = [];
const mk = (id, extra) => Object.assign({ id, hidden: false, disabled: false, checked: true, value: '',
  textContent: '', innerHTML: '', attrs: {}, listeners: {}, controls: [],
  setAttribute(k, v) { this.attrs[k] = String(v); },
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
  removeAttribute(k) { delete this.attrs[k]; },
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
  fire(t, props) { const e = Object.assign({ type: t, target: this, preventDefault() { e.prevented = true; } }, props);
    (this.listeners[t] || []).forEach(f => f(e)); return e; },
  contains(n) { return n === this; },
  querySelector() { return null; },
  querySelectorAll() { return this.controls; },
  focus() { doc.activeElement = this; focusLog.push(this.id); } }, extra || {});
const ids = ['alarm-overlay', 'alarm-overlay-close', 'alarm-overlay-sub', 'alarm-rows', 'alarm-empty', 'alarm-feedback',
  'ack-visible', 'filter-state', 'filter-tag', 'filter-search', 'filter-critical', 'filter-high', 'filter-low',
  'count-critical', 'count-high', 'count-low', 'ribbon-alarms'];
const els = Object.fromEntries(ids.map(id => [id, mk(id)]));
els['filter-state'].value = 'all';
els['filter-tag'].value = 'all';
els['alarm-overlay'].controls = [els['alarm-overlay-close'], els['ack-visible']];
// The table's buttons, as rendered: what a redraw can put focus back on.
const inRows = new Set();
els['alarm-rows'].querySelectorAll = () => [...els['alarm-rows'].innerHTML.matchAll(/data-row-action="(\w+)" data-alarm-id="([^"]*)"/g)]
  .map(m => { const id = m[1] + '|' + m[2].replace(/&quot;/g, '"');
    const b = mk(id, { attrs: { 'data-row-action': m[1], 'data-alarm-id': m[2].replace(/&quot;/g, '"') } });
    inRows.add(b); return b; });
els['alarm-rows'].contains = n => inRows.has(n);
const workspace = mk('workspace');
const keys = [];
const doc = { getElementById: id => els[id] || null, activeElement: null, addEventListener(t, f) { if (t === 'keydown') keys.push(f); },
  removeEventListener(t, f) { const at = keys.indexOf(f); if (t === 'keydown' && at >= 0) keys.splice(at, 1); } };
const reply = (body) => ({ ok: true, status: 200, json: async () => body });
// A button in the table: the row listener asks the target for its closest row action.
const press = (action, id) => {
  const button = { getAttribute: k => (k === 'data-row-action' ? action : id) };
  return els['alarm-rows'].fire('click', { target: { closest: () => button } });
};
// The ids of the rows with an action, as rendered.
const rowIds = (action) => [...els['alarm-rows'].innerHTML.matchAll(new RegExp('data-row-action="' + action + '" data-alarm-id="([^"]+)"', 'g'))].map(m => m[1]);
const settle = () => new Promise(r => setTimeout(r, 0));
"""


def mounted(body, **data):
    return run_js(STUB + body, **data)


@needs_node
def test_opening_moves_focus_in_makes_the_workspace_inert_and_closing_gives_both_back(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)

    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'], inert: workspace });
        const before = { hidden: els['alarm-overlay'].hidden, inert: workspace.getAttribute('inert') };
        els['ribbon-alarms'].fire('click');
        await overlay.ready;
        const open = { hidden: els['alarm-overlay'].hidden, inert: workspace.getAttribute('inert'),
          expanded: els['ribbon-alarms'].getAttribute('aria-expanded'), focus: doc.activeElement.id,
          rows: els['alarm-rows'].innerHTML.includes('K-101'), sub: els['alarm-overlay-sub'].textContent };
        els['alarm-overlay-close'].fire('click');
        const closed = { hidden: els['alarm-overlay'].hidden, inert: workspace.getAttribute('inert'),
          expanded: els['ribbon-alarms'].getAttribute('aria-expanded'), focus: doc.activeElement.id };
        return { before, open, closed };
        """,
        entries=plant.entries(),
    )

    assert got["before"] == {"hidden": False, "inert": None}
    assert got["open"] == {
        "hidden": False, "inert": "", "expanded": "true", "focus": "alarm-overlay-close", "rows": True,
        "sub": "1 active \u00b7 1 not acknowledged \u00b7 showing 1",
    }
    assert got["closed"] == {"hidden": True, "inert": None, "expanded": "false", "focus": "ribbon-alarms"}


@needs_node
def test_escape_the_backdrop_and_the_ribbon_button_each_close_the_overlay_to_the_ribbon():
    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply([]), opener: els['ribbon-alarms'], inert: workspace });
        const results = [];
        const state = () => ({ open: overlay.isOpen(), focus: doc.activeElement && doc.activeElement.id });
        const reopen = async () => { overlay.open(); await overlay.ready; };

        await reopen();
        keys.forEach(f => f({ key: 'Escape' }));
        results.push(state());

        await reopen();
        // A click on the panel is not a click outside it.
        els['alarm-overlay'].fire('click', { target: { id: 'panel' } });
        results.push(state());
        els['alarm-overlay'].fire('click');
        results.push(state());

        await reopen();
        els['ribbon-alarms'].fire('click');
        results.push(state());

        keys.forEach(f => f({ key: 'Escape' }));
        results.push(state());
        return results;
        """,
    )

    assert got == [
        {"open": False, "focus": "ribbon-alarms"},
        {"open": True, "focus": "alarm-overlay-close"},
        {"open": False, "focus": "ribbon-alarms"},
        {"open": False, "focus": "ribbon-alarms"},
        {"open": False, "focus": "ribbon-alarms"},
    ]


@needs_node
def test_tab_wraps_inside_the_dialog_in_both_directions():
    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply([]), opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        const tab = (shiftKey) => els['alarm-overlay'].fire('keydown', { key: 'Tab', shiftKey });
        els['ack-visible'].disabled = false;
        els['ack-visible'].focus();
        const forward = tab(false);
        const afterForward = doc.activeElement.id;
        const backward = tab(true);
        const afterBackward = doc.activeElement.id;
        // From the middle of the dialog the browser's own order stands.
        doc.activeElement = { id: 'middle' };
        const middle = tab(false);
        return { forward: forward.prevented, afterForward, backward: backward.prevented, afterBackward, middle: !!middle.prevented };
        """,
    )

    assert got == {
        "forward": True, "afterForward": "alarm-overlay-close",
        "backward": True, "afterBackward": "ack-visible", "middle": False,
    }


@needs_node
def test_acknowledge_all_shown_acknowledges_exactly_the_unacknowledged_rows_shown(plant):
    first = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.set_band("K-101", "vibration", Severity.ALARM, at=2.0)
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=3.0)
    plant.set_band("E-101", "outlet temperature", Severity.ALARM, at=4.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})
    entries = plant.entries()

    got = mounted(
        """
        const posts = [];
        const doFetch = async (url, init) => {
          if (init && init.method === 'POST') { posts.push(JSON.parse(init.body).alarm_id); return reply({ ok: true }); }
          return reply(data.entries);
        };
        let acknowledged = 0;
        const overlay = A.mount(doc, { fetch: doFetch, opener: els['ribbon-alarms'], onAcknowledge: () => { acknowledged += 1; } });
        overlay.open();
        await overlay.ready;
        const unackedOnScreen = [...rowIds('ack')];

        // Only the K-101 rows are shown; one of them is already acknowledged.
        els['filter-tag'].value = 'K-101';
        els['filter-tag'].fire('change');
        const shownIds = [...rowIds('locate')];
        const buttonOff = els['ack-visible'].disabled;
        els['ack-visible'].fire('click');
        await settle(); await settle();
        return { posts, unackedOnScreen, shownIds, buttonOff, acknowledged };
        """,
        entries=entries,
    )

    k101_vibration = _alarm_id("K-101", "vibration")
    assert len(got["unackedOnScreen"]) == 3
    assert len(got["shownIds"]) == 2
    assert got["buttonOff"] is False
    assert got["posts"] == [k101_vibration]
    assert got["acknowledged"] == 1


@needs_node
def test_acknowledge_all_shown_is_off_when_nothing_shown_needs_it(plant):
    first = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": first})

    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        return { off: els['ack-visible'].disabled, empty: els['alarm-empty'].hidden };
        """,
        entries=plant.entries(),
    )

    assert got == {"off": True, "empty": True}


@needs_node
def test_a_failed_acknowledgement_says_so_and_the_list_is_fetched_again(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)

    got = mounted(
        """
        let gets = 0;
        const doFetch = async (url, init) => {
          if (init && init.method === 'POST') return { ok: false, status: 500, json: async () => ({}) };
          gets += 1;
          return reply(data.entries);
        };
        const overlay = A.mount(doc, { fetch: doFetch, opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        press('ack', data.id);
        await settle(); await settle();
        return { note: els['alarm-feedback'].textContent, gets };
        """,
        entries=plant.entries(),
        id=alarm_id,
    )

    assert got == {"note": "1 acknowledgement could not be recorded.", "gets": 2}


@needs_node
def test_show_trend_and_locate_close_the_overlay_and_leave_focus_to_the_canvas(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0, point="K-101.discharge_pressure")
    alarm_id = _alarm_id("K-101", "discharge pressure")

    got = mounted(
        """
        const called = [];
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'], inert: workspace,
          onShowTrend: a => called.push(['trend', a.tag, a.point, overlay.isOpen(), workspace.getAttribute('inert')]),
          onLocate: a => called.push(['locate', a.tag, a.point, overlay.isOpen()]) });
        overlay.open();
        await overlay.ready;
        await press('trend', data.id);
        overlay.open();
        await overlay.ready;
        await press('locate', data.id);
        return { called, focusLog: focusLog.filter(id => id === 'ribbon-alarms') };
        """,
        entries=plant.entries(),
        id=alarm_id,
    )

    assert got["called"] == [
        ["trend", "K-101", "K-101.discharge_pressure", False, None],
        ["locate", "K-101", "K-101.discharge_pressure", False],
    ]
    assert got["focusLog"] == [], "the ribbon button does not take focus back from the canvas"


@needs_node
def test_the_history_is_polled_only_while_the_overlay_is_open():
    got = mounted(
        """
        const ticks = new Map();
        let next = 0;
        globalThis.setInterval = fn => { ticks.set(++next, fn); return next; };
        globalThis.clearInterval = id => { ticks.delete(id); };
        let gets = 0;
        const overlay = A.mount(doc, { fetch: async () => { gets += 1; return reply([]); }, opener: els['ribbon-alarms'], pollMs: 15 });
        const closed = { timers: ticks.size, gets };
        overlay.open();
        await overlay.ready;
        const open = { timers: ticks.size, gets };
        ticks.forEach(fn => fn());
        await settle();
        const afterTick = gets;
        overlay.close();
        return { closed, open, afterTick, after: ticks.size };
        """,
    )

    assert got == {"closed": {"timers": 0, "gets": 0}, "open": {"timers": 1, "gets": 1}, "afterTick": 2, "after": 0}


@needs_node
def test_after_acknowledging_focus_never_lands_on_another_alarms_acknowledge(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0, point="K-101.discharge_pressure")
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=2.0)
    k101 = _alarm_id("K-101", "discharge pressure")
    first = plant.entries()
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": k101})
    second = plant.entries()

    got = mounted(
        """
        const replies = [data.first, data.second];
        const doFetch = async (url, init) => (init && init.method === 'POST' ? reply({ ok: true }) : reply(replies.shift() || data.second));
        const overlay = A.mount(doc, { fetch: doFetch, opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        // Focus is on the top row's Acknowledge when it is pressed.
        const buttons = els['alarm-rows'].querySelectorAll();
        const ack = buttons.find(b => b.getAttribute('data-row-action') === 'ack');
        doc.activeElement = ack;
        press('ack', ack.getAttribute('data-alarm-id'));
        await settle(); await settle();
        return doc.activeElement.id;
        """,
        first=first,
        second=second,
    )

    # K-101 is acknowledged and its row keeps Show trend and Locate: focus stays in that row.
    assert got == f"trend|{k101}"


@needs_node
def test_focus_goes_to_the_close_button_when_the_alarms_row_is_gone(plant):
    alarm_id = plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    first = plant.entries()
    plant.set_band("K-101", "discharge pressure", Severity.NORMAL, at=2.0)
    plant.client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id})
    gone = plant.entries()
    plant.set_band("P-101", "suction pressure", Severity.WARNING, side="lo", at=3.0)
    other = plant.entries()

    got = mounted(
        """
        const replies = [data.first, data.other];
        const overlay = A.mount(doc, { fetch: async () => reply(replies.shift() || data.other), opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        const ack = els['alarm-rows'].querySelectorAll().find(b => b.getAttribute('data-row-action') === 'ack');
        doc.activeElement = ack;
        await overlay.refresh();
        return doc.activeElement.id;
        """,
        first=first,
        other=other,
    )

    assert got == "alarm-overlay-close"


@needs_node
def test_an_alarm_id_with_quotes_does_not_break_the_redraw():
    entries = [
        {"type": "alarm", "id": '1:a"]\\x', "tag": "A", "sim_time": 1.0, "priority": "low", "message": "m", "data": {}}
    ]

    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        doc.activeElement = els['alarm-rows'].querySelectorAll()[0];
        els['filter-search'].value = 'a';
        els['filter-search'].fire('input');
        return els['alarm-rows'].innerHTML.includes('data-row-action="ack"');
        """,
        entries=entries,
    )

    assert got is True


@needs_node
@pytest.mark.parametrize(
    ("action", "words"),
    [("trend", "Could not show a trend"), ("locate", "not drawn on the canvas")],
)
def test_a_show_trend_or_locate_that_finds_nothing_reopens_the_overlay_and_says_so(plant, action, words):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0, point="K-101.discharge_pressure")
    alarm_id = _alarm_id("K-101", "discharge pressure")

    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'], inert: workspace,
          onShowTrend: () => false, onLocate: () => false });
        overlay.open();
        await overlay.ready;
        const buttons = els['alarm-rows'].querySelectorAll();
        doc.activeElement = buttons[0];
        press(data.action, data.id);
        return { open: overlay.isOpen(), inert: workspace.getAttribute('inert'), note: els['alarm-feedback'].textContent,
          focus: doc.activeElement.id };
        """,
        entries=plant.entries(),
        id=alarm_id,
        action=action,
    )

    assert got["open"] is True and got["inert"] == ""
    assert words in got["note"]
    assert got["focus"] == f"{action}|{alarm_id}"


@needs_node
def test_a_handler_that_throws_is_treated_as_finding_nothing(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0, point="K-101.discharge_pressure")
    alarm_id = _alarm_id("K-101", "discharge pressure")

    got = mounted(
        """
        console.error = () => {};
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'],
          onShowTrend: () => { throw new Error('boom'); } });
        overlay.open();
        await overlay.ready;
        press('trend', data.id);
        return { open: overlay.isOpen(), note: els['alarm-feedback'].textContent };
        """,
        entries=plant.entries(),
        id=alarm_id,
    )

    assert got == {"open": True, "note": "Could not show a trend for this alarm."}


@needs_node
def test_stopping_the_overlay_takes_its_escape_listener_off_the_document():
    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply([]), opener: els['ribbon-alarms'] });
        const mounted_ = keys.length;
        overlay.stop();
        return [mounted_, keys.length];
        """,
    )

    assert got == [1, 0]


@needs_node
def test_a_show_trend_that_shows_something_leaves_the_overlay_closed(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0, point="K-101.discharge_pressure")
    alarm_id = _alarm_id("K-101", "discharge pressure")

    got = mounted(
        """
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'], onShowTrend: () => true });
        overlay.open();
        await overlay.ready;
        focusLog.length = 0;
        press('trend', data.id);
        return { open: overlay.isOpen(), focusLog };
        """,
        entries=plant.entries(),
        id=alarm_id,
    )

    assert got == {"open": False, "focusLog": []}


@needs_node
def test_a_redraw_with_nothing_new_leaves_the_table_alone(plant):
    plant.set_band("K-101", "discharge pressure", Severity.TRIP, at=1.0)

    got = mounted(
        """
        let writes = 0;
        let html = '';
        Object.defineProperty(els['alarm-rows'], 'innerHTML', { get: () => html, set: v => { writes += 1; html = v; } });
        const overlay = A.mount(doc, { fetch: async () => reply(data.entries), opener: els['ribbon-alarms'] });
        overlay.open();
        await overlay.ready;
        await overlay.refresh();
        await overlay.refresh();
        return writes;
        """,
        entries=plant.entries(),
    )

    assert got == 1


@needs_node
def test_a_stale_history_response_does_not_overwrite_a_newer_one(plant):
    plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=5.0)
    older = plant.entries()
    plant.set_band("K-102", "vibration", Severity.TRIP, at=6.0)
    newer = plant.entries()

    result = mounted(
        """
        const resolvers = [];
        const fakeFetch = () => new Promise((resolve) => resolvers.push(resolve));
        const respond = (i, entries) => resolvers[i]({ok: true, json: async () => entries});
        const overlay = A.mount(doc, {fetch: fakeFetch, opener: els['ribbon-alarms']});
        overlay.open();
        const first = overlay.ready;
        const second = overlay.refresh();
        respond(1, data.newer);
        await second;
        respond(0, data.older);
        await first;
        return els['alarm-rows'].innerHTML;
        """,
        older=older,
        newer=newer,
    )

    assert "K-102" in result


# ---- The page ---------------------------------------------------------------


@pytest.fixture
def client():
    # The console starts the plant's scheduler, so the session is ended after.
    from app import main

    client = main.app.test_client()
    yield client
    cookie = client.get_cookie(main.SESSION_COOKIE)
    if cookie is not None:
        main.sessions.end(cookie.value)


def test_the_console_page_is_an_overlay_dialog_opened_from_the_ribbon_and_has_no_alarm_section(client):
    page = client.get("/console").get_data(as_text=True)

    assert re.search(r'<button[^>]*id="ribbon-alarms"[^>]*aria-haspopup="dialog"[^>]*aria-controls="alarm-overlay"', page)
    assert re.search(r'<section[^>]*id="alarm-overlay"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*hidden', page)
    assert 'aria-labelledby="alarm-overlay-title"' in page
    for gone in ("alarm-banner", 'id="alarm-summary"', "alarms-heading", "console-sections"):
        assert gone not in page


def test_the_status_line_is_always_in_the_page_so_it_is_announced_when_it_fills():
    page = (ROOT / "templates" / "console.html").read_text()

    assert re.search(r'<p[^>]*id="alarm-feedback"[^>]*role="status"[^>]*></p>', page)
    assert 'id="alarm-feedback"' in page and "hidden" not in re.search(r'<p[^>]*id="alarm-feedback"[^>]*>', page).group(0)


def test_the_overlay_has_every_element_the_script_looks_up():
    page = (ROOT / "templates" / "console.html").read_text()
    script = ALARMS_JS.read_text()

    wanted = set(re.findall(r'\$\("([\w-]+)"\)', script)) | {
        f"{prefix}-{priority}" for prefix in ("filter", "count") for priority in ("critical", "high", "low")
    }

    for element in sorted(wanted - {"ribbon-alarms"}):
        assert f'id="{element}"' in page, element


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


def rule_for(css, selector):
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.DOTALL)
    assert block is not None, selector
    return block.group(1)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("priority", ["critical", "high", "low"])
def test_the_returned_to_normal_badge_outline_is_visible_on_the_overlay_surface(theme, priority):
    from test_console_tokens import GRAPHIC_CONTRAST, contrast, load_tokens

    css = ALARMS_CSS.read_text()
    returned = rule_for(css, '.alarm-row[data-state="rtn_unack"] .alarm-badge')
    assert "border-color: var(--alarm-mark)" in returned
    panel = rule_for(css, ".overlay-panel")
    assert "background-color: var(--surface-raised)" in panel

    colours = load_tokens()[theme]
    edge, surface = colours[f"--alarm-{priority}-mark"], colours["--surface-raised"]
    assert contrast(edge, surface) >= GRAPHIC_CONTRAST


def test_the_overlay_covers_the_workspace_and_stacks_above_what_floats_over_it():
    css = ALARMS_CSS.read_text()
    trends = (ROOT / "static" / "css" / "trends.css").read_text()
    console = CONSOLE_CSS.read_text()

    z = int(re.search(r"z-index:\s*(\d+)", rule_for(css, ".alarm-overlay")).group(1))
    floating = [int(n) for n in re.findall(r"z-index:\s*(\d+)", trends + console)]
    assert z > max(floating)
    assert "inset: 0" in rule_for(css, ".alarm-overlay")
