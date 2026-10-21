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

pytestmark = pytest.mark.usefixtures("olefins_free_play")

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
let current = null;
const mk = id => ({ id, hidden: false, disabled: false, textContent: "", innerHTML: "", attrs: {}, listeners: {},
  setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
  addEventListener(t, f) { this.listeners[t] = f; },
  focus() { focused = this.id; if (current) current.activeElement = this; } });
function page(phase, title, withButtons = true) {
  const ids = ["ribbon", "ribbon-alarms", "ribbon-alarm-summary", "run-clock", "run-clock-value", "run-clock-inline",
    "scenario-bar", "scenario-title", "scenario-state", "scenario-notice"];
  if (withButtons) ids.push("scenario-start", "scenario-abort", "scenario-confirm", "scenario-abort-confirm",
    "scenario-abort-keep", "scenario-choose", "scenario-choose-label", "ribbon-pause", "ribbon-resume");
  const els = Object.fromEntries(ids.map(id => [id, mk(id)]));
  els["scenario-bar"].attrs = { "data-phase": phase, "data-title": title,
    "data-phase-labels": JSON.stringify({ idle: "", loaded: "Loaded, not started", running: "Running", complete: "Finished", aborted: "Aborted" }) };
  // The run controls hold everything but the ribbon's own frame and alarms.
  els["scenario-bar"].contains = el => !!el && el.id.startsWith("scenario-");
  const keys = {};
  const doc = { getElementById: id => els[id] || null, body: mk("body"), activeElement: null,
    addEventListener(t, f) { keys[t] = f; }, removeEventListener(t) { delete keys[t]; } };
  current = doc;
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
def test_focus_moves_to_the_control_that_replaces_the_one_it_was_on():
    got = run_js(
        STUB
        + """
        const { els, doc, focusOn } = page("loaded", "T");
        let next = "running";
        R.mount(doc, { fetch: async () => reply(200, { phase: next }), pollMs: 0 });
        focusOn("scenario-start");
        await els["scenario-start"].listeners.click();
        const started = focused;
        els["scenario-abort"].listeners.click();
        next = "aborted";
        await els["scenario-abort-confirm"].listeners.click();
        return { started, aborted: focused };
        """,
    )

    assert got == {"started": "scenario-abort", "aborted": "scenario-choose"}


@needs_node
def test_focus_moved_away_while_a_start_is_in_flight_stays_where_it_went():
    got = run_js(
        STUB
        + """
        const { els, doc, focusOn } = page("loaded", "T");
        let release;
        const held = new Promise(r => (release = r));
        R.mount(doc, { fetch: async url => { if (url.endsWith("start")) await held; return reply(200, { phase: "running" }); }, pollMs: 0 });
        focusOn("scenario-start");
        const click = els["scenario-start"].listeners.click();
        focusOn("ribbon-alarms");
        release();
        await click;
        return doc.activeElement.id;
        """,
    )

    assert got == "ribbon-alarms"


@needs_node
def test_a_poll_that_ends_the_run_moves_focus_only_if_it_was_in_the_bar():
    got = run_js(
        STUB
        + """
        async function endRun(focusFrom) {
          let next = reply(200, { phase: "running" });
          const { els, doc, focusOn } = page("running", "T");
          const ribbon = R.mount(doc, { fetch: async () => next, pollMs: 0 });
          await ribbon.ready;
          if (focusFrom === "keep") els["scenario-abort"].listeners.click();
          else focusOn(focusFrom);
          next = reply(200, { phase: "complete" });
          await ribbon.poll();
          return doc.activeElement.id;
        }
        return { outside: await endRun("ribbon-alarms"), inside: await endRun("keep") };
        """,
    )

    assert got == {"outside": "ribbon-alarms", "inside": "scenario-choose"}


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


# ---- pause and resume (T20-14) ----------------------------------------------


def test_pause_and_resume_are_on_the_console_only(client):
    landing = client.get("/").get_data(as_text=True)
    console = client.get("/console").get_data(as_text=True)

    assert 'id="ribbon-pause"' not in landing and 'id="ribbon-resume"' not in landing
    assert 'id="ribbon-pause"' in console and 'id="ribbon-resume"' in console


@needs_node
@pytest.mark.parametrize(
    ("phase", "running", "offered"),
    [
        ("idle", True, "pause"),
        ("idle", False, "resume"),
        ("running", True, "pause"),
        ("running", False, "resume"),
        ("idle", None, "none"),
        ("loaded", True, "none"),
        ("loaded", False, "none"),
        ("complete", True, "none"),
        ("aborted", False, "none"),
    ],
)
def test_pause_or_resume_is_offered_by_phase_and_the_snapshots_running_field(phase, running, offered):
    got = run_js("const c = R.pauseControls(data.phase, data.running); return c.pause ? 'pause' : c.resume ? 'resume' : 'none';", phase=phase, running=running)

    assert got == offered


@needs_node
def test_the_ribbon_pauses_and_resumes_the_live_plant_following_the_snapshot_over_http(client, served):
    client.get("/console")

    got = run_js(
        STUB
        + HTTP
        + """
        const { els, doc } = page("idle", "");
        const ribbon = R.mount(doc, { fetch: http, pollMs: 0 });
        await ribbon.ready;
        const before = { pause: els["ribbon-pause"].hidden, resume: els["ribbon-resume"].hidden };
        ribbon.update({ sim_time: 1, running: true });
        const running = { pause: els["ribbon-pause"].hidden, resume: els["ribbon-resume"].hidden };
        await els["ribbon-pause"].listeners.click();
        const paused = { pause: els["ribbon-pause"].hidden, resume: els["ribbon-resume"].hidden };
        ribbon.update({ sim_time: 2, running: false });
        await els["ribbon-resume"].listeners.click();
        const resumed = { pause: els["ribbon-pause"].hidden, resume: els["ribbon-resume"].hidden };
        return { before, running, paused, resumed, sent: calls.filter(u => u.includes("simulation")) };
        """,
        base=served,
        cookie=cookie_of(client),
    )

    assert got["before"] == {"pause": True, "resume": True}
    assert got["running"] == {"pause": False, "resume": True}
    assert got["paused"] == {"pause": True, "resume": False}
    assert got["resumed"] == {"pause": False, "resume": True}
    assert got["sent"] == ["/api/simulation/pause", "/api/simulation/resume"]


@needs_node
def test_a_paused_plant_says_so_in_the_ribbon_and_a_resumed_one_goes_back():
    got = run_js(
        STUB
        + """
        const out = {};
        for (const phase of ["idle", "running"]) {
          const { els, doc } = page(phase, "Falling vessel level");
          const fetchImpl = async url => url.includes("simulation")
            ? reply(200, { running: true })
            : url.endsWith("result") ? (phase === "idle" ? reply(409, null)
              : reply(200, { phase, title: "Falling vessel level", elapsed_s: 1, time_limit_s: 9 })) : reply(200, []);
          const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
          await ribbon.ready;
          ribbon.update({ sim_time: 1, running: true });
          const live = els["scenario-state"].textContent;
          ribbon.update({ sim_time: 2, running: false });
          const frozen = { text: els["scenario-state"].textContent, paused: els["scenario-bar"].attrs["data-paused"] };
          await els["ribbon-resume"].listeners.click();
          out[phase] = { live, frozen, after: els["scenario-state"].textContent, paused: els["scenario-bar"].attrs["data-paused"] };
        }
        return out;
        """,
    )

    assert got["idle"] == {
        "live": "Plant running",
        "frozen": {"text": "Plant paused", "paused": "true"},
        "after": "Plant running",
        "paused": "false",
    }
    assert got["running"] == {
        "live": f"Scenario {DOT} Running",
        "frozen": {"text": f"Scenario {DOT} Paused", "paused": "true"},
        "after": f"Scenario {DOT} Running",
        "paused": "false",
    }


@needs_node
@pytest.mark.parametrize("reply_with", ["reply(429, null)", "reply(500, null)", "reply(400, null)", "reply(200, {})", "reject"])
def test_every_other_pause_failure_is_worded_and_leaves_the_button_usable(reply_with):
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        const failing = data.reply_with === "reject"
          ? async () => { throw new Error("network"); }
          : async () => eval(data.reply_with);
        const fetchImpl = async (url, init) => url.includes("simulation") ? failing() : reply(409, null);
        const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await ribbon.ready;
        ribbon.update({ sim_time: 1, running: true });
        await els["ribbon-pause"].listeners.click();
        return { notice: els["scenario-notice"].textContent, shown: !els["scenario-notice"].hidden,
          enabled: !els["ribbon-pause"].disabled, offered: !els["ribbon-pause"].hidden };
        """,
        reply_with=reply_with,
    )

    assert got["notice"] != "" and got["shown"] is True
    assert got["enabled"] is True and got["offered"] is True


@needs_node
def test_a_successful_pause_clears_the_refusal_it_wrote_earlier():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        let calls = 0;
        const fetchImpl = async url => url.includes("simulation")
          ? (++calls === 1 ? reply(409, null) : reply(200, { running: false })) : reply(409, null);
        const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await ribbon.ready;
        ribbon.update({ sim_time: 1, running: true });
        await els["ribbon-pause"].listeners.click();
        const refused = !els["scenario-notice"].hidden;
        await els["ribbon-pause"].listeners.click();
        return { refused, notice: els["scenario-notice"].textContent, shown: !els["scenario-notice"].hidden };
        """,
    )

    assert got == {"refused": True, "notice": "", "shown": False}


