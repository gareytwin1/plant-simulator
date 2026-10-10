"""Scenario run controls - T16-15: the console's Scenario bar (templates/console.html,
static/js/scenario.js) and the calls it makes.

Scenario state is per browser session (the plant_session_id cookie), so every
replay here uses one Flask client for load, start and abort.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app import main
from app.scenarios.runner import scenario_key

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = sorted((ROOT / "config" / "scenarios").glob("*.yaml"))
SCENARIO_JS = ROOT / "static" / "js" / "scenario.js"
SCENARIO_CSS = ROOT / "static" / "css" / "scenario.css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

KEY = scenario_key("pump_trip")


@pytest.fixture
def client():
    client = main.app.test_client()
    yield client
    cookie = client.get_cookie(main.SESSION_COOKIE)
    if cookie is not None:
        main.sessions.end(cookie.value)


def console(client):
    response = client.get("/console")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def run_js(body, **data):
    script = (
        f"const S = require({json.dumps(str(SCENARIO_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def send(client, request):
    return client.post(request["url"], data=request["init"].get("body"), content_type="application/json")


@needs_node
def test_the_requests_the_page_builds_take_a_loaded_scenario_to_running_and_aborted(client):
    start = run_js("return S.buildStartRequest();")
    abort = run_js("return S.buildAbortRequest();")
    client.post("/api/scenario/load", json={"scenario": KEY})

    started = send(client, start)
    assert started.status_code == 200
    assert run_js("return S.phaseOf(200, data.reply);", reply=started.get_json()) == "running"

    aborted = send(client, abort)
    assert aborted.status_code == 200
    assert run_js("return S.phaseOf(200, data.reply);", reply=aborted.get_json()) == "aborted"


@needs_node
def test_the_result_request_follows_each_phase_and_a_409_is_free_play(client):
    request = run_js("return S.buildResultRequest();")
    phases = []

    free = client.get(request["url"])
    phases.append(run_js("return S.phaseOf(data.status, null);", status=free.status_code))
    client.post("/api/scenario/load", json={"scenario": KEY})
    for step in (None, "/api/scenario/start", "/api/scenario/abort"):
        if step:
            client.post(step)
        got = client.get(request["url"])
        phases.append(run_js("return S.phaseOf(data.status, data.reply);", status=got.status_code, reply=got.get_json()))

    assert free.status_code == 409
    assert phases == ["idle", "loaded", "running", "aborted"]


@needs_node
def test_a_start_or_abort_the_runner_refuses_is_a_409_the_page_words_itself(client):
    start = run_js("return S.buildStartRequest();")
    abort = run_js("return S.buildAbortRequest();")

    not_loaded = send(client, start)
    client.post("/api/scenario/load", json={"scenario": KEY})
    client.post("/api/scenario/start")
    client.post("/api/scenario/abort")
    over = send(client, abort)

    assert not_loaded.status_code == over.status_code == 409
    for action, refused in (("start", not_loaded), ("abort", over)):
        text = run_js("return S.refusalText(data.action, data.status);", action=action, status=409)
        assert text and text[0].isupper() and text.endswith(".")
        assert text != refused.get_json()["error"]
        assert not any(path.stem in text for path in SCENARIOS)


@needs_node
@pytest.mark.parametrize("action", ["start", "abort"])
@pytest.mark.parametrize("status", [400, 404, 409, 429, 500, 0])
def test_every_refusal_is_plain_words(action, status):
    text = run_js("return S.refusalText(data.action, data.status);", action=action, status=status)

    assert text and text[0].isupper() and text.endswith(".")


@needs_node
@pytest.mark.parametrize(
    ("phase", "armed", "expected"),
    [
        ("idle", False, {"start": False, "abort": False, "confirm": False, "keep": False, "choose": "Choose a scenario"}),
        ("loaded", False, {"start": True, "abort": False, "confirm": False, "keep": False, "choose": "Choose another scenario"}),
        ("running", False, {"start": False, "abort": True, "confirm": False, "keep": False, "choose": ""}),
        ("running", True, {"start": False, "abort": False, "confirm": True, "keep": True, "choose": ""}),
        ("complete", False, {"start": False, "abort": False, "confirm": False, "keep": False, "choose": "Choose a scenario"}),
        ("aborted", True, {"start": False, "abort": False, "confirm": False, "keep": False, "choose": "Choose a scenario"}),
    ],
)
def test_the_bar_offers_the_right_controls_for_each_phase(phase, armed, expected):
    assert run_js("return S.controlsFor(data.phase, data.armed);", phase=phase, armed=armed) == expected


@needs_node
def test_a_body_with_no_known_phase_changes_nothing():
    got = run_js(
        "return [S.phaseOf(200, {phase: 'bogus'}), S.phaseOf(200, null), S.phaseOf(500, {phase: 'running'}), S.phaseOf(200, {})];",
    )

    assert got == [None, None, None, None]


# A stand-in for the page: elements with the attributes mount() touches, and a
# scripted fetch. Each response is {status, body}; a function is called per request.
STUB = """
const mk = () => ({ hidden: false, disabled: false, textContent: "", attrs: {}, listeners: {},
  setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
  addEventListener(t, f) { this.listeners[t] = f; } });
