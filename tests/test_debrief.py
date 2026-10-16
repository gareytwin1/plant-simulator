"""The result and debrief panel - T20-22: static/js/debrief.js, its place in
templates/console.html and the ribbon's Result button.

The pure half of debrief.js runs under Node. The mounted panel runs under Node
too, in tests/fake_dom.js, against a stand-in fetch or, where a test says
"over HTTP", against the real app served on a local port with the session's
cookie. How it looks, light and dark, was checked in a real browser.
"""

import json
import re
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml
from werkzeug.serving import make_server

from app import main
from app.scenarios.runner import scenario_key

ROOT = Path(__file__).resolve().parent.parent
DEBRIEF_JS = ROOT / "static" / "js" / "debrief.js"
RIBBON_JS = ROOT / "static" / "js" / "ribbon.js"
FAKE_DOM = (ROOT / "tests" / "fake_dom.js").read_text()
SCORING = ROOT / "config" / "scoring.yaml"
CSS = ROOT / "static" / "css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

KEY = scenario_key("pump_trip")
DOT = "·"
DEBRIEF = "P-101 has just tripped.\n\nDiagnosis path - restart P-101."


def run_js(script_body, **data):
    script = (
        f"const D = require({json.dumps(str(DEBRIEF_JS))});"
        f"const R = require({json.dumps(str(RIBBON_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {script_body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def finished(outcome="failed", **extra):
    body = {
        "phase": "aborted" if outcome == "aborted" else "complete",
        "outcome": outcome,
        "key": KEY,
        "title": "Falling vessel level",
        "elapsed_s": 339.4,
        "time_limit_s": 600.0,
        "actions": [],
        "score": None if outcome == "aborted" else {"total": 41.6, "penalties": {"outcome": 1.0, "alarm_count": 0.2, "trip_count": 0.0}},
        "debrief": DEBRIEF,
    }
    body.update(extra)
    return body


# The console's elements that the ribbon, its run controls and the panel
# touch, built in the fake DOM: ids are looked up through the body.
PAGE = (
    FAKE_DOM
    + """
doc.getElementById = id => doc.body.all().find(n => n.getAttribute('id') === id) || null;
doc.removeEventListener = () => {};
function add(tag, id, attrs = {}) {
  const made = doc.createElement(tag);
  made.setAttribute('id', id);
  Object.entries(attrs).forEach(([k, v]) => made.setAttribute(k, v));
  made.hidden = false;
  doc.body.appendChild(made);
  return made;
}
function page(phase) {
  add('header', 'ribbon');
  add('button', 'ribbon-alarms');
  add('span', 'ribbon-alarm-summary');
  ['run-clock', 'run-clock-value', 'run-clock-inline', 'scenario-title', 'scenario-state'].forEach(id => add('span', id));
  const bar = add('div', 'scenario-bar', { 'data-phase': phase, 'data-title': 'Falling vessel level',
    'data-phase-labels': JSON.stringify({ idle: '', loaded: 'Loaded, not started', running: 'Running', complete: 'Finished', aborted: 'Aborted' }) });
  ['scenario-start', 'scenario-abort', 'scenario-abort-confirm', 'scenario-abort-keep', 'ribbon-pause', 'ribbon-resume']
    .forEach(id => bar.appendChild(add('button', id)));
  bar.appendChild(add('span', 'scenario-confirm'));
  bar.appendChild(add('a', 'scenario-choose', { href: '/' }));
  bar.appendChild(add('span', 'scenario-choose-label'));
  bar.appendChild(add('button', 'scenario-result'));
  add('p', 'scenario-notice');
  const workspace = add('section', 'workspace');
  add('p', 'debrief-status');
  const panel = add('section', 'debrief');
  workspace.appendChild(panel);
  return { bar, panel, result: doc.getElementById('scenario-result') };
}
const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const text = node => node.textContent;
const byClass = (root, name) => root.all().find(n => n.classes.includes(name));
const button = (root, words) => root.all().find(n => n.tagName === 'BUTTON' && n.textContent.includes(words));
const focusedId = () => doc.activeElement && doc.activeElement.getAttribute('id');
"""
)

HTTP = """
const calls = [];
const http = async (url, init) => {
  calls.push(url);
  const headers = Object.assign({}, (init && init.headers) || {}, { cookie: data.cookie });
  return fetch(data.base + url, Object.assign({}, init || {}, { headers }));
};
"""


@pytest.fixture
def client():
    client = main.app.test_client()
    yield client
    cookie = client.get_cookie(main.SESSION_COOKIE)
    if cookie is not None:
        main.sessions.end(cookie.value)


@pytest.fixture
def served():
    server = make_server("127.0.0.1", 0, main.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.socket.getsockname()[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5.0)


def cookie_of(client):
    return f"{main.SESSION_COOKIE}={client.get_cookie(main.SESSION_COOKIE).value}"


# ---- the view -----------------------------------------------------------


@needs_node
@pytest.mark.parametrize(
    ("outcome", "words", "glyph", "total"),
    [
        ("succeeded", "Succeeded", "✓", "42"),
        ("failed", "Failed", "✗", "42"),
        ("timed_out", "Timed out", "✗", "42"),
        ("aborted", "Aborted", "■", ""),
    ],
)
def test_each_outcome_renders_its_words_its_glyph_and_when_scored_its_total(outcome, words, glyph, total):
    got = run_js(
        PAGE
        + """
        const { panel } = page(data.body.phase);
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        debrief.result(200, data.body);
        const value = byClass(panel, 'debrief-score-value');
        return { view: D.view(200, data.body), words: text(byClass(panel, 'debrief-outcome-text')),
          glyph: text(byClass(panel, 'debrief-glyph')), hidden: byClass(panel, 'debrief-glyph').getAttribute('aria-hidden'),
          total: value ? text(value) : '', score: text(byClass(panel, 'debrief-score')) };
        """,
        body=finished(outcome),
    )

    assert got["words"] == words
    assert got["glyph"] == glyph
    assert got["hidden"] == "true"
    assert got["view"]["outcomeText"] == words
    assert got["view"]["outcome"] == outcome
    assert got["total"] == total
    if outcome == "aborted":
        assert got["score"] == "Not scored, because the run was aborted."
    else:
        assert got["score"] == f"Score {total} out of 100"


@needs_node
def test_the_view_carries_the_run_time_the_criteria_that_cost_points_and_the_debrief():
    got = run_js("return D.view(200, data.body);", body=finished("failed"))

    assert got["time"] == "Run time 05:39 of 10:00"
    assert got["title"] == "Falling vessel level"
    assert got["costs"] == [
        {"text": "The run did not succeed", "extent": "in full"},
        {"text": "Alarms raised", "extent": "a little"},
    ]
    assert got["debrief"] == ["P-101 has just tripped.", "Diagnosis path - restart P-101."]


@needs_node
@pytest.mark.parametrize("phase", ["loaded", "running"])
def test_a_loaded_or_running_body_renders_no_panel_and_no_debrief_whatever_it_carries(phase):
    got = run_js(
        PAGE
        + """
        const { panel, result } = page(data.body.phase);
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        debrief.result(200, data.body);
        const shown = { view: D.view(200, data.body), panel: !panel.hidden, button: !result.hidden,
          text: text(panel) };
        // A run that was finished, then another tab loads or starts one.
        debrief.result(200, data.done);
        debrief.result(200, data.body);
        return [shown, { panel: !panel.hidden, button: !result.hidden, text: text(panel) }];
        """,
        body=finished("failed", phase=phase),
        done=finished("succeeded"),
    )

    for seen in got:
        assert seen.get("view") is None
        assert seen["panel"] is False
        assert seen["button"] is False
        for leaked in ("Failed", "Succeeded", "Score", "Diagnosis", "P-101", "did not succeed", "Run time"):
            assert leaked not in seen["text"]


@needs_node
def test_free_play_hides_the_panel_and_the_result_button():
    got = run_js(
        PAGE
        + """
        const { panel, result } = page('complete');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        debrief.result(200, data.done);
        const before = !panel.hidden;
        debrief.result(409, null);
        return { before, panel: !panel.hidden, button: !result.hidden, text: text(panel).includes('Diagnosis') };
        """,
        done=finished("failed"),
    )

    assert got == {"before": True, "panel": False, "button": False, "text": False}


# ---- criteria -------------------------------------------------------------


@needs_node
def test_every_penalty_name_in_the_scoring_config_is_worded():
    weights = yaml.safe_load(SCORING.read_text())["weights"]
    criteria = run_js("return D.CRITERIA;")

    assert set(weights) <= set(criteria)
    for name in weights:
        assert criteria[name] != name
        assert "_" not in criteria[name]


@needs_node
def test_only_criteria_that_cost_points_are_listed_heaviest_first_with_how_much_and_an_unknown_one_as_its_name():
    got = run_js(
        "return D.costs(data.penalties);",
        penalties={"new_criterion": 0.5, "alarm_count": 0.25, "trip_count": 1.0, "peak_severity": 0.0, "outcome": 0},
    )

    assert got == [
        {"text": "Equipment tripped", "extent": "in full"},
        {"text": "Alarms raised", "extent": "partly"},
        {"text": "new_criterion", "extent": "partly"},
    ]


@needs_node
def test_a_scored_run_that_lost_nothing_says_so():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        D.mount(doc, { fetch: async () => reply(500, null) }).result(200, data.body);
        return byClass(panel, 'debrief-costs').children.map(text);
        """,
        body=finished("succeeded", score={"total": 100.0, "penalties": {"outcome": 0.0}}),
    )

    assert got == ["Nothing counted against the score."]


# ---- opening, closing and focus ------------------------------------------------


@needs_node
def test_the_panel_opens_itself_once_per_finished_run():
    got = run_js(
        PAGE
        + """
        const { panel } = page('running');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        const seen = [];
        debrief.result(200, Object.assign({}, data.done, { phase: 'running' }));
        seen.push(!panel.hidden);
        debrief.result(200, data.done);
        seen.push(!panel.hidden);
        debrief.close();
        debrief.result(200, data.done);
        seen.push(!panel.hidden);
        // The next run, finished, opens it again.
        debrief.result(200, Object.assign({}, data.done, { phase: 'running' }));
        debrief.result(200, data.done);
        seen.push(!panel.hidden);
        debrief.close();
        // A different run finished in another tab between two polls.
        debrief.result(200, Object.assign({}, data.done, { elapsed_s: 12.0 }));
        seen.push(!panel.hidden);
        return seen;
        """,
        done=finished("failed"),
    )

    assert got == [False, True, False, True, True]


@needs_node
def test_the_panel_is_a_dialog_that_takes_focus_and_escape_hands_it_back_to_the_result_button():
    got = run_js(
        PAGE
        + """
        const { panel, result } = page('complete');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        debrief.result(200, data.done);
        const opened = { focus: focusedId(), expanded: result.getAttribute('aria-expanded') };
        const escape = panel.fire('keydown', { key: 'Escape' });
        const closed = { panel: !panel.hidden, focus: focusedId(), expanded: result.getAttribute('aria-expanded'),
          prevented: escape.defaultPrevented };
        result.fire('click');
        const reopened = { panel: !panel.hidden, focus: focusedId() };
        button(panel, '✕').fire('click');
        return { opened, closed, reopened, after: { panel: !panel.hidden, focus: focusedId(), button: !result.hidden } };
        """,
        done=finished("failed"),
    )

    assert got["opened"] == {"focus": "debrief", "expanded": "true"}
    assert got["closed"] == {"panel": False, "focus": "scenario-result", "expanded": "false", "prevented": True}
    assert got["reopened"] == {"panel": True, "focus": "debrief"}
    assert got["after"] == {"panel": False, "focus": "scenario-result", "button": True}


@needs_node
def test_a_panel_that_opens_itself_leaves_focus_in_a_faceplate_and_announces_itself_instead():
    got = run_js(
        PAGE
        + """
        const { panel } = page('running');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        const elsewhere = add('input', 'faceplate-entry');
        elsewhere.focus();
        debrief.result(200, data.done);
        const status = doc.getElementById('debrief-status');
        const opened = { open: !panel.hidden, focus: focusedId(), status: text(status) };
        debrief.close();
        return { opened, closed: { open: !panel.hidden, focus: focusedId(), status: text(status) } };
        """,
        done=finished("failed"),
    )

    assert got["opened"] == {
        "open": True,
        "focus": "faceplate-entry",
        "status": "Run finished: Failed. The result is open above the plant.",
    }
    assert got["closed"] == {"open": False, "focus": "faceplate-entry", "status": ""}


@needs_node
def test_a_panel_that_opens_itself_takes_focus_from_the_ribbons_run_controls():
    got = run_js(
        PAGE
        + """
        const { panel } = page('running');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        doc.getElementById('scenario-abort').focus();
        debrief.result(200, data.done);
        const opened = focusedId();
        panel.fire('keydown', { key: 'Escape' });
        return { opened, closed: focusedId() };
        """,
        done=finished("failed"),
    )

    assert got == {"opened": "debrief", "closed": "scenario-abort"}


@needs_node
def test_escape_in_the_panel_closes_only_the_panel_not_a_faceplate_the_canvas_would_close():
    got = run_js(
        PAGE
        + """
        const { panel, result } = page('complete');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        const reached = [];
        doc.addEventListener('keydown', event => reached.push(event.key));
        debrief.result(200, data.done);
        result.focus();
        debrief.close();
        result.fire('click');
        panel.fire('keydown', { key: 'Escape' });
        return { reached, open: !panel.hidden, focus: focusedId() };
        """,
        done=finished("failed"),
    )

    assert got == {"reached": [], "open": False, "focus": "scenario-result"}


# ---- try again ------------------------------------------------------------


@needs_node
def test_try_again_loads_the_finished_runs_key_and_starts_it_then_the_console_shows_the_new_run_over_http(client, served):
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    client.post("/api/scenario/abort")

    got = run_js(
        PAGE
        + HTTP
        + """
        const { panel, result } = page('aborted');
        let ribbon = null;
        const changed = [];
        const debrief = D.mount(doc, { fetch: http, onRetry: (s, b, t) => ribbon.adopt(s, b, t) });
        ribbon = R.mount(doc, { fetch: http, pollMs: 0, onChange: () => changed.push('change'),
          onResult: (s, b) => debrief.result(s, b) });
        await ribbon.ready;
        const before = { panel: !panel.hidden, outcome: text(byClass(panel, 'debrief-outcome-text')),
          state: text(doc.getElementById('scenario-state')) };
        calls.length = 0;
        await button(panel, 'Try again').listeners.click[0]();
        return { before, sent: calls, state: text(doc.getElementById('scenario-state')), panel: !panel.hidden,
          button: !result.hidden, focus: focusedId(), changed: changed.length, text: text(panel) };
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert got["before"] == {"panel": True, "outcome": "Aborted", "state": f"Scenario {DOT} Aborted"}
    assert got["sent"] == ["/api/scenario/load", "/api/scenario/start"]
    assert got["state"] == f"Scenario {DOT} Running"
    assert got["panel"] is False
    assert got["button"] is False
    assert got["focus"] == "scenario-abort"
    assert got["changed"] >= 1
    assert "Diagnosis" not in got["text"]
    result = client.get("/api/scenario/result").get_json()
    assert result["phase"] == "running"
    assert result["key"] == KEY


@needs_node
@pytest.mark.parametrize(
    ("status", "words"),
    [
        (409, "Another run has started since this one finished"),
        (404, "That scenario is not available any more"),
        (400, "That scenario could not be set up"),
        (429, "Too many requests"),
        (503, "The request did not finish"),
        (418, "Something went wrong, so nothing was changed"),
    ],
)
def test_a_refused_load_is_worded_by_status_code_in_the_panel_never_from_the_server(status, words):
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const adopted = [];
        const debrief = D.mount(doc, {
          fetch: async () => ({ ok: false, status: data.status, json: async () => ({ error: 'scenario pump_trip refused' }) }),
          onRetry: (...args) => adopted.push(args) });
        debrief.result(200, data.done);
        const retry = button(panel, 'Try again');
        await retry.listeners.click[0]();
        return { notice: text(byClass(panel, 'debrief-notice')), adopted, panel: !panel.hidden, disabled: retry.disabled };
        """,
        status=status,
        done=finished("failed"),
    )

    assert got["notice"].startswith(words)
    assert "pump_trip" not in got["notice"]
    assert got["adopted"] == []
    assert got["panel"] is True
    assert got["disabled"] is False


@needs_node
def test_a_start_refused_after_the_load_hands_the_loaded_run_and_the_words_to_the_ribbon():
    got = run_js(
        PAGE
        + """
        page('complete');
        const adopted = [];
        const answers = [reply(200, { phase: 'loaded', key: data.key }), reply(429, null)];
        const debrief = D.mount(doc, { fetch: async () => answers.shift(), onRetry: (...args) => adopted.push(args) });
        debrief.result(200, data.done);
        await byClass(doc.getElementById('debrief'), 'btn-primary').listeners.click[0]();
        return adopted;
        """,
        key=KEY,
        done=finished("failed"),
    )

    assert got == [[200, {"phase": "loaded", "key": KEY}, "Too many requests. Wait a moment and try again."]]


@needs_node
def test_choose_another_scenario_is_the_ribbons_link_back_to_the_scenarios():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        D.mount(doc, { fetch: async () => reply(500, null) }).result(200, data.done);
        const link = panel.all().find(n => n.tagName === 'A');
        return { href: link.getAttribute('href'), text: text(link) };
        """,
        done=finished("failed"),
    )

    assert got == {"href": "/", "text": "Choose another scenario"}


# ---- the page -------------------------------------------------------------


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.found = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.found[attrs["id"]] = {"tag": tag, "attrs": attrs, "inside": " ".join(self.stack).split()}
        if tag not in ("input", "link", "meta", "br", "img"):
            self.stack.append(attrs.get("class") or "")

    def handle_endtag(self, tag):
        if tag not in ("input", "link", "meta", "br", "img") and self.stack:
            self.stack.pop()


def test_the_console_has_a_non_modal_labelled_panel_inside_the_workspace_and_a_result_button(client):
    page = client.get("/console").get_data(as_text=True)
    parsed = Elements()
    parsed.feed(page)
    panel = parsed.found["debrief"]
    button = parsed.found["scenario-result"]

    assert panel["attrs"]["role"] == "dialog"
    assert panel["attrs"]["aria-modal"] == "false"
    assert panel["attrs"]["aria-labelledby"] == "debrief-title"
    assert panel["attrs"]["tabindex"] == "-1"
    assert "hidden" in panel["attrs"]
    assert "workspace" in panel["inside"]
    assert button["tag"] == "button"
    assert button["attrs"]["aria-controls"] == "debrief"
    assert "hidden" in button["attrs"]
    assert page.index("js/ribbon.js") < page.index("js/debrief.js")
    assert "css/debrief.css" in page


def test_the_result_button_is_on_the_console_only(client):
    assert 'id="scenario-result"' not in client.get("/").get_data(as_text=True)


def z_index(path, selector):
    match = re.search(re.escape(selector) + r"\s*\{[^}]*z-index:\s*(\d+)", path.read_text())
    assert match, selector
    return int(match.group(1))


def test_the_panel_sits_above_the_trend_windows_and_below_the_faceplates_and_the_alarm_overlay():
    panel = z_index(CSS / "debrief.css", ".debrief")

    assert z_index(CSS / "trends.css", ".window-layer") < panel
    assert z_index(CSS / "trends.css", ".tray") < panel
    assert panel < z_index(CSS / "console.css", ".faceplate-layer")
    assert panel < z_index(CSS / "alarms.css", ".alarm-overlay")


@needs_node
def test_an_adopted_run_makes_a_poll_already_in_flight_stale():
    got = run_js(
        PAGE
        + """
        page('complete');
        let release = null;
        const fetch = url => url.endsWith('/result')
          ? new Promise(resolve => { release = () => resolve(reply(200, data.done)); })
          : Promise.resolve(reply(200, []));
        const ribbon = R.mount(doc, { fetch, pollMs: 0 });
        await settle();
        ribbon.adopt(200, { phase: 'running', title: 'Falling vessel level' }, '');
        release();
        await ribbon.ready;
        return text(doc.getElementById('scenario-state'));
        """,
        done=finished("failed"),
    )

    assert got == f"Scenario {DOT} Running"


@needs_node
def test_a_criterion_too_small_to_move_the_rounded_total_still_says_how_little_it_cost():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        D.mount(doc, { fetch: async () => reply(500, null) }).result(200, data.body);
        return { score: text(byClass(panel, 'debrief-score')), costs: byClass(panel, 'debrief-costs').children.map(text) };
        """,
        body=finished("succeeded", score={"total": 99.8, "penalties": {"outcome": 0.0, "production_lost": 0.04}}),
    )

    assert got == {"score": "Score 100 out of 100", "costs": ["Production lost, a little"]}


@needs_node
def test_a_start_still_in_flight_when_a_run_is_adopted_does_not_overwrite_it():
    got = run_js(
        PAGE
        + """
        page('loaded');
        let release = null;
        const fetch = url => url.endsWith('/start')
          ? new Promise(resolve => { release = () => resolve(reply(200, { phase: 'running', title: 'Old run' })); })
          : Promise.resolve(reply(200, data.done));
        const ribbon = R.mount(doc, { fetch, pollMs: 0 });
        await ribbon.ready;
        const sending = doc.getElementById('scenario-start').listeners.click[0]();
        ribbon.adopt(200, { phase: 'loaded', title: 'New run' }, 'Words from the panel');
        release();
        await sending;
        return { state: text(doc.getElementById('scenario-state')), title: text(doc.getElementById('scenario-title')),
          notice: text(doc.getElementById('scenario-notice')), disabled: doc.getElementById('scenario-start').disabled };
        """,
        done=finished("failed", phase="loaded"),
    )

    assert got == {
        "state": f"Scenario {DOT} Loaded, not started",
        "title": "New run",
        "notice": "Words from the panel",
        "disabled": False,
    }


@needs_node
def test_a_run_loaded_elsewhere_while_the_panel_has_focus_hands_focus_to_a_run_control():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const debrief = D.mount(doc, { fetch: async () => reply(500, null) });
        debrief.result(200, data.done);
        const had = focusedId();
        button(panel, 'Try again').focus();
        debrief.result(200, Object.assign({}, data.done, { phase: 'loaded' }));
        await settle();
        return { had, panel: !panel.hidden, focus: focusedId() };
        """,
        done=finished("failed"),
    )

    assert got == {"had": "debrief", "panel": False, "focus": "scenario-start"}


