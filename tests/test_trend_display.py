"""Trend display (T17-4): static/js/trends.js and static/css/trends.css.

There is no JavaScript test runner in this repo, so the pure half of trends.js
runs under Node (a subprocess, JSON in and out) and is fed responses recorded
from a real `TrainingSession` through the real trend and alarm blueprints. The
request trends.js builds is sent to the real trend endpoint, and the glue runs
over a minimal DOM whose fetch replays those recordings.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from app import config
from app.api.alarms import create_alarm_blueprint
from app.api.trend import create_trend_blueprint
from app.envelope.evaluator import Severity, isa_band
from app.scenarios.runner import ScenarioLibrary
from app.training.session import TrainingSession
from tests.test_alarm_console import Plant
from tests.test_console_tokens import THEMES, contrast, load_symbols, load_tokens
from tests.test_training_session import SCENARIO

ROOT = Path(__file__).resolve().parent.parent
TRENDS_JS = ROOT / "static" / "js" / "trends.js"
TRENDS_CSS = ROOT / "static" / "css" / "trends.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

LEVEL = "V-101.level"
# Every point with evaluated limits, then PIC-101's PV and SP. K-101's and
# P-101's are solved points the engine publishes on each machine's row (T9-5).
DEFAULTS = ["K-101.outlet_pressure", "P-101.flow", LEVEL, "PIC-101.pv", "PIC-101.sp"]
STEPS = 40


def run_js(body, **data):
    """Evaluate `body` (the body of an async function with `T`, the trends.js
    exports, and `data` in scope) under Node; return its JSON result."""
    script = (
        f"const T = require({json.dumps(str(TRENDS_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(
        ["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def session(tmp_path):
    (tmp_path / "pump-trip.yaml").write_text(SCENARIO)
    made = TrainingSession(ScenarioLibrary(scenarios=tmp_path))

    yield made

    made.end()


@pytest.fixture
def client(session):
    app = Flask(__name__)
    app.register_blueprint(
        create_trend_blueprint(session.trend_points, session.trend_history, session.trend_limits, session.trend_descriptors, session.trend_units)
    )
    app.register_blueprint(create_alarm_blueprint(session.alarm_entries, session.acknowledge))

    return app.test_client()


def step(session, count=STEPS):
    for _ in range(count):
        assert session.training_scheduler.step_once() is not None

    return session.operator_view(session.training_scheduler.snapshot())


def record(client):
    """Everything the glue fetches for the plant as it stands: the points
    listing, every point's full retained series and the alarm record."""
    listing = client.get("/api/trend/points").get_json()
    series = {}

    for first in range(0, len(listing["points"]), config.TREND_MAX_TAGS):
        tags = ",".join(listing["points"][first:first + config.TREND_MAX_TAGS])
        series.update(client.get(f"/api/trend?tags={tags}&max_points={config.TREND_MAX_POINTS}").get_json())

    return {"points": listing, "series": series, "alarms": client.get("/api/alarms/history").get_json()}


@pytest.fixture
def free(session, client):
    snapshot = step(session)

    return {"snapshot": snapshot, **record(client)}


# ---- Selection, window and request ----


@needs_node
def test_the_default_pens_are_the_limited_points_then_each_loops_pv_and_sp(free):
    chosen = run_js(
        "return T.defaultSelection(data.points.points, data.points.limits, data.snapshot.controllers, data.points.max_tags);",
        **free,
    )

    assert chosen == DEFAULTS


@needs_node
def test_the_default_pens_never_exceed_the_servers_bound(free):
    chosen = run_js(
        "return T.defaultSelection(data.points.points, data.points.limits, data.snapshot.controllers, 2);", **free,
    )

    assert chosen == DEFAULTS[:2]


@needs_node
def test_a_selection_keeps_only_points_the_plant_publishes_without_repeats(free):
    chosen = run_js(
        "return T.sanitizeSelection([data.a, 'GONE.x', data.a, data.b], data.points.points, 8);",
        a=LEVEL, b="N-101.pressure", **free,
    )

    assert chosen == [LEVEL, "N-101.pressure"]


@needs_node
def test_a_span_outside_the_offered_ones_falls_back_to_the_default():
    assert run_js("return [T.sanitizeSpan(5), T.sanitizeSpan(7), T.sanitizeSpan('x')];") == [5, 10, 10]
    assert run_js("return T.SPANS;") == [1, 5, 10, 30]
    assert max(run_js("return T.SPANS;")) * 60 <= config.TREND_CAPACITY * config.TREND_SAMPLE_PERIOD_SECONDS


@needs_node
def test_the_window_ends_at_the_latest_time_and_reaches_the_span_back():
    assert run_js("return T.windowFor(600, 5);") == {"from": 300, "to": 600}


@needs_node
def test_the_window_never_starts_before_simulated_time_zero():
    assert run_js("return T.windowFor(90, 10);") == {"from": 0, "to": 90}
    assert run_js("return T.windowFor(0, 10);") == {"from": 0, "to": 0}


