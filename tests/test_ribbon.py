"""The ribbon - T20-8: templates/base.html, static/js/ribbon.js and the run
controls it hands to static/js/scenario.js.

The pure half of ribbon.js runs under Node. The mounted ribbon runs under Node
too, against a stand-in page and, where a test says "over HTTP", against the
real app served on a local port: the ribbon's own requests, with the session's
cookie, are what move the run, and the test reads the session back in Python.
"""

import json
import re
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.serving import make_server

from app import main
from app.alarms.acknowledge import acknowledge_alarm
from app.alarms.history import AlarmHistory
from app.alarms.manager import AlarmManager, EnvelopeEvent, _alarm_id
from app.api.alarms import create_alarm_blueprint
from app.envelope.evaluator import Severity
from app.scenarios.runner import scenario_key

ROOT = Path(__file__).resolve().parent.parent
RIBBON_JS = ROOT / "static" / "js" / "ribbon.js"
CONSOLE_CSS = ROOT / "static" / "css" / "console.css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

KEY = scenario_key("pump_trip")
DOT = "·"


def run_js(body, **data):
    script = (
        f"const R = require({json.dumps(str(RIBBON_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


# A stand-in for the page: every element the ribbon and its run controls
# touch, a document that takes key listeners, and focus that is recorded.
STUB = """
let focused = null;
const mk = id => ({ id, hidden: false, disabled: false, textContent: "", innerHTML: "", attrs: {}, listeners: {},
  setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
  addEventListener(t, f) { this.listeners[t] = f; }, focus() { focused = this.id; } });
function page(phase, title, withButtons = true) {
  const ids = ["ribbon", "ribbon-alarms", "ribbon-alarm-summary", "run-clock", "run-clock-value", "run-clock-inline",
    "scenario-bar", "scenario-title", "scenario-state", "scenario-notice"];
  if (withButtons) ids.push("scenario-start", "scenario-abort", "scenario-confirm", "scenario-abort-confirm",
    "scenario-abort-keep", "scenario-choose", "scenario-choose-label");
  const els = Object.fromEntries(ids.map(id => [id, mk(id)]));
  els["scenario-bar"].attrs = { "data-phase": phase, "data-title": title,
    "data-phase-labels": JSON.stringify({ idle: "", loaded: "Loaded, not started", running: "Running", complete: "Finished", aborted: "Aborted" }) };
  // The run controls hold everything but the ribbon's own frame and alarms.
  els["scenario-bar"].contains = el => !!el && el.id.startsWith("scenario-");
  const keys = {};
  const doc = { getElementById: id => els[id] || null, body: mk("body"), activeElement: null,
    addEventListener(t, f) { keys[t] = f; }, removeEventListener(t) { delete keys[t]; } };
  return { els, doc, keys, focusOn: id => { doc.activeElement = els[id] || doc.body; } };
}
const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
"""

# Talks to the served app as the test's session: relative URLs, the cookie,
# and a record of every request in order.
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


def session_of(client):
    return main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)


def cookie_of(client):
    return f"{main.SESSION_COOKIE}={client.get_cookie(main.SESSION_COOKIE).value}"


# ---- run controls -------------------------------------------------------