@needs_node
def test_a_successful_pause_leaves_a_notice_it_did_not_write():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        const fetchImpl = async url => url.includes("simulation") ? reply(200, { running: false }) : reply(409, null);
        const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await ribbon.ready;
        ribbon.update({ sim_time: 1, running: true });
        els["scenario-notice"].textContent = "A start refusal";
        els["scenario-notice"].hidden = false;
        await els["ribbon-pause"].listeners.click();
        return { notice: els["scenario-notice"].textContent, shown: !els["scenario-notice"].hidden };
        """,
    )

    assert got == {"notice": "A start refusal", "shown": True}


@needs_node
def test_a_refused_pause_says_so_in_the_pages_own_words():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("running", "Falling vessel level");
        const fetchImpl = async url => url.includes("simulation")
          ? reply(409, { error: "cannot pause or resume a scenario that is complete: SECRET" })
          : url.endsWith("result") ? reply(200, { phase: "running", title: "t", elapsed_s: 1, time_limit_s: 9 }) : reply(200, []);
        const ribbon = R.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await ribbon.ready;
        ribbon.update({ sim_time: 1, running: true });
        await els["ribbon-pause"].listeners.click();
        return { notice: els["scenario-notice"].textContent, shown: !els["scenario-notice"].hidden,
          pause: els["ribbon-pause"].hidden };
        """,
    )

    assert "SECRET" not in got["notice"]
    assert "nothing to pause" in got["notice"]
    assert got["shown"] is True
    assert got["pause"] is False


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
def test_while_running_the_clock_reads_the_latest_snapshot_from_the_runs_start_and_stops_at_the_limit():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(1000);
        clock.result("running", { elapsed: 10, limit: 30, startedAt: 990 });
        const seen = [clock.read()];
        clock.snapshot(1005); seen.push(clock.read());
        clock.snapshot(1100); seen.push(clock.read());
        return seen;
        """,
    )

    assert got == pytest.approx([10, 15, 30])


@needs_node
def test_a_poll_answering_after_a_snapshot_never_carries_the_clock_past_the_plant():
    # The poll measured elapsed_s at plant time 1004 and answered while the
    # page's last snapshot was 1003; the snapshot at 1004 is the same moment.
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.result("running", { elapsed: 12, limit: 900, startedAt: 990 });
        clock.snapshot(1003);
        const before = clock.read();
        clock.result("running", { elapsed: 14, limit: 900, startedAt: 990 });
        const polled = clock.read();
        clock.snapshot(1004);
        const same = clock.read();
        clock.snapshot(1005);
        return { before, polled, same, after: clock.read() };
        """,
    )

    assert got == pytest.approx({"before": 13, "polled": 14, "same": 14, "after": 15})