@needs_node
def test_the_request_the_display_builds_is_accepted_by_the_real_endpoint(session, client):
    step(session, 90)
    sim_time = session.training_scheduler.snapshot().sim_time
    url = run_js(
        "return T.buildTrendUrl(data.tags, T.windowFor(data.t, 1), T.pointsFor(data.width, data.max_points));",
        tags=[LEVEL, "PIC-101.pv"], t=sim_time, width=300, max_points=config.TREND_MAX_POINTS,
    )

    response = client.get(url)

    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {LEVEL, "PIC-101.pv"}
    assert all(sim_time - 60 <= t <= sim_time for t, _ in body[LEVEL])
    assert 2 <= len(body[LEVEL]) <= 300


@needs_node
def test_the_requested_point_count_follows_the_plot_width_within_the_servers_bound():
    assert run_js("return [T.pointsFor(1, 2000), T.pointsFor(333.4, 2000), T.pointsFor(99999, 2000)];") == [2, 333, 2000]
    assert run_js("return T.pointsFor(900, 500);") == 500


# ---- Scaling ----


@needs_node
def test_a_pen_is_scaled_to_its_samples_and_limits_padded_five_percent():
    scale = run_js("return T.scaleFor([[0, 10], [1, 20], [2, null]], {warning_hi: 30});")

    assert scale == pytest.approx({"lo": 10 - 1.0, "hi": 30 + 1.0})


@needs_node
def test_a_flat_pen_gets_a_visible_range():
    assert run_js("return T.scaleFor([[0, 200], [1, 200]], {});") == pytest.approx({"lo": 190, "hi": 210})
    assert run_js("return T.scaleFor([[0, 0.5], [1, 0.5]], {});") == pytest.approx({"lo": -0.5, "hi": 1.5})


@needs_node
def test_float_noise_on_a_steady_pen_is_not_magnified_into_a_trend():
    scale = run_js("return T.scaleFor([[0, 200.0000019], [1, 200.0000018]], {});")

    assert scale["hi"] - scale["lo"] == pytest.approx(20, abs=0.01)
    assert run_js("return T.scaleFor([[0, 0.5], [1, 0.51]], {});")["hi"] < 0.52


@needs_node
def test_a_pen_with_nothing_to_scale_still_has_a_range():
    assert run_js("return T.scaleFor([[0, null]], {});") == {"lo": 0, "hi": 1}


@needs_node
def test_ticks_are_round_numbers_inside_the_range():
    ticks = run_js("return T.ticksFor(0.07, 0.93, 4);")

    assert ticks == pytest.approx([0.2, 0.4, 0.6, 0.8])


# ---- Bands ----


@needs_node
def test_bands_fill_each_bound_outward_to_the_next_and_label_every_rule(free):
    limits = free["points"]["limits"][LEVEL]

    shading = run_js("return T.bandsFor(data.limits, {lo: 0, hi: 1});", limits=limits)

    assert shading["bands"] == pytest.approx([
        {"severity": "warning", "lo": 0.8, "hi": 0.9},
        {"severity": "trip", "lo": 0.9, "hi": 1.0},
        {"severity": "warning", "lo": 0.1, "hi": 0.2},
        {"severity": "trip", "lo": 0.0, "hi": 0.1},
    ])
    rules = sorted(shading["rules"], key=lambda r: r["value"])
    assert [(r["label"], r["severity"]) for r in rules] == [
        ("LOLO", "trip"), ("LO", "warning"), ("HI", "warning"), ("HIHI", "trip"),
    ]
    assert [r["value"] for r in rules] == pytest.approx([0.1, 0.2, 0.8, 0.9])


@needs_node
def test_the_rule_labels_are_named_by_position_like_the_alarm_messages():
    shading = run_js(
        "return T.bandsFor({warning_hi: 5, trip_hi: 7, warning_lo: 3, trip_lo: 1}, {lo: 0, hi: 8});",
    )

    by_value = {r["value"]: r["label"] for r in shading["rules"]}
    assert by_value == {1: "LOLO", 3: "LO", 5: "HI", 7: "HIHI"}


@needs_node
def test_an_alarm_rated_outer_limit_reads_the_same_label_as_a_trip_rated_one():
    shading = run_js("return T.bandsFor({warning_hi: 5, alarm_hi: 7}, {lo: 0, hi: 8});")

    assert {r["value"]: r["label"] for r in shading["rules"]} == {5: "HI", 7: "HIHI"}


@needs_node
def test_a_bound_off_screen_draws_no_rule_but_its_band_still_reaches_in():
    shading = run_js("return T.bandsFor({warning_hi: 0.5}, {lo: 0.6, hi: 1});")

    assert shading["rules"] == []
    assert shading["bands"] == pytest.approx([{"severity": "warning", "lo": 0.6, "hi": 1.0}])


@needs_node
def test_a_point_without_limits_has_no_bands():
    assert run_js("return T.bandsFor(undefined, {lo: 0, hi: 1});") == {"bands": [], "rules": []}


