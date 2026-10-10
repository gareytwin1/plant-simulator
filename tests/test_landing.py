"""Landing page - T16-11: GET /, the shared header, the scenario catalogue and
static/js/landing.js.

The acceptance test that matters most is the disclosure one: a scenario's
`description` carries its diagnosis path, so nothing the page sends may contain
it. Every file in config/scenarios is walked, so a new scenario is covered
without editing this file.
"""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from app import main
from app.scenarios.runner import ScenarioConfigError, ScenarioLibrary, scenario_key

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = sorted((ROOT / "config" / "scenarios").glob("*.yaml"))
LANDING_JS = ROOT / "static" / "js" / "landing.js"
LANDING_CSS = ROOT / "static" / "css" / "landing.css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def squash(text):
    return " ".join(text.split())


def document(path):
    return yaml.safe_load(path.read_text())


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs)["href"])


@pytest.fixture
def client():
    return main.app.test_client()


@pytest.fixture
def page(client):
    response = client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_every_scenario_file_carries_a_title_and_briefing():
    for path in SCENARIOS:
        doc = document(path)
        assert doc.get("title"), path.name
        assert doc.get("briefing"), path.name


def test_the_page_never_contains_a_scenarios_description(page):
    flat = squash(page)

    for path in SCENARIOS:
        description = squash(document(path)["description"])
        assert description not in flat, path.name
        # Not even the sentence that carries the diagnosis path.
        assert "Diagnosis path" not in flat, path.name


def test_a_title_or_briefing_never_names_the_fault_target(page):
    for path in SCENARIOS:
        doc = document(path)
        words = {m["target_tag"] for m in doc.get("malfunctions", [])}
        words |= {m["parameter"] for m in doc.get("malfunctions", [])}
        for key in doc["initial_condition"].get("overrides", {}):
            tag, _, attribute = key.partition(".")
            words |= {tag, attribute} - {""}
        text = f"{doc['title']} {doc['briefing']}"

        for word in words:
            assert not re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text), (path.name, word)


def test_the_page_shows_each_scenarios_title_briefing_and_difficulty(page):
    flat = squash(page)

    for path in SCENARIOS:
        doc = document(path)
        assert doc["title"] in flat, path.name
        assert squash(doc["briefing"]) in flat, path.name
        assert f'data-difficulty="{doc["difficulty"]}"' in flat, path.name


def test_the_page_carries_each_scenarios_key_and_never_its_id(page):
    for path in SCENARIOS:
        assert f'data-scenario="{scenario_key(path.stem)}"' in page
        assert path.stem not in page


def test_the_page_offers_start_free_play_and_one_choice_per_scenario(page):
    assert 'id="hero-start"' in page
    assert 'id="free-play"' in page
    assert page.count('class="scenario-option"') == len(SCENARIOS)


def test_the_first_catalogue_entry_is_selected_by_default(page):
    first = main.CATALOGUE[0]

    assert re.search(rf'id="hero-title"[^>]*>{re.escape(first.title)}<', page)
    assert f'id="hero-start" data-scenario="{first.key}"' in page
    assert page.count('aria-pressed="true"') == 1


def test_the_hero_shows_the_time_limit_in_minutes(page):
    first = main.CATALOGUE[0]

    assert f"Time limit {first.time_limit_s / 60:g} min" in squash(page)


def test_recent_scenarios_is_an_honest_empty_state(page):
    flat = squash(page)

    assert "Recent scenarios" in flat
    assert "No finished runs to show yet" in flat


def test_the_thumbnail_is_the_plants_graphic_and_is_not_interactive(client, page):
    url = re.search(r'data-thumbnail="([^"]+)"', page).group(1)

    assert client.get(url).status_code == 200
    figure = re.search(r'<figure class="hero-art".*?</figure>', page, re.S).group(0)
    assert 'aria-hidden="true"' in figure
    assert " inert" in figure
    assert not re.search(r"<(a|button|input)\b", figure)