@needs_node
def test_a_refusal_the_panel_can_no_longer_show_goes_to_the_ribbon_as_a_notice():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const adopted = [];
        let release = null;
        const debrief = D.mount(doc, {
          fetch: () => new Promise(resolve => { release = () => resolve(reply(429, null)); }),
          onRetry: (...args) => adopted.push(['retry', ...args]), onRefusal: text => adopted.push(['refusal', text]) });
        debrief.result(200, data.done);
        const sending = button(panel, 'Try again').listeners.click[0]();
        debrief.result(200, Object.assign({}, data.done, { phase: 'loaded' }));
        release();
        await sending;
        return adopted;
        """,
        done=finished("failed"),
    )

    assert got == [["refusal", "Too many requests. Wait a moment and try again."]]


@needs_node
def test_a_request_that_never_settles_is_given_up_on_and_try_again_is_usable_again():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const signals = [];
        const debrief = D.mount(doc, { timeoutMs: 20,
          fetch: (url, init) => { signals.push(!!init.signal); return new Promise(() => {}); } });
        debrief.result(200, data.done);
        const retry = button(panel, 'Try again');
        const sending = retry.listeners.click[0]();
        const during = retry.disabled;
        await sending;
        return { during, after: retry.disabled, notice: text(byClass(panel, 'debrief-notice')), signals };
        """,
        done=finished("failed"),
    )

    assert got["during"] is True
    assert got["after"] is False
    assert got["notice"].startswith("The request did not finish")
    assert got["signals"] == [True]