def svg_numbers(svg, element, attribute):
    return [float(v) for v in re.findall(rf"<{element}\b[^>]*?\b{attribute}=\"(-?[\d.]+)\"", svg)]


def render(pens, **extra):
    return run_js(
        "return T.renderChart({width: data.width, height: 260, window: data.window, pens: data.pens,"
        " limits: data.limits, focus: data.focus, markers: data.markers});",
        **{"width": 640, "window": {"from": 0, "to": 100}, "limits": {}, "focus": pens[0]["point"],
           "markers": [], "pens": pens, **extra},
    )


@needs_node
def test_a_band_edge_lines_up_with_the_axis_value_it_marks(free):
    limits = {LEVEL: free["points"]["limits"][LEVEL]}
    # A pen that sits exactly on the warning-high limit, so its drawn height is
    # where that value falls on the axis.
    pen = {"point": LEVEL, "samples": [[0, 0.8], [100, 0.8]]}

    svg = render([pen], limits=limits)

    pen_y = float(re.search(r'class="trend-pen"[^>]*d="M[\d.]+ ([\d.]+)', svg).group(1))
    rule_y = float(re.search(r'class="trend-rule" data-severity="warning" x1="[\d.]+" x2="[\d.]+" y1="([\d.]+)"', svg).group(1))
    band = re.search(r'class="trend-band" data-severity="warning" x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"', svg)

    assert rule_y == pytest.approx(pen_y, abs=0.01)
    # The warning band above the limit starts at the limit: its bottom edge is the rule.
    assert float(band.group(1)) + float(band.group(2)) == pytest.approx(pen_y, abs=0.02)


@needs_node
def test_the_bands_belong_to_the_focused_pen_only(free):
    limits = {LEVEL: free["points"]["limits"][LEVEL]}
    pens = [
        {"point": "PIC-101.pv", "samples": [[0, 200], [100, 210]]},
        {"point": LEVEL, "samples": [[0, 0.5], [100, 0.6]]},
    ]

    assert 'class="trend-band"' not in render(pens, limits=limits, focus="PIC-101.pv")
    assert 'class="trend-band"' in render(pens, limits=limits, focus=LEVEL)


@needs_node
def test_a_pen_is_a_numbered_dash_pattern_and_the_focused_one_is_marked():
    pens = [{"point": f"X.p{i}", "samples": [[0, i], [100, i + 1]]} for i in range(4)]

    svg = render(pens, focus="X.p2")

    assert len(re.findall(r'class="trend-pen"', svg)) == 4
    assert svg.count('data-focus="true"') == 1
    assert 'data-pen="3" data-focus="true"' in svg
    dashes = re.findall(r'class="trend-pen"[^>]*?stroke-dasharray="([^"]*)"', svg)
    assert len(set(dashes)) == len(dashes) == 3
    assert run_js("return T.DASHES.length;") >= config.TREND_MAX_TAGS
    assert len(set(run_js("return T.DASHES;"))) == run_js("return T.DASHES.length;")


@needs_node
def test_pen_numbers_sit_in_the_right_margin_and_never_overlap():
    pens = [{"point": f"X.p{i}", "samples": [[0, 5], [100, 5]]} for i in range(4)]

    svg = render(pens)

    numbers = re.findall(r'class="trend-pen-number" data-pen="\d" x="([\d.]+)" y="([\d.]+)"', svg)
    assert len(numbers) == 4
    assert all(float(x) >= 640 - 22 for x, _ in numbers)
    ys = sorted(float(y) for _, y in numbers)
    assert all(b - a >= 11 for a, b in zip(ys, ys[1:]))


@needs_node
def test_pen_numbers_pushed_past_the_plot_bottom_are_packed_back_inside_it():
    pens = [{"point": f"X.p{i}", "samples": [[0, 0], [100, 0]]} for i in range(3)]
    pens += [{"point": "X.low", "samples": [[0, 1], [100, 0]]}]

    svg = render(pens, focus="X.low")

    ys = sorted(float(y) for y in re.findall(r'class="trend-pen-number" data-pen="\d" x="[\d.]+" y="([\d.]+)"', svg))
    assert max(ys) <= 260 - 26
    assert all(b - a >= 11 - 0.01 for a, b in zip(ys, ys[1:]))


@needs_node
def test_a_time_label_that_would_be_clipped_at_an_edge_is_not_drawn():
    svg = render([{"point": "X.p", "samples": [[0, 1], [100, 2]]}], width=320, window={"from": 0, "to": 100})

    for x in svg_numbers(svg, "text", "x"):
        assert 0 <= x <= 320 - 22 + 4


@needs_node
def test_a_null_reading_breaks_the_line_instead_of_bridging_it():
    path = run_js(
        "return T.pathFor([[0, 1], [1, null], [2, 3], [3, 4]], t => t * 10, v => v * 10);",
    )

    assert path == "M0 10 M20 30 L30 40"