def test_the_return_strip_shows_only_while_a_scenario_stands(client):
    free = client.get("/").get_data(as_text=True)
    assert 'id="standing"' not in free

    client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})
    loaded = client.get("/").get_data(as_text=True)
    assert 'id="standing"' in loaded
    assert 'id="back-to-console"' in loaded


def test_the_header_links_only_to_pages_that_exist(client, page):
    links = Links()
    links.feed(page)

    assert links.hrefs
    try:
        for href in links.hrefs:
            assert client.get(href).status_code == 200, href
    finally:
        # The console link starts the session's scheduler; end it so the
        # worker does not outlive the test.
        main.sessions.end(client.get_cookie(main.SESSION_COOKIE).value)


def test_rendering_the_page_does_not_start_a_scheduler(client):
    client.get("/")

    session = main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)

    assert session.training_scheduler.running is False


def test_the_page_starts_in_free_play_with_nothing_to_return_to(page):
    assert 'data-phase="idle"' in page
    assert 'id="standing"' not in page
    assert "Plant not started" in page


def test_the_return_strip_shows_the_plants_own_time(client):
    client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})
    snapshot = client.get("/api/snapshot").get_json()

    assert f'id="standing-time">{snapshot["sim_time"]:.0f}<' in client.get("/").get_data(as_text=True)


def test_the_page_follows_a_loaded_scenario_and_free_play_unloads_it(client):
    client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})

    loaded = squash(client.get("/").get_data(as_text=True))
    assert 'data-mode="scenario"' in loaded
    assert "Scenario: Falling vessel level" in loaded
    assert "Loaded, not started" in loaded

    assert client.post("/api/scenario/unload").status_code == 200

    free = client.get("/").get_data(as_text=True)
    assert 'data-mode="scenario"' not in free
    assert 'data-phase="idle"' in free


def test_the_base_template_gives_every_page_the_tokens_and_the_header(page):
    assert "css/tokens.css" in page
    assert 'class="ribbon" id="ribbon"' in page
    assert 'aria-current="page"' in page


def test_the_stylesheet_uses_tokens_and_no_colour_literals():
    css = LANDING_CSS.read_text()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)


def test_the_runtime_image_copies_the_templates():
    assert "COPY templates ./templates" in (ROOT / "Dockerfile").read_text()


def test_the_catalogue_is_easiest_first_and_carries_no_description():
    entries = ScenarioLibrary().catalogue()

    assert {e.id for e in entries} == {p.stem for p in SCENARIOS}
    order = {"easy": 0, "medium": 1, "hard": 2}
    assert [order[e.difficulty] for e in entries] == sorted(order[e.difficulty] for e in entries)
    assert not any(hasattr(e, "description") for e in entries)


def test_the_catalogue_falls_back_to_the_id_without_a_title(tmp_path):
    (tmp_path / "plain.yaml").write_text(
        "id: plain\nplant: olefins_lite\ninitial_condition: {condition: normal_operation}\n"
        "time_limit_s: 60\ndifficulty: easy\nseed: 1\n",
    )

    (entry,) = ScenarioLibrary(scenarios=tmp_path).catalogue()

    assert (entry.id, entry.title, entry.briefing) == ("plain", "plain", "")


def test_the_catalogue_refuses_an_id_that_is_not_the_file_name(tmp_path):
    (tmp_path / "plain.yaml").write_text(
        "id: other\nplant: olefins_lite\ninitial_condition: {condition: normal_operation}\n"
        "time_limit_s: 60\ndifficulty: easy\nseed: 1\n",
    )

    with pytest.raises(ScenarioConfigError, match="plain.yaml"):
        ScenarioLibrary(scenarios=tmp_path).catalogue()