@needs_node
def test_a_poll_that_lands_behind_the_last_snapshot_neither_rewinds_the_clock_nor_moves_it_on():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.result("running", { elapsed: 10, limit: 900, startedAt: 990 });
        clock.snapshot(1003);
        const before = clock.read();
        clock.result("running", { elapsed: 12, limit: 900, startedAt: 990 });
        const held = clock.read();
        clock.snapshot(1005);
        return { before, held, after: clock.read() };
        """,
    )

    assert got == pytest.approx({"before": 13, "held": 13, "after": 15})


@needs_node
def test_before_any_snapshot_after_a_result_the_clock_shows_that_results_elapsed_time():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(400);
        clock.result("running", { elapsed: 7, limit: 900, startedAt: 990 });
        const first = clock.read();
        const noOrigin = R.createRunClock();
        noOrigin.result("running", { elapsed: 7, limit: 900, startedAt: null });
        noOrigin.snapshot(2000);
        return { first, noOrigin: noOrigin.read() };
        """,
    )

    assert got == pytest.approx({"first": 7, "noOrigin": 7})


@needs_node
def test_outside_a_running_run_the_clock_shows_exactly_what_the_server_says():
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.snapshot(1000);
        clock.result("running", { elapsed: 40, limit: 900, startedAt: 960 });
        clock.snapshot(1010);
        const running = clock.read();
        clock.result("aborted", { elapsed: 45, limit: 900, startedAt: 960 });
        clock.snapshot(1020);
        const aborted = clock.read();
        clock.result("loaded", { elapsed: 0, limit: 600, startedAt: 500 });
        clock.snapshot(1030);
        return { running, aborted, loaded: clock.read(), text: R.clockText(clock.read(), clock.times()),
          free: R.clockText(null, null) };
        """,
    )

    assert got == {"running": 50, "aborted": 45, "loaded": 0, "text": "00:00 / 10:00", "free": ""}


@needs_node
def test_a_new_run_between_two_polls_restarts_the_clock_rather_than_holding_the_old_one():
    # Try again on the same scenario starts from the same saved plant clock,
    # so the new run's started_at can equal the old one's.
    got = run_js(
        """
        const clock = R.createRunClock();
        clock.result("running", { elapsed: 300, limit: 900, startedAt: 500 });
        clock.snapshot(800);
        const old = clock.read();
        clock.result("running", { elapsed: 4, limit: 900, startedAt: 500 });
        const restarted = clock.read();
        clock.snapshot(505);
        const following = clock.read();
        clock.result("running", { elapsed: 5, limit: 600, startedAt: 2000 });
        const other = clock.read();
        clock.snapshot(1990);
        return { old, restarted, following, other, early: clock.read() };
        """,
    )

    assert got == pytest.approx({"old": 300, "restarted": 4, "following": 5, "other": 5, "early": 5})


@needs_node
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            200,
            {"phase": "running", "elapsed_s": 12.5, "time_limit_s": 900, "started_at": 500.0},
            {"elapsed": 12.5, "limit": 900, "startedAt": 500.0},
        ),
        (200, {"phase": "running", "elapsed_s": 12.5, "time_limit_s": 900}, {"elapsed": 12.5, "limit": 900, "startedAt": None}),
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
    assert got["label"] == "1 CRIT, 2 HIGH, 1 LOW alarms, 3 not acknowledged. Go to the console."


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


def test_the_alarm_summary_opens_the_overlay_on_the_console_and_links_to_it_elsewhere(client):
    console = client.get("/console").get_data(as_text=True)
    landing = client.get("/").get_data(as_text=True)

    assert re.search(r'<button[^>]*id="ribbon-alarms"[^>]*aria-expanded="false"', console)
    assert 'Open the alarm list.' in console
    assert 'id="ribbon-alarms" href="/console"' in landing
    assert "Go to the console." in landing


@needs_node
def test_the_alarm_summarys_spoken_label_says_what_pressing_it_does():
    got = run_js(
        """
        const summary = R.summarize([{ priority: "high", state: "unack" }]);
        return [R.summaryView(summary).label, R.summaryView(summary, true).label,
          R.summaryView(R.summarize([]), true).label];
        """
    )

    assert got == [
        "1 HIGH alarms, 1 not acknowledged. Go to the console.",
        "1 HIGH alarms, 1 not acknowledged. Open the alarm list.",
        "No active alarms. Open the alarm list.",
    ]


def test_the_ribbon_stylesheet_uses_tokens_and_no_colour_literals():
    css = CONSOLE_CSS.read_text()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)


def test_the_ribbon_draws_a_glyph_beside_every_priority_colour():
    css = CONSOLE_CSS.read_text()

    for priority in ("critical", "high", "low"):
        block = re.search(rf'\.sev-badge\[data-priority="{priority}"\]::before\s*\{{([^}}]*)\}}', css)
        assert block, priority
        assert re.search(rf"content:\s*var\(--symbol-alarm-{priority}\)", block.group(1)), priority