@needs_node
def test_a_drawing_with_no_samples_is_still_valid():
    svg = render([{"point": "X.p", "samples": []}])

    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'd=""' in svg


# ---- Alarm markers ----


@needs_node
def test_markers_are_the_raised_alarms_inside_the_window():
    plant = Plant()
    plant.set_band("K-101", "discharge pressure", Severity.ALARM, at=50.0)
    plant.set_band("K-101", "discharge pressure", Severity.NORMAL, at=60.0)
    plant.set_band("V-101", "level", Severity.TRIP, side="lo", at=500.0)
    entries = plant.entries()

    inside = run_js("return T.markersFor(data.entries, {from: 0, to: 100});", entries=entries)
    later = run_js("return T.markersFor(data.entries, {from: 400, to: 600});", entries=entries)

    assert [(m["time"], m["tag"]) for m in inside] == [(50.0, "K-101")]
    assert [(m["time"], m["tag"]) for m in later] == [(500.0, "V-101")]
    assert {entry["type"] for entry in entries} == {"alarm", "clear"}


@needs_node
def test_an_alarm_without_a_known_priority_is_drawn_as_low():
    entries = [{"type": "alarm", "id": "a", "sim_time": 5, "tag": "X", "message": "m", "priority": None}]

    assert run_js("return T.markersFor(data.entries, {from: 0, to: 10});", entries=entries)[0]["priority"] == "low"


@needs_node
def test_a_marker_is_a_full_height_glyph_with_an_accessible_label():
    marker = {"time": 50, "priority": "critical", "tag": "K-101", "message": "K-101 discharge pressure HIHI"}

    svg = render([{"point": "X.p", "samples": [[0, 1], [100, 2]]}], markers=[marker])

    assert 'class="trend-marker" data-priority="critical" role="img"' in svg
    assert 'aria-label="critical alarm at 00:00:50: K-101 discharge pressure HIHI"' in svg
    assert "▲" in svg
    group = re.search(r'<g class="trend-marker".*?</g>', svg).group(0)
    y1, y2 = svg_numbers(group, "line", "y1")[0], svg_numbers(group, "line", "y2")[0]
    assert y2 - y1 == pytest.approx(260 - 16 - 26)


@needs_node
def test_the_marker_glyphs_are_the_alarm_symbols_of_the_token_file():
    symbols = load_symbols()

    assert run_js("return T.PRIORITY_GLYPH;") == {
        "critical": symbols["--symbol-alarm-critical"],
        "high": symbols["--symbol-alarm-high"],
        "low": symbols["--symbol-alarm-low"],
    }


# ---- Phone width ----


@needs_node
@pytest.mark.parametrize("width", [280, 320, 375, 768, 1280])
def test_everything_drawn_stays_inside_the_view_box_at_any_width(free, width):
    limits = {LEVEL: free["points"]["limits"][LEVEL]}
    pens = [{"point": LEVEL, "samples": [[0, 0.8], [50, 0.5], [100, 0.1]]}]
    marker = {"time": 100, "priority": "high", "tag": "V-101", "message": "V-101 level LOLO"}

    svg = render(pens, width=width, limits=limits, markers=[marker])

    assert f'viewBox="0 0 {width} 260"' in svg
    for attribute in ("x", "x1", "x2"):
        assert all(-0.5 <= v <= width + 0.5 for e in ("rect", "line", "text") for v in svg_numbers(svg, e, attribute))
    for attribute in ("y", "y1", "y2"):
        assert all(-0.5 <= v <= 260 + 0.5 for e in ("rect", "line", "text") for v in svg_numbers(svg, e, attribute))
    plot = re.search(r'class="trend-plot" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg)
    assert float(plot.group(2)) >= 150


def test_the_chart_scales_to_its_container_and_the_legend_wraps():
    css = TRENDS_CSS.read_text()

    assert re.search(r"\.trend-svg\s*\{[^}]*width:\s*100%;[^}]*height:\s*auto", css)
    assert re.search(r"\.trend-legend\s*\{[^}]*repeat\(auto-fill,\s*minmax\(min\(100%,", css)
    assert re.search(r"\.trend-selectors\s*\{[^}]*flex-wrap:\s*wrap", css)
    assert re.search(r"\.trend-pen-point\s*\{[^}]*text-overflow:\s*ellipsis", css)


# ---- Themes ----


def test_the_stylesheet_uses_tokens_only():
    css = TRENDS_CSS.read_text()
    defined = set(re.findall(r"(--[\w-]+)\s*:", TOKENS_CSS.read_text()))
    defined |= set(re.findall(r"(--[\w-]+)\s*:", css))

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)
    assert set(re.findall(r"var\((--[\w-]+)\)", css)) <= defined


def test_the_script_hard_codes_no_colour():
    script = TRENDS_JS.read_text()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", script)
    assert not re.search(r"\b(rgb|hsl)a?\(", script)