function page(phase, title) {
  const ids = ["scenario-bar", "scenario-title", "scenario-state", "scenario-notice", "scenario-start",
    "scenario-abort", "scenario-confirm", "scenario-abort-confirm", "scenario-abort-keep", "scenario-choose",
    "scenario-choose-label"];
  const els = Object.fromEntries(ids.map(id => [id, mk()]));
  els["scenario-bar"].attrs = { "data-phase": phase, "data-title": title,
    "data-phase-labels": JSON.stringify({ idle: "", loaded: "Loaded, not started", running: "Running", complete: "Finished", aborted: "Aborted" }) };
  return { els, doc: { getElementById: id => els[id] } };
}
const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const settle = () => new Promise(r => setTimeout(r, 0));
"""


@needs_node
def test_the_first_render_shows_the_servers_phase_and_title_with_the_right_buttons():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "Falling vessel level");
        S.mount(doc, { fetch: async () => reply(409, {}), pollMs: 0 });
        return {
          mode: els["scenario-title"].textContent, phase: els["scenario-state"].textContent,
          start: els["scenario-start"].hidden, abort: els["scenario-abort"].hidden,
          choose: els["scenario-choose-label"].textContent,
        };
        """,
    )

    assert got == {
        "mode": "Falling vessel level",
        "phase": "Scenario \u00b7 Loaded, not started",
        "start": False,
        "abort": True,
        "choose": "Choose another scenario",
    }


@needs_node
def test_start_then_abort_walk_the_bar_through_its_phases_without_a_reload():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "Falling vessel level");
        const calls = [];
        const fetchImpl = async (url, init) => {
          calls.push(url);
          return reply(200, { phase: url.endsWith("start") ? "running" : "aborted" });
        };
        S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        const seen = [];
        const snap = () => seen.push({ phase: els["scenario-state"].textContent,
          start: !els["scenario-start"].hidden, abort: !els["scenario-abort"].hidden,
          confirm: !els["scenario-abort-confirm"].hidden, keep: !els["scenario-abort-keep"].hidden });
        await els["scenario-start"].listeners.click(); snap();
        els["scenario-abort"].listeners.click(); snap();
        const armedCalls = calls.length;
        await els["scenario-abort-confirm"].listeners.click(); snap();
        return { seen, calls, armedCalls };
        """,
    )

    assert got["calls"] == ["/api/scenario/start", "/api/scenario/abort"]
    assert got["armedCalls"] == 1
    assert got["seen"] == [
        {"phase": "Scenario \u00b7 Running", "start": False, "abort": True, "confirm": False, "keep": False},
        {"phase": "Scenario \u00b7 Running", "start": False, "abort": False, "confirm": True, "keep": True},
        {"phase": "Scenario \u00b7 Aborted", "start": False, "abort": False, "confirm": False, "keep": False},
    ]


@needs_node
def test_keep_running_disarms_abort_and_sends_nothing():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("running", "T");
        const calls = [];
        S.mount(doc, { fetch: async url => { calls.push(url); return reply(200, { phase: "running" }); }, pollMs: 0 });
        els["scenario-abort"].listeners.click();
        els["scenario-abort-keep"].listeners.click();
        return { calls, abort: !els["scenario-abort"].hidden, confirm: !els["scenario-abort-confirm"].hidden };
        """,
    )

    assert got == {"calls": [], "abort": True, "confirm": False}