@needs_node
def test_the_ribbons_run_controls_take_a_loaded_scenario_to_running_then_aborted_over_http(client, served):
    client.post("/api/scenario/load", json={"scenario": KEY})

    got = run_js(
        STUB
        + HTTP
        + """
        const { els, doc } = page("loaded", "Falling vessel level");
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        await els["scenario-start"].listeners.click();
        const running = els["scenario-state"].textContent;
        els["scenario-abort"].listeners.click();
        const armed = { calls: calls.filter(u => u.endsWith("abort")).length,
          confirm: !els["scenario-confirm"].hidden, abort: !els["scenario-abort"].hidden };
        await els["scenario-abort-confirm"].listeners.click();
        return { running, armed, aborted: els["scenario-state"].textContent,
          sent: calls.filter(u => u.endsWith("start") || u.endsWith("abort")) };
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert got["running"] == f"Scenario {DOT} Running"
    assert got["armed"] == {"calls": 0, "confirm": True, "abort": False}
    assert got["aborted"] == f"Scenario {DOT} Aborted"
    assert got["sent"] == ["/api/scenario/start", "/api/scenario/abort"]
    assert client.get("/api/scenario/result").get_json()["phase"] == "aborted"


@needs_node
def test_escape_disarms_abort_sends_nothing_and_hands_focus_back():
    got = run_js(
        STUB
        + """
        const { els, doc, keys } = page("running", "T");
        const calls = [];
        R.mount(doc, { fetch: async url => { calls.push(url); return reply(200, { phase: "running" }); }, pollMs: 0 });
        calls.length = 0;
        els["scenario-abort"].listeners.click();
        const armedFocus = focused;
        keys.keydown({ key: "Enter" });
        const stillArmed = !els["scenario-confirm"].hidden;
        keys.keydown({ key: "Escape" });
        return { armedFocus, stillArmed, confirm: !els["scenario-confirm"].hidden,
          abort: !els["scenario-abort"].hidden, focus: focused, calls };
        """,
    )

    assert got == {
        "armedFocus": "scenario-abort-keep",
        "stillArmed": True,
        "confirm": False,
        "abort": True,
        "focus": "scenario-abort",
        "calls": [],
    }


@needs_node
def test_escape_elsewhere_on_the_page_leaves_abort_armed():
    got = run_js(
        STUB
        + """
        const { els, doc, keys, focusOn } = page("running", "T");
        R.mount(doc, { fetch: async () => reply(200, { phase: "running" }), pollMs: 0 });
        els["scenario-abort"].listeners.click();
        focusOn("ribbon-alarms");
        keys.keydown({ key: "Escape" });
        const elsewhere = !els["scenario-confirm"].hidden;
        focusOn("scenario-abort-keep");
        keys.keydown({ key: "Escape" });
        return { elsewhere, inside: !els["scenario-confirm"].hidden };
        """,
    )

    assert got == {"elsewhere": True, "inside": False}


@needs_node
def test_the_landing_page_shows_the_run_without_start_or_abort():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("running", "Falling vessel level", false);
        const fetchImpl = async url => url.endsWith("result")
          ? reply(200, { phase: "running", title: "Falling vessel level", elapsed_s: 65, time_limit_s: 900 })
          : reply(200, []);
        const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await ribbon.ready;
        return { title: els["scenario-title"].textContent, state: els["scenario-state"].textContent,
          clock: els["run-clock-value"].textContent };
        """,
    )

    assert got == {"title": "Falling vessel level", "state": f"Scenario {DOT} Running", "clock": "01:05 / 15:00"}


def test_start_and_abort_are_on_the_console_only(client):
    landing = client.get("/").get_data(as_text=True)
    console = client.get("/console").get_data(as_text=True)

    for page in (landing, console):
        assert 'id="ribbon"' in page and 'id="scenario-bar"' in page and 'id="ribbon-alarms"' in page
    assert 'id="scenario-start"' not in landing and 'id="scenario-abort"' not in landing
    assert 'id="scenario-start"' in console and 'id="scenario-abort-confirm"' in console


# ---- run clock ------------------------------------------------------------


@needs_node
def test_free_play_shows_plant_running_and_no_run_clock():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        const ribbon = R.mount(doc, { fetch: async url => url.endsWith("result") ? reply(409, {}) : reply(200, []), pollMs: 0 });
        await ribbon.ready;
        ribbon.update({ sim_time: 120 });
        return { title: els["scenario-title"].textContent, state: els["scenario-state"].textContent,
          clock: els["run-clock-value"].textContent, hidden: els["run-clock"].hidden,
          inline: els["run-clock-inline"].textContent };
        """,
    )

    assert got == {"title": "Free play", "state": "Plant running", "clock": "", "hidden": True, "inline": ""}


def test_free_play_renders_plant_running_before_any_script(client):
    page = client.get("/console").get_data(as_text=True)

    assert '<span id="scenario-state">Plant running</span>' in page
    assert 'data-plant-running="true"' in page


def test_the_landing_page_says_the_plant_has_not_started_until_the_console_starts_it(client):
    fresh = client.get("/").get_data(as_text=True)
    client.get("/console")
    started = client.get("/").get_data(as_text=True)

    assert '<span id="scenario-state">Plant not started</span>' in fresh
    assert 'data-plant-running="false"' in fresh
    assert '<span id="scenario-state">Plant running</span>' in started


@needs_node
def test_free_play_follows_the_pages_word_on_whether_the_plant_is_running():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "", false);
        els["scenario-bar"].attrs["data-plant-running"] = "false";
        R.mount(doc, { fetch: async url => url.endsWith("result") ? reply(409, {}) : reply(200, []), pollMs: 0 });
        return els["scenario-state"].textContent;
        """,
    )

    assert got == "Plant not started"