def test_the_catalogue_refuses_a_file_that_fails_the_schema(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: bad\ntitle: ''\n")

    with pytest.raises(ScenarioConfigError, match="bad.yaml"):
        ScenarioLibrary(scenarios=tmp_path).catalogue()


def run_js(body, **data):
    script = (
        f"const L = require({json.dumps(str(LANDING_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@needs_node
def test_the_load_request_posts_the_scenario_the_api_expects(client):
    request = run_js("return L.buildLoadRequest(data.key);", key=scenario_key("pump_trip"))

    assert request["url"] == "/api/scenario/load"
    assert request["init"]["method"] == "POST"
    response = client.post(request["url"], data=request["init"]["body"], content_type="application/json")
    assert response.status_code == 200


@needs_node
def test_the_unload_request_is_accepted_by_the_api(client):
    request = run_js("return L.buildUnloadRequest();")

    assert client.post(request["url"]).status_code == 200


@needs_node
@pytest.mark.parametrize("status", [400, 404, 409, 429, 500, 0])
def test_a_refusal_is_plain_words_that_never_quote_a_scenario(status):
    text = run_js("return L.refusalText(data.status);", status=status)
    ids = {p.stem for p in SCENARIOS}

    assert text and text[0].isupper() and text.endswith(".")
    assert not any(i in text for i in ids)


@needs_node
def test_a_running_scenario_refusal_is_the_one_the_api_gives(client):
    client.post("/api/scenario/load", json={"scenario": scenario_key("pump_trip")})
    client.post("/api/scenario/start")

    refused = client.post("/api/scenario/load", json={"scenario": scenario_key("blocked_drain")})
    text = run_js("return L.refusalText(data.status);", status=refused.status_code)

    assert refused.status_code == 409
    assert "running" in text


@needs_node
def test_a_phase_label_comes_from_the_servers_map_and_unknown_phases_are_blank():
    labels = {"idle": "", "running": "Running"}

    assert run_js("return [L.phaseLabel(data.l, 'running'), L.phaseLabel(data.l, 'nope'), L.phaseLabel(data.l, 'constructor')];", l=labels) == [
        "Running",
        "",
        "",
    ]


def test_the_page_sends_a_label_for_every_runner_phase(page):
    from app.scenarios.runner import Phase

    sent = json.loads(re.search(r"data-phase-labels='([^']*)'", page).group(1))

    assert set(sent) == {phase.value for phase in Phase}


# A stand-in for the page, enough for Landing.mount to run under Node.
PAGE_STUB = """
function el(attrs) {
  return {
    attrs: Object.assign({}, attrs), listeners: {}, hidden: false, disabled: false, textContent: "", focused: 0,
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener(t, f) { this.listeners[t] = f; },
    focus() { this.focused += 1; },
  };
}
function makePage(phase, keys) {
  const ids = {};
  const add = (id, attrs) => (ids[id] = el(attrs));
  add("launch", { "data-phase-labels": '{"idle": "", "running": "Running", "aborted": "Aborted"}', "data-phase": phase });
  ["standing", "standing-phase", "notice", "free-play", "choose-another", "scenario-picker", "hero-actions",
   "abort-confirm", "abort-confirm-yes", "abort-confirm-no", "hero-title", "hero-briefing", "hero-limit", "hero-difficulty"].forEach(id => add(id));
  ids["abort-confirm"].hidden = true;
  ids["scenario-picker"].hidden = true;
  add("hero-start", { "data-scenario": keys[0] });
  const choices = keys.map(k => el({ "data-scenario": k, "data-title": "T " + k, "data-briefing": "B " + k, "data-limit": "9", "data-difficulty": "hard" }));
  const header = el();
  const doc = {
    ids, choices, header,
    getElementById: id => ids[id] || null,
    querySelectorAll: sel => (sel === "main button" ? Object.values(ids).concat(choices) : sel === ".scenario-option" ? choices : []),
  };
  return doc;
}
function makeFetch(statusFor) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push(url);
    const status = statusFor(url);
    return { ok: status === 200, status, json: async () => ({ phase: url.endsWith("abort") ? "aborted" : url.endsWith("unload") ? "idle" : "loaded" }) };
  };
  fn.calls = calls;
  return fn;
}
const tick = () => new Promise(r => setTimeout(r, 0));
const memory = (store) => ({ getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } });
"""


def run_page(body, **data):
    return run_js(PAGE_STUB + body, **data)


@needs_node
def test_start_loads_then_starts_the_selected_scenario_and_opens_the_console():
    result = run_page(
        """
        const doc = makePage("idle", ["a", "b"]), fetchImpl = makeFetch(() => 200), went = [];
        L.mount(doc, fetchImpl, u => went.push(u), { storage: memory({}), refreshRibbon() {} });
        doc.ids["hero-start"].listeners.click();
        await tick(); await tick(); await tick();
        return { calls: fetchImpl.calls, went };
        """
    )

    assert result == {"calls": ["/api/scenario/load", "/api/scenario/start"], "went": ["/console"]}


@needs_node
def test_free_play_goes_to_the_console_once_it_succeeds():
    result = run_page(
        """
        const doc = makePage("idle", ["a"]), fetchImpl = makeFetch(() => 200), went = [];
        L.mount(doc, fetchImpl, u => went.push(u), { storage: memory({}), refreshRibbon() {} });
        doc.ids["free-play"].listeners.click();
        await tick(); await tick();
        return { calls: fetchImpl.calls, went };
        """
    )

    assert result == {"calls": ["/api/scenario/unload"], "went": ["/console"]}


@needs_node
def test_a_refused_free_play_keeps_the_landing_page():
    result = run_page(
        """
        const doc = makePage("idle", ["a"]), went = [];
        L.mount(doc, makeFetch(() => 409), u => went.push(u), { storage: memory({}), refreshRibbon() {} });
        doc.ids["free-play"].listeners.click();
        await tick(); await tick();
        return { went, shown: doc.ids.notice.textContent };
        """
    )

    assert result["went"] == []
    assert "running" in result["shown"]


@needs_node
def test_the_start_requests_take_free_play_to_a_running_scenario(client):
    steps = run_js("return L.startSteps(data.key, false);", key=scenario_key("pump_trip"))

    for step in steps:
        response = client.post(step["url"], data=step["init"].get("body"), content_type="application/json")
        assert response.status_code == 200, step["url"]
        for path in SCENARIOS:
            assert path.stem not in response.get_data(as_text=True)

    assert client.get("/api/scenario/result").get_json()["phase"] == "running"


@needs_node
def test_start_during_a_run_asks_first_and_declining_sends_nothing():
    result = run_page(
        """
        const doc = makePage("running", ["a", "b"]), fetchImpl = makeFetch(() => 200), went = [];
        L.mount(doc, fetchImpl, u => went.push(u), { storage: memory({}), refreshRibbon() {} });
        doc.ids["hero-start"].listeners.click();
        await tick();
        const asked = { confirm: !doc.ids["abort-confirm"].hidden, actions: doc.ids["hero-actions"].hidden, calls: fetchImpl.calls.length };
        doc.ids["abort-confirm-no"].listeners.click();
        await tick();
        return { asked, calls: fetchImpl.calls, went, shown: !doc.ids["hero-actions"].hidden, start: doc.ids["hero-start"].focused };
        """
    )

    assert result["asked"] == {"confirm": True, "actions": True, "calls": 0}
    assert result["calls"] == [] and result["went"] == []
    assert result["shown"] and result["start"] == 1


@needs_node
def test_confirming_aborts_then_loads_and_starts():
    result = run_page(
        """
        const doc = makePage("running", ["a", "b"]), fetchImpl = makeFetch(() => 200), went = [];
        L.mount(doc, fetchImpl, u => went.push(u), { storage: memory({}), refreshRibbon() {} });
        doc.ids["hero-start"].listeners.click();
        doc.ids["abort-confirm-yes"].listeners.click();
        for (let i = 0; i < 6; i += 1) await tick();
        return { calls: fetchImpl.calls, went };
        """
    )

    assert result["calls"] == ["/api/scenario/abort", "/api/scenario/load", "/api/scenario/start"]
    assert result["went"] == ["/console"]


@needs_node
def test_free_play_during_a_run_asks_first_then_aborts_and_unloads():
    result = run_page(
        """
        const doc = makePage("running", ["a"]), fetchImpl = makeFetch(() => 200), went = [];
        L.mount(doc, fetchImpl, u => went.push(u), { storage: memory({}), refreshRibbon() {} });
        doc.ids["free-play"].listeners.click();
        await tick();
        const before = fetchImpl.calls.length;
        doc.ids["abort-confirm-yes"].listeners.click();
        for (let i = 0; i < 6; i += 1) await tick();
        return { before, calls: fetchImpl.calls, went };
        """
    )

    assert result["before"] == 0
    assert result["calls"] == ["/api/scenario/abort", "/api/scenario/unload"]
    assert result["went"] == ["/console"]


@needs_node
@pytest.mark.parametrize("status", [409, 429])
def test_a_refused_start_is_worded_by_status_and_stays_on_the_page(status):
    result = run_page(
        """
        const doc = makePage("idle", ["a"]), fetchImpl = makeFetch(u => (u.endsWith("start") ? data.status : 200)), went = [];
        let refreshed = 0;
        L.mount(doc, fetchImpl, u => went.push(u), { storage: memory({}), refreshRibbon() { refreshed += 1; } });
        doc.ids["hero-start"].listeners.click();
        for (let i = 0; i < 4; i += 1) await tick();
        return { text: doc.ids.notice.textContent, went, refreshed, disabled: doc.ids["hero-start"].disabled, calls: fetchImpl.calls };
        """,
        status=status,
    )

    assert result["text"] == run_js("return L.refusalText(data.status);", status=status)
    assert result["went"] == [] and result["refreshed"] == 1 and not result["disabled"]
    assert result["calls"] == ["/api/scenario/load", "/api/scenario/start"]


@needs_node
def test_choosing_another_scenario_updates_the_hero_remembers_it_and_returns_focus_to_start():
    result = run_page(
        """
        const store = {}, doc = makePage("idle", ["a", "b"]);
        L.mount(doc, makeFetch(() => 200), () => {}, { storage: memory(store), refreshRibbon() {} });
        doc.ids["choose-another"].listeners.click();
        const opened = [!doc.ids["scenario-picker"].hidden, doc.ids["choose-another"].attrs["aria-expanded"]];
        doc.choices[1].listeners.click();
        return {
            opened, store, start: doc.ids["hero-start"].attrs["data-scenario"], title: doc.ids["hero-title"].textContent,
            limit: doc.ids["hero-limit"].textContent, chip: doc.ids["hero-difficulty"].textContent,
            pressed: doc.choices.map(c => c.attrs["aria-pressed"]), closed: doc.ids["scenario-picker"].hidden,
            focus: doc.ids["hero-start"].focused,
        };
        """
    )

    assert result["opened"] == [True, "true"]
    assert result["store"] == {"landing.scenario": "b"}
    assert (result["start"], result["title"], result["limit"], result["chip"]) == ("b", "T b", "Time limit 9 min", "Hard")
    assert result["pressed"] == ["false", "true"] and result["closed"] and result["focus"] == 1


@needs_node
def test_a_remembered_choice_is_restored_and_a_stale_or_blocked_one_is_ignored():
    result = run_js(
        """
        const throwing = { getItem() { throw new Error("blocked"); } };
        return [
          L.chooseScenario({ getItem: () => "b" }, ["a", "b"]),
          L.chooseScenario({ getItem: () => "gone" }, ["a", "b"]),
          L.chooseScenario(throwing, ["a", "b"]),
          L.chooseScenario(null, ["a", "b"]),
        ];
        """
    )

    assert result == ["b", "a", "a", "a"]


@needs_node
def test_remembering_a_choice_with_blocked_storage_does_not_throw():
    run_js("L.rememberScenario({ setItem() { throw new Error('blocked'); } }, 'a'); L.rememberScenario(null, 'a');")


@needs_node
def test_only_the_pages_action_buttons_are_disabled_while_a_request_is_in_flight():
    result = run_page(
        """
        const doc = makePage("idle", ["a"]);
        let release;
        const fetchImpl = () => new Promise(res => { release = () => res({ ok: true, json: async () => ({}) }); });
        L.mount(doc, fetchImpl, () => {}, { storage: memory({}), refreshRibbon() {} });
        doc.ids["free-play"].listeners.click();
        await tick();
        const inFlight = { free: doc.ids["free-play"].disabled, header: doc.header.disabled };
        release();
        return { inFlight };
        """,
    )

    assert result["inFlight"] == {"free": True, "header": False}


THEME_JS = ROOT / "static" / "js" / "theme.js"


def run_theme(body, **data):
    script = (
        f"const T = require({json.dumps(str(THEME_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


# A stand-in for the page: a <html> with attributes, a toggle button, storage.
STUB = """
function makeDoc() {
  const attrs = {};
  const handlers = [];
  const button = {
    hidden: true, textContent: "", attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(_, fn) { handlers.push(fn); },
  };
  return {
    attrs, button, click() { handlers.forEach(fn => fn()); },
    documentElement: {
      setAttribute(k, v) { attrs[k] = v; },
      removeAttribute(k) { delete attrs[k]; },
    },
    getElementById(id) { return id === "theme-toggle" ? button : null; },
  };
}
function makeWin(store, throws) {
  return { localStorage: throws
    ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } }
    : { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } } };
}
"""


@needs_node
def test_the_theme_cycles_auto_light_dark_and_back():
    order = run_theme("let c = 'auto'; const seen = []; for (let i = 0; i < 4; i++) { c = T.next(c); seen.push(c); } return seen;")

    assert order == ["light", "dark", "auto", "light"]


@needs_node
@pytest.mark.parametrize("stored", [None, "", "sepia", "constructor", "__proto__"])
def test_an_unknown_stored_theme_is_auto(stored):
    assert run_theme("return T.normalise(data.v);", v=stored) == "auto"


@needs_node
def test_a_saved_theme_is_painted_before_the_page_draws():
    result = run_theme(STUB + "const d = makeDoc(); T.apply(d, makeWin({plant_theme: 'dark'})); return d.attrs;")

    assert result == {"data-theme": "dark"}


@needs_node
def test_auto_removes_the_override_so_the_os_preference_applies():
    result = run_theme(STUB + "const d = makeDoc(); d.attrs['data-theme'] = 'dark'; T.apply(d, makeWin({})); return d.attrs;")

    assert result == {}


@needs_node
def test_the_button_cycles_paints_labels_and_saves_the_choice():
    result = run_theme(
        STUB
        + """
        const d = makeDoc(); const store = {}; const win = makeWin(store);
        T.mount(d, win);
        const steps = [{ label: d.button.textContent, hidden: d.button.hidden, theme: d.attrs["data-theme"] ?? null }];
        for (let i = 0; i < 3; i++) {
          d.click();
          steps.push({ label: d.button.textContent, theme: d.attrs["data-theme"] ?? null, saved: store.plant_theme });
        }
        return steps;
        """,
    )

    assert result == [
        {"label": "Theme: Auto", "hidden": False, "theme": None},
        {"label": "Theme: Light", "theme": "light", "saved": "light"},
        {"label": "Theme: Dark", "theme": "dark", "saved": "dark"},
        {"label": "Theme: Auto", "theme": None, "saved": "auto"},
    ]


@needs_node
def test_a_page_with_blocked_storage_still_cycles():
    result = run_theme(
        STUB
        + """
        const d = makeDoc(); const win = makeWin({}, true);
        T.apply(d, win); T.mount(d, win); d.click();
        return [d.button.textContent, d.attrs["data-theme"]];
        """,
    )

    assert result == ["Theme: Light", "light"]


def test_the_header_carries_the_toggle_and_every_page_loads_theme_js_early(page):
    assert 'id="theme-toggle"' in page
    assert page.index("js/theme.js") < page.index("</head>")
    assert page.index("Theme.apply(document)") < page.index("</head>")