@needs_node
def test_a_409_shows_the_pages_words_then_resyncs_from_the_result():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "T");
        const calls = [];
        const fetchImpl = async url => {
          calls.push(url);
          return url.endsWith("start") ? reply(409, { error: "cannot start a scenario that is running; load one first" })
                                       : reply(200, { phase: "running" });
        };
        S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await els["scenario-start"].listeners.click();
        return { calls, notice: els["scenario-notice"].textContent, hidden: els["scenario-notice"].hidden,
                 phase: els["scenario-state"].textContent };
        """,
    )

    assert got["calls"] == ["/api/scenario/start", "/api/scenario/result"]
    assert got["phase"] == "Scenario \u00b7 Running"
    assert got["hidden"] is False
    assert "no longer ready to start" in got["notice"]
    assert "cannot start" not in got["notice"]


@needs_node
def test_a_poll_follows_the_server_and_a_409_is_free_play_and_disarms_abort():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("running", "Falling vessel level");
        let next = reply(200, { phase: "complete", key: "k" });
        const bar = S.mount(doc, { fetch: async () => next, pollMs: 0 });
        els["scenario-abort"].listeners.click();
        await bar.poll();
        const complete = { phase: els["scenario-state"].textContent, confirm: els["scenario-abort-confirm"].hidden,
          mode: els["scenario-title"].textContent };
        next = reply(409, { error: "no scenario is loaded" });
        await bar.poll();
        return { complete, free: { mode: els["scenario-title"].textContent, phase: els["scenario-state"].textContent } };
        """,
    )

    assert got["complete"] == {"phase": "Scenario \u00b7 Finished", "confirm": True, "mode": "Falling vessel level"}
    assert got["free"] == {"mode": "Free play", "phase": "Plant running"}


