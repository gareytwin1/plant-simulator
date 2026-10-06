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
from app.scenarios.runner import ScenarioConfigError, ScenarioLibrary

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
        words |= {key.split(".")[0] for key in doc["initial_condition"].get("overrides", {})}
        words |= {key.split(".")[1] for key in doc["initial_condition"].get("overrides", {})}
        text = f"{doc['title']} {doc['briefing']}"

        for word in words:
            assert word not in text, (path.name, word)


def test_the_page_shows_each_scenarios_title_briefing_and_difficulty(page):
    flat = squash(page)

    for path in SCENARIOS:
        doc = document(path)
        assert doc["title"] in flat, path.name
        assert squash(doc["briefing"]) in flat, path.name
        assert f"Difficulty: {doc['difficulty']}" in flat, path.name


def test_the_page_offers_free_play_and_one_load_button_per_scenario(page):
    assert 'id="free-play"' in page
    assert page.count('class="load-scenario"') == len(SCENARIOS)


def test_the_header_links_only_to_pages_that_exist(client, page):
    links = Links()
    links.feed(page)

    assert links.hrefs
    for href in links.hrefs:
        assert client.get(href).status_code == 200, href


def test_rendering_the_page_does_not_start_a_scheduler(client):
    client.get("/")

    session = main.sessions.get(client.get_cookie(main.SESSION_COOKIE).value)

    assert session.training_scheduler.running is False


def test_the_page_starts_in_free_play_at_the_plants_own_time(client, page):
    snapshot = client.get("/api/snapshot").get_json()

    assert "Free play" in page
    assert f'id="standing-time">{snapshot["sim_time"]:.0f}<' in page


def test_the_page_follows_a_loaded_scenario_and_free_play_unloads_it(client):
    client.post("/api/scenario/load", json={"scenario": "pump_trip"})

    loaded = squash(client.get("/").get_data(as_text=True))
    assert 'data-mode="scenario"' in loaded
    assert "Scenario: Falling vessel level" in loaded
    assert "Loaded, not started" in loaded

    assert client.post("/api/scenario/unload").status_code == 200

    free = client.get("/").get_data(as_text=True)
    assert 'data-mode="free_play"' in free


def test_the_base_template_gives_every_page_the_tokens_and_the_header(page):
    assert "css/tokens.css" in page
    assert 'class="site-header"' in page
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
    request = run_js("return L.buildLoadRequest(data.id);", id="pump_trip")

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
    client.post("/api/scenario/load", json={"scenario": "pump_trip"})
    client.post("/api/scenario/start")

    refused = client.post("/api/scenario/load", json={"scenario": "blocked_drain"})
    text = run_js("return L.refusalText(data.status);", status=refused.status_code)

    assert refused.status_code == 409
    assert "running" in text


@needs_node
def test_phase_labels_cover_every_runner_phase():
    labels = run_js("return ['idle','loaded','running','complete','aborted'].map(L.phaseLabel);")

    assert labels == ["", "Loaded, not started", "Running", "Finished", "Aborted"]