@needs_node
def test_a_notice_only_message_leaves_a_send_in_flight_and_focus_alone(client, served):
    client.post("/api/scenario/load", json={"scenario": KEY})

    got = run_js(
        PAGE
        + HTTP
        + """
        const { bar } = page('loaded');
        let ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        const elsewhere = add('input', 'faceplate-entry');
        const sending = doc.getElementById('scenario-start').listeners.click[0]();
        elsewhere.focus();
        ribbon.say('Too many requests. Wait a moment and try again.');
        const during = { notice: text(doc.getElementById('scenario-notice')), focus: focusedId() };
        await sending;
        return { during, state: text(doc.getElementById('scenario-state')), focus: focusedId() };
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert got["during"] == {"notice": "Too many requests. Wait a moment and try again.", "focus": "faceplate-entry"}
    assert got["state"] == f"Scenario {DOT} Running"
    assert got["focus"] == "faceplate-entry"


@needs_node
def test_a_try_again_that_accepted_nothing_asks_the_ribbon_to_read_the_run_again():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const asked = [];
        const debrief = D.mount(doc, { timeoutMs: 20, fetch: () => new Promise(() => {}), onResync: () => asked.push('poll') });
        debrief.result(200, data.done);
        await button(panel, 'Try again').listeners.click[0]();
        return asked;
        """,
        done=finished("failed"),
    )

    assert got == ["poll"]