@needs_node
def test_the_run_clock_shows_the_runs_elapsed_scenario_time_and_its_limit_over_http(client, served):
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    scheduler = session_of(client).training_scheduler
    for _ in range(75):
        scheduler.step_once()
    result = client.get("/api/scenario/result").get_json()

    got = run_js(
        STUB
        + HTTP
        + """
        const { els, doc } = page("running", "Falling vessel level", false);
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        return { clock: els["run-clock-value"].textContent, inline: els["run-clock-inline"].textContent,
          expected: R.formatClock(data.elapsed) + " / " + R.formatClock(data.limit) };
        """,
        base=served,
        cookie=cookie_of(client),
        elapsed=result["elapsed_s"],
        limit=result["time_limit_s"],
    )

    assert result["elapsed_s"] > 0
    assert got["clock"] == got["expected"]
    assert got["inline"] == f" {DOT} " + got["expected"]


@needs_node
@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "00:00"), (59.9, "00:59"), (61, "01:01"), (900, "15:00"), (3725, "62:05"), (-3, "00:00")],
)
def test_the_clock_reads_whole_minutes_and_seconds(seconds, text):
    assert run_js("return R.formatClock(data.s);", s=seconds) == text


@needs_node
def test_between_polls_the_clock_follows_the_plants_sim_time_and_stops_at_the_limit():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(1000);
        clock.result("running", { elapsed: 10, limit: 30 });
        const seen = [clock.read()];
        clock.snapshot(1005); seen.push(clock.read());
        clock.snapshot(1100); seen.push(clock.read());
        return seen;
        """,
    )

    assert got == pytest.approx([10, 15, 30])


@needs_node
def test_a_poll_that_lands_behind_the_last_snapshot_holds_the_clock_rather_than_rewinding_it():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(1000);
        clock.result("running", { elapsed: 10, limit: 900 });
        clock.snapshot(1003);
        const before = clock.read();
        clock.result("running", { elapsed: 12, limit: 900 });
        const held = clock.read();
        clock.snapshot(1005);
        return { before, held, after: clock.read() };
        """,
    )

    assert got == pytest.approx({"before": 13, "held": 13, "after": 14})


@needs_node
def test_outside_a_running_run_the_clock_shows_exactly_what_the_server_says():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(1000);
        clock.result("running", { elapsed: 40, limit: 900 });
        clock.snapshot(1010);
        const running = clock.read();
        clock.result("aborted", { elapsed: 45, limit: 900 });
        clock.snapshot(1020);
        const aborted = clock.read();
        clock.result("loaded", { elapsed: 0, limit: 600 });
        clock.snapshot(1030);
        return { running, aborted, loaded: clock.read(), text: R.clockText(clock.read(), clock.times()),
          free: R.clockText(null, null) };
        """,
    )

    assert got == {"running": 50, "aborted": 45, "loaded": 0, "text": "00:00 / 10:00", "free": ""}


@needs_node
def test_a_new_run_between_two_polls_restarts_the_clock_rather_than_holding_the_old_one():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(1000);
        clock.result("running", { elapsed: 300, limit: 900 });
        const old = clock.read();
        clock.result("running", { elapsed: 4, limit: 900 });
        const restarted = clock.read();
        clock.result("running", { elapsed: 5, limit: 600 });
        return { old, restarted, other: clock.read() };
        """,
    )

    assert got == pytest.approx({"old": 300, "restarted": 4, "other": 5})


@needs_node
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"phase": "running", "elapsed_s": 12.5, "time_limit_s": 900}, {"elapsed": 12.5, "limit": 900}),
        (409, None, None),
        (500, None, None),
        (200, {"phase": "running"}, None),
        (200, {"phase": "running", "elapsed_s": "12", "time_limit_s": 900}, None),
    ],
)
def test_run_times_come_only_from_a_result_that_carries_both(status, body, expected):
    assert run_js("return R.runTimes(data.status, data.reply);", status=status, reply=body) == expected


# ---- alarm summary -------------------------------------------------------