@pytest.mark.parametrize("theme", THEMES)
def test_every_drawn_colour_is_legible_in_both_themes(theme):
    t = load_tokens()[theme]
    ground = t["--surface-raised"]

    for text in ("--text", "--text-muted"):
        assert contrast(t[text], ground) >= 4.5, (theme, text)
    # Rule labels are the only text drawn over a tint, and they use --text.
    for tint in ("warning", "alarm", "trip"):
        assert contrast(t["--text"], t[f"--band-{tint}-tint"]) >= 4.5, (theme, tint)
    for graphic in ("--focus-ring", "--text-muted", "--border"):
        assert contrast(t[graphic], ground) >= 3.0, (theme, graphic)
    for priority in ("critical", "high", "low"):
        assert contrast(t[f"--alarm-{priority}-mark"], ground) >= 3.0, (theme, priority)
        for tint in ("warning", "alarm", "trip"):
            assert contrast(t[f"--alarm-{priority}-mark"], t[f"--band-{tint}-tint"]) >= 1.5, (theme, priority, tint)


def test_the_console_loads_the_trend_after_its_tokens_and_mounts_it_on_the_stream():
    page = (ROOT / "templates" / "console.html").read_text()

    assert "css/trends.css" in page and "js/trends.js" in page
    assert "Trends.mount(" in page
    assert re.search(r"try \{ trends\.update\(snapshot\); \} catch", page)


# ---- The browser glue, over a minimal DOM ----

FAKE_DOM = """
class Node {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.children = [];
    this.listeners = {}; this._html = ''; this._text = ''; this.ownerDocument = doc; this.clientWidth = 640; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { this.children.push(c); return c; }
  set innerHTML(h) { this._html = String(h); this.sets = (this.sets || 0) + 1; }
  get innerHTML() { return this._html; }
  set textContent(t) { this._text = String(t); }
  get textContent() { return this._text; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  // Just the live values of a legend: a node whose text can be rewritten in place.
  querySelectorAll(sel) {
    if (sel === '[data-focus-point]') {
      return [...this._html.matchAll(/data-focus-point="([^"]*)"/g)].map(m => ({
        getAttribute: a => (a === 'data-focus-point' ? m[1] : null),
        focus: () => focusLog.push(m[1]),
      }));
    }
    if (sel !== '[data-live-value]') return [];
    const host = this;
    const found = [...this._html.matchAll(/data-live-value data-value-point="([^"]*)"/g)].map(m => m[1]);
    return found.map(point => ({
      getAttribute: a => (a === 'data-value-point' ? point : null),
      set textContent(text) {
        host._html = host._html.replace(new RegExp('(data-value-point="' + point + '">)[^<]*'), '$1' + text);
      },
    }));
  }
  async fire(type, event) { for (const fn of this.listeners[type] || []) await fn(event); }
}
const focusLog = [];
const doc = { createElement: tag => new Node(tag) };
const container = new Node('div');
const part = cls => container.children.find(c => c.attrs['class'] === cls);

const storageData = {};
const storage = { getItem: k => (k in storageData ? storageData[k] : null), setItem: (k, v) => { storageData[k] = v; } };
if (data.stored) storageData['plant-simulator.trends'] = JSON.stringify(data.stored);

let served = data.served;
const calls = [];
const fetch = async url => {
  calls.push(url);
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  const json = body => ({ ok: true, status: 200, json: async () => body });
  if (path === '/api/trend/points') return json(served.points);
  if (path === '/api/alarms/history') return json(served.alarms);
  if (path === '/api/trend') {
    const from = Number(params.get('from')), to = Number(params.get('to'));
    const out = {};
    for (const tag of params.get('tags').split(',')) {
      out[tag] = (served.series[tag] || []).filter(([t]) => t >= from && t <= to);
    }
    return json(out);
  }
  return { ok: false, status: 404, json: async () => ({}) };
};
const trends = T.mount(container, { fetch, storage, width: 640, pollMs: 0 });
const chart = () => part('trend-chart').innerHTML;
const legend = () => part('trend-legend-box').innerHTML;
const selectors = () => part('trend-controls');
const pens = () => (legend().match(/data-focus-point="[^"]*"/g) || [])
  .map(m => m.match(/data-focus-point="([^"]*)"/)[1]);
const pathsOf = () => Object.fromEntries((chart().match(/<path class="trend-pen"[^>]*>/g) || [])
  .map(m => [m.match(/data-point="([^"]*)"/)[1], m.match(/ d="([^"]*)"/)[1]]));
const click = (host, attr, value) => host.fire('click', { target: { closest: sel =>
  sel === '[' + attr + ']' ? { getAttribute: () => value } : null } });
const change = (host, attr, value) => host.fire('change', { target: {
  value, getAttribute: a => (a === attr ? '' : null) } });
const start = async () => { await trends.ready; await trends.update(data.snapshot); };
"""


def run_glue(body, **data):
    return run_js(FAKE_DOM + body, **data)


def served(free):
    return {key: free[key] for key in ("points", "series", "alarms")}