@needs_node
def test_only_a_request_that_did_not_finish_asks_for_a_resync_not_an_accepted_or_refused_one():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const asked = [];
        const answers = { accepted: [reply(200, { phase: 'loaded' }), reply(200, { phase: 'running' })], refused: [reply(429, null)] };
        let mode = 'accepted';
        const debrief = D.mount(doc, { fetch: async () => answers[mode].shift(), onResync: () => asked.push(mode) });
        debrief.result(200, data.done);
        await button(panel, 'Try again').listeners.click[0]();
        mode = 'refused';
        await button(panel, 'Try again').listeners.click[0]();
        return asked;
        """,
        done=finished("failed"),
    )

    assert got == []


@needs_node
def test_a_panel_that_loses_its_run_hands_focus_to_the_control_the_ribbon_offers_for_the_new_phase(client, served):
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    client.post("/api/scenario/abort")

    got = run_js(
        PAGE
        + HTTP
        + """
        const { panel } = page('aborted');
        const debrief = D.mount(doc, { fetch: http });
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0, onResult: (s, b) => debrief.result(s, b) });
        await ribbon.ready;
        button(panel, 'Try again').focus();
        const held = !panel.hidden && panel.contains(doc.activeElement);
        // Another tab unloads the run and loads a fresh one.
        await http('/api/scenario/unload', { method: 'POST' });
        await http('/api/scenario/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario: data.key }) });
        await ribbon.poll();
        await settle();
        return { held, panel: !panel.hidden, focus: focusedId(), state: text(doc.getElementById('scenario-state')) };
        """,
        base=served,
        cookie=cookie_of(client),
        key=KEY,
    )

    assert got == {"held": True, "panel": False, "focus": "scenario-start", "state": f"Scenario {DOT} Loaded, not started"}


@needs_node
def test_a_start_that_did_not_finish_after_an_accepted_load_adopts_the_load_then_asks_for_a_resync():
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const order = [];
        const answers = [reply(200, { phase: 'loaded' }), null];
        const debrief = D.mount(doc, { timeoutMs: 20,
          fetch: () => { const next = answers.shift(); return next ? Promise.resolve(next) : new Promise(() => {}); },
          onRetry: (s, b) => order.push(['retry', b.phase]), onResync: () => order.push(['resync']) });
        debrief.result(200, data.done);
        await button(panel, 'Try again').listeners.click[0]();
        return order;
        """,
        done=finished("failed"),
    )

    assert got == [["retry", "loaded"], ["resync"]]