class SeededAlarms:
    """A real manager, history and alarm blueprint, seeded by the test."""

    def __init__(self):
        self.manager = AlarmManager()
        self.history = AlarmHistory(capacity=100)
        self.sim_time = 0.0
        app = Flask(__name__)
        app.register_blueprint(
            create_alarm_blueprint(
                self.history.entries,
                lambda alarm_id: acknowledge_alarm(self.manager, self.history, alarm_id, self.sim_time),
            )
        )
        self.client = app.test_client()

    def band(self, tag, pv, severity, at):
        self.sim_time = at
        point = EnvelopeEvent(tag=tag, pv=pv, severity=severity, side="hi")
        self.history.record_events(self.manager.evaluate([point], at))
        self.history.record_clears(self.manager.last_cleared(), at)
        return _alarm_id(tag, pv)

    def acknowledge(self, alarm_id):
        assert self.client.post("/api/alarms/acknowledge", json={"alarm_id": alarm_id}).status_code == 200

    def entries(self):
        response = self.client.get("/api/alarms/history")
        assert response.status_code == 200
        return response.get_json()


@needs_node
def test_the_summary_counts_a_seeded_history_by_priority_and_acknowledgement():
    alarms = SeededAlarms()
    alarms.band("K-101", "discharge pressure", Severity.TRIP, at=1.0)
    alarms.acknowledge(alarms.band("V-101", "level", Severity.ALARM, at=2.0))
    alarms.band("P-101", "flow", Severity.ALARM, at=3.0)
    alarms.band("V-101", "pressure", Severity.WARNING, at=4.0)
    alarms.band("V-101", "pressure", Severity.NORMAL, at=5.0)
    gone = alarms.band("K-101", "temperature", Severity.WARNING, at=6.0)
    alarms.acknowledge(gone)
    alarms.band("K-101", "temperature", Severity.NORMAL, at=7.0)

    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        const fetchImpl = async url => url.endsWith("history") ? reply(200, data.entries) : reply(409, {});
        const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await ribbon.ready;
        const summary = R.summarize(require(data.alarms).deriveAlarms(data.entries));
        return { summary, severity: els["ribbon"].attrs["data-severity"], unack: els["ribbon"].attrs["data-unack"],
          html: els["ribbon-alarm-summary"].innerHTML, label: els["ribbon-alarms"].attrs["aria-label"] };
        """,
        entries=alarms.entries(),
        alarms=str(ROOT / "static" / "js" / "alarms.js"),
    )

    # Critical K-101 unacknowledged; high V-101 level acknowledged and high
    # P-101 flow not; low V-101 pressure returned unseen; K-101 temperature
    # acknowledged and returned, so off the console.
    assert got["summary"] == {
        "counts": {"critical": 1, "high": 2, "low": 1},
        "unacknowledged": 3,
        "worst": "critical",
        "total": 4,
    }
    assert got["severity"] == "critical"
    assert got["unack"] == "true"
    assert re.findall(r'data-priority="(\w+)">(\d+) (\w+)<', got["html"]) == [
        ("critical", "1", "CRIT"),
        ("high", "2", "HIGH"),
        ("low", "1", "LOW"),
    ]
    assert "3 new" in got["html"]
    assert got["label"] == "1 CRIT, 2 HIGH, 1 LOW alarms, 3 not acknowledged. Go to the alarm list."


@needs_node
@pytest.mark.parametrize(
    ("states", "worst", "unack", "text"),
    [
        ([], "none", "false", "No active alarms"),
        ([("low", "acked")], "low", "false", "All acknowledged"),
        ([("low", "acked"), ("high", "rtn_unack")], "high", "true", "1 new"),
    ],
)
def test_the_summary_takes_the_worst_priority_and_says_when_all_are_seen(states, worst, unack, text):
    got = run_js(
        """
        const alarms = data.states.map(([priority, state], i) => ({ id: String(i), priority, state }));
        const summary = R.summarize(alarms);
        return { worst: summary.worst, unack: summary.unacknowledged > 0 ? "true" : "false",
          html: R.summaryView(summary).html };
        """,
        states=states,
    )

    assert got["worst"] == worst
    assert got["unack"] == unack
    assert text in got["html"]


@needs_node
def test_the_ribbon_on_the_live_plant_takes_the_worst_priority_in_its_history_over_http(client, served):
    client.get("/console")
    scheduler = session_of(client).training_scheduler
    scheduler.stop()
    assert client.post("/api/action", json={"target": "P-101", "action": "stop", "value": None}).status_code == 200
    for _ in range(600):
        scheduler.step_once()
    entries = client.get("/api/alarms/history").get_json()
    raised = {entry["priority"] for entry in entries if entry["type"] == "alarm"}

    got = run_js(
        STUB
        + HTTP
        + """
        const { els, doc } = page("idle", "");
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        return els["ribbon"].attrs["data-severity"];
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert raised
    assert got == next(p for p in ("critical", "high", "low") if p in raised)