@needs_node
def test_a_failed_poll_leaves_the_bar_alone_and_buttons_disable_while_a_call_is_in_flight():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "T");
        let release;
        const hold = new Promise(r => (release = r));
        let mode = "fail";
        const fetchImpl = async () => { if (mode === "fail") throw new Error("down"); await hold; return reply(200, { phase: "running" }); };
        const bar = S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await bar.poll();
        const afterFail = els["scenario-state"].textContent;
        mode = "hold";
        const pending = els["scenario-start"].listeners.click();
        await settle();
        const inFlight = els["scenario-start"].disabled;
        release();
        await pending;
        return { afterFail, inFlight, after: els["scenario-start"].disabled };
        """,
    )

    assert got == {"afterFail": "Scenario \u00b7 Loaded, not started", "inFlight": True, "after": False}


def test_the_console_renders_free_play_with_no_scenario_text(client):
    page = console(client)

    assert 'id="scenario-bar"' in page
    assert 'data-phase="idle"' in page
    assert ">Free play<" in page
    assert ">Plant running<" in page
    assert "js/scenario.js" in page
    assert "Ribbon.mount(document, { onChange: graphic.refresh })" in page
    assert page.index("Trends.mount") < page.index("Ribbon.mount(document, {")


def test_the_consoles_first_render_carries_the_standing_phase_and_title(client):
    client.post("/api/scenario/load", json={"scenario": KEY})
    loaded = console(client)
    assert 'data-phase="loaded"' in loaded
    assert ">Falling vessel level<" in loaded
    assert ">Scenario &middot; Loaded, not started<" in loaded

    client.post("/api/scenario/start")
    assert 'data-phase="running"' in console(client)
    assert ">Scenario &middot; Running<" in console(client)

    client.post("/api/scenario/abort")
    aborted = console(client)
    assert 'data-phase="aborted"' in aborted
    assert ">Scenario &middot; Aborted<" in aborted


def test_the_console_sends_a_label_for_every_runner_phase(client):
    from app.scenarios.runner import Phase

    sent = json.loads(re.search(r"data-phase-labels='([^']*)'", console(client)).group(1))

    assert set(sent) == {phase.value for phase in Phase}


def test_no_live_response_or_console_names_a_scenario_id_or_its_fault(client):
    for path in SCENARIOS:
        doc = yaml.safe_load(path.read_text())
        words = {path.stem}
        words |= {m["target_tag"] for m in doc.get("malfunctions", [])}
        words |= {m["parameter"] for m in doc.get("malfunctions", [])}
        words |= {m["id"] for m in doc.get("triggers", []) if isinstance(m, dict) and "id" in m}
        client.post("/api/scenario/load", json={"scenario": scenario_key(path.stem)})
        seen = [console(client)]
        seen.append(client.post("/api/scenario/start").get_data(as_text=True))
        seen.append(client.get("/api/scenario/result").get_data(as_text=True))
        seen.append(console(client))
        client.post("/api/scenario/abort")
        client.post("/api/scenario/unload")
        flat = " ".join(seen)

        for word in words:
            assert not re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", flat), (path.name, word)
        assert "Diagnosis path" not in flat, path.name


def test_the_landing_page_links_back_to_the_console_only_with_a_scenario(client):
    assert "back-to-console" not in client.get("/").get_data(as_text=True)

    client.post("/api/scenario/load", json={"scenario": KEY})
    page = client.get("/").get_data(as_text=True)

    assert 'id="back-to-console" href="/console"' in page
    assert "Start run" not in page
    assert "Abort run" not in page


def test_the_stylesheet_uses_tokens_and_no_colour_literals():
    css = SCENARIO_CSS.read_text()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)


@needs_node
def test_a_poll_follows_a_title_that_changes_or_appears_after_mount():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("idle", "");
        let next = reply(200, { phase: "loaded", title: "Falling vessel level" });
        const bar = S.mount(doc, { fetch: async () => next, pollMs: 0 });
        await bar.poll();
        const appeared = els["scenario-title"].textContent;
        next = reply(200, { phase: "loaded", title: "Blocked drain" });
        await bar.poll();
        const changed = els["scenario-title"].textContent;
        next = reply(200, { phase: "loaded" });
        await bar.poll();
        return { appeared, changed, kept: els["scenario-title"].textContent };
        """,
    )

    assert got == {
        "appeared": "Falling vessel level",
        "changed": "Blocked drain",
        "kept": "Blocked drain",
    }


@needs_node
def test_a_poll_that_began_before_a_click_cannot_overwrite_the_clicks_result():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "T");
        let releasePoll;
        const held = new Promise(r => (releasePoll = r));
        const fetchImpl = async url => {
          if (url.endsWith("result")) { await held; return reply(200, { phase: "loaded", title: "T" }); }
          return reply(200, { phase: "running", title: "T" });
        };
        const bar = S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        const stale = bar.poll();
        await settle();
        await els["scenario-start"].listeners.click();
        releasePoll();
        await stale;
        return els["scenario-state"].textContent;
        """,
    )

    assert got == "Scenario \u00b7 Running"


@needs_node
def test_a_refusal_waits_out_a_poll_in_flight_and_then_resyncs():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "T");
        let releasePoll, results = 0;
        const held = new Promise(r => (releasePoll = r));
        const fetchImpl = async url => {
          if (url.endsWith("start")) return reply(409, {});
          results += 1;
          if (results === 1) { await held; return reply(200, { phase: "loaded", title: "T" }); }
          return reply(200, { phase: "running", title: "T" });
        };
        const bar = S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        const first = bar.poll();
        await settle();
        const click = els["scenario-start"].listeners.click();
        await settle();
        releasePoll();
        await click;
        await first;
        return { results, phase: els["scenario-state"].textContent };
        """,
    )

    assert got == {"results": 2, "phase": "Scenario \u00b7 Running"}