@needs_node
def test_a_resync_asked_for_during_a_send_is_read_when_the_send_ends(client, served):
    client.post("/api/scenario/load", json={"scenario": KEY})

    got = run_js(
        PAGE
        + HTTP
        + """
        page('loaded');
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        calls.length = 0;
        const sending = doc.getElementById('scenario-start').listeners.click[0]();
        const early = ribbon.resync();
        await Promise.all([sending, early]);
        return calls;
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert got == ["/api/scenario/start", "/api/scenario/result"]


@needs_node
@pytest.mark.parametrize(("status", "order"), [(503, ["retry", "resync"]), (429, ["retry"])])
def test_only_a_5xx_start_after_an_accepted_load_asks_for_a_resync_after_adopting_the_load(status, order):
    got = run_js(
        PAGE
        + """
        const { panel } = page('complete');
        const order = [];
        const answers = [reply(200, { phase: 'loaded' }), reply(data.status, null)];
        const debrief = D.mount(doc, { fetch: async () => answers.shift(),
          onRetry: () => order.push('retry'), onResync: () => order.push('resync') });
        debrief.result(200, data.done);
        await button(panel, 'Try again').listeners.click[0]();
        return order;
        """,
        done=finished("failed"),
        status=status,
    )

    assert got == order


@needs_node
def test_a_resync_asked_for_while_a_poll_is_in_flight_is_still_read_if_a_send_starts_meanwhile():
    got = run_js(
        PAGE
        + """
        page('loaded');
        const calls = [];
        let releasePoll = null;
        let releaseStart = null;
        let held = true;
        const fetch = url => {
          calls.push(url);
          if (url.endsWith('/result') && held) {
            held = false;
            return new Promise(resolve => { releasePoll = () => resolve(reply(200, { phase: 'loaded' })); });
          }
          if (url.endsWith('/start')) return new Promise(resolve => { releaseStart = () => resolve(reply(200, { phase: 'running' })); });
          return Promise.resolve(reply(200, { phase: 'running' }));
        };
        const ribbon = R.mount(doc, { fetch, pollMs: 0 });
        await settle();
        const resyncing = ribbon.resync();
        const sending = doc.getElementById('scenario-start').listeners.click[0]();
        releasePoll();
        await settle();
        releaseStart();
        await Promise.all([resyncing, sending]);
        return calls.filter(u => !u.includes('history'));
        """,
    )

    assert got == ["/api/scenario/result", "/api/scenario/start", "/api/scenario/result"]