@needs_node
def test_a_failed_alarm_read_keeps_the_summary_it_had():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        let next = reply(200, [{ type: "alarm", id: "a", tag: "K-101", priority: "low", message: "m", sim_time: 1 }]);
        const ribbon = R.mount(doc, { fetch: async url => url.endsWith("history") ? next : reply(409, {}), pollMs: 0 });
        await ribbon.ready;
        next = reply(500, null);
        await ribbon.refresh();
        const failed = els["ribbon"].attrs["data-severity"];
        next = Promise.reject(new Error("down"));
        await ribbon.refresh();
        return { failed, thrown: els["ribbon"].attrs["data-severity"] };
        """,
    )

    assert got == {"failed": "low", "thrown": "low"}


@needs_node
def test_an_acknowledgement_in_the_alarm_list_tells_the_page_at_once():
    got = run_js(
        """
        const A = require(data.alarms);
        const mk = () => ({ innerHTML: "", listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } });
        const banner = mk(), summary = mk();
        const reply = (status, body) => ({ ok: status === 200, status, json: async () => body });
        let told = 0;
        const fetchImpl = async url => url.endsWith("acknowledge") ? reply(200, { recorded: true }) : reply(200, []);
        const console_ = A.mount(banner, summary, { fetch: fetchImpl, pollMs: 0, onAcknowledge: () => { told += 1; } });
        await console_.ready;
        const button = { getAttribute: () => "a" };
        await summary.listeners.click({ target: { closest: sel => sel === "[data-alarm-id]" ? button : null } });
        return told;
        """,
        alarms=str(ROOT / "static" / "js" / "alarms.js"),
    )

    assert got == 1


def test_the_console_refreshes_the_ribbon_after_an_acknowledgement(client):
    page = client.get("/console").get_data(as_text=True)

    assert "onAcknowledge: function () { if (ribbon) ribbon.refresh(); }" in page


# ---- pages ----------------------------------------------------------------


@needs_node
def test_the_landing_page_and_the_ribbons_polls_never_start_the_scheduler(client, served):
    assert client.get("/").status_code == 200

    run_js(
        STUB
        + HTTP
        + """
        const { doc } = page("idle", "", false);
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert session_of(client).training_scheduler.running is False


class Interactive(HTMLParser):
    """Every element inside the ribbon a keyboard user might need."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.found = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "header" and attrs.get("id") == "ribbon":
            self.depth = 1
            return
        if not self.depth:
            return
        if tag == "header":
            self.depth += 1
        if tag in ("a", "button") or "tabindex" in attrs or "onclick" in attrs:
            self.found.append((tag, attrs))

    def handle_endtag(self, tag):
        if self.depth and tag == "header":
            self.depth -= 1


@pytest.mark.parametrize("path", ["/", "/console"])
def test_every_ribbon_control_is_a_native_link_or_button_in_the_tab_order(client, path):
    parser = Interactive()
    parser.feed(client.get(path).get_data(as_text=True))

    names = {attrs.get("id") or attrs.get("href") for _, attrs in parser.found}
    assert {"theme-toggle", "ribbon-alarms"} <= names
    if path == "/console":
        assert {"scenario-start", "scenario-abort", "scenario-abort-confirm", "scenario-abort-keep", "scenario-choose"} <= names
    for tag, attrs in parser.found:
        assert tag in ("a", "button"), attrs
        assert attrs.get("tabindex") != "-1", attrs
        assert tag == "button" or attrs.get("href"), attrs
        assert tag == "a" or attrs.get("type") == "button", attrs


def test_the_alarm_summary_links_to_the_consoles_alarm_section_until_the_overlay(client):
    page = client.get("/console").get_data(as_text=True)

    assert 'id="ribbon-alarms" href="/console#alarms-heading"' in page
    assert 'id="alarms-heading"' in page


def test_the_ribbon_stylesheet_uses_tokens_and_no_colour_literals():
    css = CONSOLE_CSS.read_text()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)


def test_the_ribbon_draws_a_glyph_beside_every_priority_colour():
    css = CONSOLE_CSS.read_text()

    for priority in ("critical", "high", "low"):
        assert f'.sev-badge[data-priority="{priority}"]::before {{\n  content: var(--symbol-alarm-{priority});' in css