@needs_node
def test_a_request_that_never_finishes_does_not_claim_nothing_changed_and_resyncs():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "T");
        const calls = [];
        const fetchImpl = async url => {
          calls.push(url);
          if (url.endsWith("start")) throw new Error("timed out");
          return reply(200, { phase: "running", title: "T" });
        };
        S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await els["scenario-start"].listeners.click();
        return { calls, notice: els["scenario-notice"].textContent, phase: els["scenario-state"].textContent };
        """,
    )

    assert got["calls"] == ["/api/scenario/start", "/api/scenario/result"]
    assert got["phase"] == "Scenario \u00b7 Running"
    assert "nothing was changed" not in got["notice"]
    assert "did not finish" in got["notice"]
    assert "refreshed" not in got["notice"]


@needs_node
@pytest.mark.parametrize("action", ["start", "abort"])
def test_a_5xx_does_not_claim_nothing_changed_and_resyncs(action):
    got = run_js(
        STUB
        + """
        const { els, doc } = page(data.action === "start" ? "loaded" : "running", "T");
        const calls = [];
        const fetchImpl = async url => {
          calls.push(url);
          return url.endsWith("result") ? reply(200, { phase: "running", title: "T" }) : reply(500, {});
        };
        S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        if (data.action === "abort") els["scenario-abort"].listeners.click();
        await els[data.action === "start" ? "scenario-start" : "scenario-abort-confirm"].listeners.click();
        return { calls, notice: els["scenario-notice"].textContent };
        """,
        action=action,
    )

    assert got["calls"][-1] == "/api/scenario/result"
    assert "nothing was changed" not in got["notice"]


@needs_node
def test_a_thrown_abort_resyncs_even_in_a_hidden_tab_and_enables_the_buttons():
    got = run_js(
        STUB
        + """
        globalThis.document = { hidden: true };
        const { els, doc } = page("running", "T");
        const calls = [];
        const fetchImpl = async url => {
          calls.push(url);
          if (url.endsWith("abort")) throw new Error("down");
          return reply(200, { phase: "aborted", title: "T" });
        };
        S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        els["scenario-abort"].listeners.click();
        await els["scenario-abort-confirm"].listeners.click();
        return { calls, phase: els["scenario-state"].textContent, notice: els["scenario-notice"].textContent,
                 disabled: els["scenario-start"].disabled };
        """,
    )

    assert got["calls"] == ["/api/scenario/abort", "/api/scenario/result"]
    assert got["phase"] == "Scenario \u00b7 Aborted"
    assert "did not finish" in got["notice"]
    assert got["disabled"] is False


@needs_node
def test_a_200_with_no_known_phase_resyncs_from_the_result():
    got = run_js(
        STUB
        + """
        const { els, doc } = page("loaded", "T");
        const calls = [];
        const fetchImpl = async url => { calls.push(url); return reply(200, url.endsWith("start") ? {} : { phase: "running", title: "T" }); };
        S.mount(doc, { fetch: fetchImpl, pollMs: 0 });
        await els["scenario-start"].listeners.click();
        return { calls, phase: els["scenario-state"].textContent };
        """,
    )

    assert got == {"calls": ["/api/scenario/start", "/api/scenario/result"], "phase": "Scenario \u00b7 Running"}


@needs_node
def test_on_change_is_called_only_when_a_response_moves_the_phase_or_the_title():
    got = run_js(
        STUB
        + """
        const { doc } = page("idle", "");
        let calls = 0;
        let next = reply(409, {});
        const bar = S.mount(doc, { fetch: async () => next, pollMs: 0, onChange: () => { calls += 1; } });
        await bar.poll();
        const unchanged = calls;
        next = reply(200, { phase: "loaded", title: "Blocked drain" });
        await bar.poll();
        await bar.poll();
        const loaded = calls;
        next = reply(200, { phase: "loaded", title: "Falling vessel level" });
        await bar.poll();
        const retitled = calls;
        next = reply(500, {});
        await bar.poll();
        const failed = calls;
        next = reply(409, {});
        await bar.poll();
        return { unchanged, loaded, retitled, failed, unloaded: calls };
        """,
    )

    assert got == {"unchanged": 0, "loaded": 1, "retitled": 2, "failed": 2, "unloaded": 3}