@needs_node
def test_the_first_snapshot_draws_the_default_pens_with_their_bands(free):
    result = run_glue(
        """
        await start();
        return { pens: pens(), chart: chart(), legend: legend(), calls };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result["pens"] == DEFAULTS
    assert 'class="trend-band"' in result["chart"]
    assert "HI 330" in result["chart"] and "HIHI 350" in result["chart"]
    assert result["legend"].count("trend-pen-row") == len(DEFAULTS)
    assert any(call.startswith(f"/api/trend?tags={','.join(DEFAULTS)}&from=") for call in result["calls"])


@needs_node
def test_a_timer_tick_asks_for_the_list_the_window_and_the_alarm_record_even_when_paused(free):
    result = run_glue(
        """
        await start();
        calls.length = 0;
        await trends.poll();
        return calls.map(c => c.split('?')[0]).sort();
        """,
        served=served(free), snapshot={**free["snapshot"], "running": False},
    )

    assert result == ["/api/alarms/history", "/api/trend", "/api/trend/points"]


@needs_node
def test_a_hidden_tab_makes_no_requests(free):
    result = run_glue(
        """
        await start();
        calls.length = 0;
        globalThis.document = { hidden: true };
        await trends.poll();
        const hidden = calls.length;
        globalThis.document = { hidden: false };
        await trends.poll();
        delete globalThis.document;
        return { hidden, visible: calls.length };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result == {"hidden": 0, "visible": 3}


@needs_node
def test_a_swap_that_leaves_a_paused_plants_time_unchanged_still_replaces_the_samples(free):
    swapped = json.loads(json.dumps(served(free)))
    swapped["series"][LEVEL] = [[t, v + 0.1] for t, v in swapped["series"][LEVEL]]

    result = run_glue(
        """
        await start();
        const before = pathsOf();
        served = data.swapped;
        await trends.poll();
        return { before, after: pathsOf() };
        """,
        served=served(free), swapped=swapped, snapshot={**free["snapshot"], "running": False},
    )

    assert result["before"][LEVEL] != result["after"][LEVEL]


@needs_node
def test_a_history_swap_replaces_every_drawn_sample(session, client, free):
    session.load("pump-trip")
    step(session, 15)
    loaded = record(client)

    result = run_glue(
        """
        await start();
        const before = pathsOf();
        served = data.after;
        await trends.update({ ...data.snapshot, sim_time: data.after_time });
        await trends.refresh();
        return { before, after: pathsOf() };
        """,
        served=served(free), after=served(loaded), snapshot=free["snapshot"],
        after_time=session.training_scheduler.snapshot().sim_time,
    )

    assert result["before"][LEVEL] != result["after"][LEVEL]
    assert result["after"][LEVEL].count("M") == 1


@needs_node
def test_removing_adding_focusing_and_the_span_persist_in_storage(free):
    result = run_glue(
        """
        await start();
        await click(part('trend-legend-box'), 'data-remove-point', 'PIC-101.sp');
        const afterRemove = pens();
        await change(selectors(), 'data-add', 'N-101.pressure');
        await change(selectors(), 'data-span', '5');
        await click(part('trend-legend-box'), 'data-focus-point', 'PIC-101.pv');
        return { afterRemove, pens: pens(), stored: JSON.parse(storageData['plant-simulator.trends']),
                 url: calls.filter(c => c.startsWith('/api/trend?')).pop() };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result["afterRemove"] == DEFAULTS[:-1]
    assert result["pens"] == [*DEFAULTS[:-1], "N-101.pressure"]
    assert result["stored"] == {"span": 5, "selection": result["pens"], "focus": "PIC-101.pv"}
    assert "from=" in result["url"]
    sim_time = free["snapshot"]["sim_time"]
    assert f"from={int(sim_time) - 300}&to={int(sim_time)}&" in result["url"]


@needs_node
def test_a_stored_selection_comes_back_and_unknown_points_are_dropped(free):
    result = run_glue(
        """
        await start();
        return pens();
        """,
        served=served(free), snapshot=free["snapshot"],
        stored={"span": 30, "selection": ["N-101.pressure", "GONE.x", LEVEL], "focus": LEVEL},
    )

    assert result == ["N-101.pressure", LEVEL]


@needs_node
def test_an_emptied_selection_stays_empty_and_says_so(free):
    result = run_glue(
        """
        await start();
        for (const point of pens()) await click(part('trend-legend-box'), 'data-remove-point', point);
        return { chart: chart(), pens: pens() };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result["pens"] == []
    assert "No pens selected" in result["chart"]


@needs_node
def test_the_pen_bound_comes_from_the_server_and_disables_adding_at_the_limit(free):
    points = {**free["points"], "max_tags": 3}
    result = run_glue(
        """
        await start();
        return selectors().innerHTML;
        """,
        served={**served(free), "points": points}, snapshot=free["snapshot"],
    )

    assert "Pen limit reached (3)" in result
    assert "data-add disabled" in result


@needs_node
def test_a_failing_endpoint_is_reported_and_the_display_survives(free):
    result = run_glue(
        """
        const host = new Node('div');
        const broken = T.mount(host, { fetch: async () => ({ ok: false, status: 500, json: async () => ({}) }),
          storage: null, width: 640, pollMs: 0 });
        await broken.ready;
        await broken.update(data.snapshot);
        return host.children.find(c => c.attrs['class'] === 'trend-status').textContent;
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result == "Trend unavailable: HTTP 500"


@needs_node
def test_storage_that_throws_does_not_stop_the_display(free):
    result = run_glue(
        """
        const hostile = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
        const host = new Node('div');
        const shown = T.mount(host, { fetch, storage: hostile, width: 640, pollMs: 0 });
        await shown.ready;
        await shown.update(data.snapshot);
        await click(host.children[1], 'data-remove-point', 'PIC-101.sp');
        return host.children.find(c => c.attrs['class'] === 'trend-chart').innerHTML.includes('trend-pen');
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result is True


@needs_node
def test_a_tick_rebuilds_neither_a_selector_nor_the_legend_the_operator_may_be_using(free):
    moved = json.loads(json.dumps(served(free)))
    moved["series"][LEVEL] = [[t, 0.25] for t, _ in moved["series"][LEVEL]]

    result = run_glue(
        """
        await start();
        const before = { selectors: selectors().sets, legend: part('trend-legend-box').sets };
        const shown = legend();
        served = data.moved;
        await trends.poll();
        return { before, after: { selectors: selectors().sets, legend: part('trend-legend-box').sets },
                 shown, now: legend() };
        """,
        served=served(free), moved=moved, snapshot=free["snapshot"],
    )

    assert result["after"] == result["before"]
    assert result["now"] != result["shown"]
    assert re.search(r'data-value-point="V-101.level">0.25<', result["now"])


@needs_node
def test_changing_the_focus_or_a_pen_does_rebuild_the_legend(free):
    result = run_glue(
        """
        await start();
        const before = part('trend-legend-box').sets;
        await click(part('trend-legend-box'), 'data-focus-point', 'PIC-101.pv');
        return { before, after: part('trend-legend-box').sets, legend: legend() };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result["after"] == result["before"] + 1
    assert re.search(r'data-pen="4" data-focus="true"', result["legend"])


@needs_node
def test_a_scenario_alarm_shows_as_a_marker_on_the_trend(session, client):
    session.load("pump-trip")
    session.start()
    snapshot = step(session, 120)
    recorded = record(client)

    result = run_glue(
        """
        await start();
        return chart();
        """,
        served=served(recorded), snapshot=snapshot,
    )

    raised = [entry for entry in recorded["alarms"] if entry["type"] == "alarm"]
    assert result.count('class="trend-marker"') == len(
        [e for e in raised if snapshot["sim_time"] - 600 <= e["sim_time"] <= snapshot["sim_time"]]
    )


@needs_node
def test_keyboard_focus_goes_back_to_the_legend_after_a_press_rebuilds_it(free):
    result = run_glue(
        """
        await start();
        await click(part('trend-legend-box'), 'data-focus-point', 'PIC-101.pv');
        const afterFocus = [...focusLog];
        await click(part('trend-legend-box'), 'data-remove-point', 'PIC-101.pv');
        return { afterFocus, afterRemove: [...focusLog] };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result["afterFocus"] == ["PIC-101.pv"]
    assert result["afterRemove"][-1] == "PIC-101.sp"


@needs_node
def test_a_tick_while_the_last_one_is_still_waiting_is_skipped_so_the_display_still_draws(free):
    result = run_glue(
        """
        await start();
        let sent = 0;
        const slowFetch = async url => { sent += 1; await new Promise(r => setTimeout(r, 30)); return fetch(url); };
        const host = new Node('div');
        const shown = T.mount(host, { fetch: slowFetch, storage: null, width: 640, pollMs: 0 });
        await shown.ready;
        await shown.update(data.snapshot);
        sent = 0;
        const first = shown.poll();
        const second = shown.poll();
        await Promise.all([first, second]);
        return { sent, drawn: host.children.find(c => c.attrs['class'] === 'trend-chart').innerHTML.includes('trend-pen') };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result == {"sent": 3, "drawn": True}


@needs_node
def test_the_chart_keeps_the_window_its_data_was_fetched_for(free):
    result = run_glue(
        """
        await start();
        const drawn = chart();
        await trends.update({ ...data.snapshot, sim_time: data.snapshot.sim_time + 500 });
        // Something else draws (a focus press) after the clock has moved.
        await click(part('trend-legend-box'), 'data-focus-point', 'PIC-101.pv');
        // The time axis only: the value axis follows whichever pen is focused.
        const ticks = html => JSON.stringify(html.match(/<text class="trend-tick"[^>]*text-anchor="middle">[^<]*<\\/text>/g));
        const paths = html => JSON.stringify((html.match(/<path class="trend-pen"[^>]*>/g) || [])
          .map(m => [m.match(/data-point="([^"]*)"/)[1], m.match(/ d="([^"]*)"/)[1]]).sort());
        return { sameTicks: ticks(chart()) === ticks(drawn), samePaths: paths(chart()) === paths(drawn),
                 hasTicks: ticks(drawn).length > 10 };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result == {"sameTicks": True, "samePaths": True, "hasTicks": True}


@needs_node
def test_a_request_that_never_settles_times_out_and_the_next_tick_recovers(free):
    result = run_glue(
        """
        await start();
        let hang = true;
        const flaky = url => (hang ? new Promise(() => {}) : fetch(url));
        const host = new Node('div');
        const shown = T.mount(host, { fetch: flaky, storage: null, width: 640, pollMs: 0, timeoutMs: 40 });
        await shown.update(data.snapshot);
        await shown.poll();
        const status = host.children.find(c => c.attrs['class'] === 'trend-status').textContent;
        hang = false;
        await shown.poll();
        return { status, drawn: host.children.find(c => c.attrs['class'] === 'trend-chart').innerHTML.includes('trend-pen') };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result == {"status": "Trend unavailable: timed out", "drawn": True}


@needs_node
def test_a_span_change_before_the_point_list_arrives_is_kept_and_a_pen_edit_is_turned_away(free):
    result = run_glue(
        """
        const host = new Node('div');
        const early = T.mount(host, { fetch: () => new Promise(() => {}), storage, width: 640, pollMs: 0, timeoutMs: 20 });
        await change(host.children[0], 'data-span', '5');
        const afterSpan = JSON.parse(storageData['plant-simulator.trends']);
        const setsBefore = host.children[0].sets;
        await change(host.children[0], 'data-add', 'N-101.pressure');
        return { afterSpan, snapped: host.children[0].sets === setsBefore + 1,
                 stored: JSON.parse(storageData['plant-simulator.trends']) };
        """,
        served=served(free), snapshot=free["snapshot"],
        stored={"span": 30, "selection": [LEVEL], "focus": LEVEL},
    )

    assert result["afterSpan"] == {"span": 5, "selection": [LEVEL], "focus": LEVEL}
    assert result["snapped"] is True
    assert result["stored"] == result["afterSpan"]


@needs_node
def test_a_tab_that_comes_back_to_the_front_refreshes_at_once(free):
    result = run_glue(
        """
        let onChange;
        globalThis.document = { hidden: true, addEventListener: (type, fn) => { onChange = fn; },
                                removeEventListener: () => { onChange = null; } };
        const host = new Node('div');
        const shown = T.mount(host, { fetch, storage: null, width: 640, pollMs: 60000 });
        await shown.ready;
        await shown.update(data.snapshot);
        calls.length = 0;
        await onChange();
        const whileHidden = calls.length;
        document.hidden = false;
        await onChange();
        const afterShown = calls.length;
        shown.stop();
        const removed = onChange === null;
        delete globalThis.document;
        return { whileHidden, afterShown, removed };
        """,
        served=served(free), snapshot=free["snapshot"],
    )

    assert result == {"whileHidden": 0, "afterShown": 3, "removed": True}


@needs_node
def test_a_stored_selection_the_plant_no_longer_publishes_falls_back_to_the_defaults(free):
    result = run_glue(
        """
        await start();
        return pens();
        """,
        served=served(free), snapshot=free["snapshot"],
        stored={"span": 10, "selection": ["GONE.a", "GONE.b"], "focus": "GONE.a"},
    )

    assert result == DEFAULTS


@needs_node
def test_a_pen_is_named_by_its_id_and_descriptor_and_a_point_without_one_by_its_id(free):
    descriptors = free["points"]["descriptors"]

    names = run_js(
        "return [T.penName('K-101.outlet_pressure', data.d), T.penName('PIC-101.pv', data.d),"
        " T.penName('X-1.unlisted', data.d), T.penName('K-101.outlet_pressure', undefined)];",
        d=descriptors,
    )

    assert names == ["K-101 discharge pressure", "PIC-101 pv", "X-1.unlisted", "K-101.outlet_pressure"]


@needs_node
def test_the_legend_and_the_add_a_pen_list_show_the_descriptor_and_keep_the_point_id(free):
    descriptors = free["points"]["descriptors"]
    point = "K-101.outlet_pressure"

    legend, selectors = run_js(
        "return [T.renderLegend({span: 10, descriptors: data.d, selection: [data.p], focus: data.p, latest: {}}),"
        " T.renderSelectors({span: 10, points: [data.p], descriptors: data.d, selection: [], maxTags: 8})];",
        d=descriptors, p=point,
    )

    assert ">K-101 discharge pressure</span>" in legend
    assert f'title="{point}"' in legend
    assert f'data-remove-point="{point}"' in legend
    assert f'aria-label="Remove {point}"' in legend
    assert f'<option value="{point}">K-101 discharge pressure</option>' in selectors
