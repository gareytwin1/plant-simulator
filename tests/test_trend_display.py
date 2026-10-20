"""Trend rendering (T17-4, reshaped by T20-11): static/js/trends.js and the
chart rules in static/css/trends.css.

There is no JavaScript test runner in this repo, so trends.js, which is pure,
runs under Node (a subprocess, JSON in and out) and is fed responses recorded
from a real `TrainingSession` through the real trend and alarm blueprints. The
request it builds is sent to the real trend endpoint. The windows that show it
are tests/test_trend_windows.py's.
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
from app.envelope.evaluator import Severity
from app.scenarios.runner import ScenarioLibrary
from app.training.session import TrainingSession
from tests.test_alarm_console import Plant
from tests.test_console_tokens import THEMES, contrast, load_symbols, load_tokens
from tests.test_training_session import SCENARIO

pytestmark = pytest.mark.usefixtures("olefins_free_play")

ROOT = Path(__file__).resolve().parent.parent
TRENDS_JS = ROOT / "static" / "js" / "trends.js"
TRENDS_CSS = ROOT / "static" / "css" / "trends.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

LEVEL = "V-101.level"
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


# ---- Span, window and request ----


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
def test_during_a_run_the_window_never_starts_before_run_time_zero():
    assert run_js("return T.windowFor(14800, 10, 14770);") == {"from": 14770, "to": 14800}
    assert run_js("return T.windowFor(15400, 5, 14770);") == {"from": 15100, "to": 15400}
    # An origin past the latest time (a poll ahead of the snapshot) never inverts the window.
    assert run_js("return T.windowFor(14760, 10, 14770);") == {"from": 14760, "to": 14760}
    assert run_js("return T.windowFor(90, 10, null);") == {"from": 0, "to": 90}


@needs_node
def test_with_an_origin_the_axis_ticks_fall_on_round_run_times_and_read_as_run_time():
    origin = 14770.4  # not a round plant time, so round plant ticks would not be round run ticks
    pen = {"point": "X.p", "samples": [[origin, 1], [origin + 120, 2]]}
    marker = {"time": origin + 12, "priority": "high", "tag": "V-101", "message": "V-101 level LO"}

    run = render([pen], window={"from": origin, "to": origin + 120}, markers=[marker], origin=origin)
    plant = render([pen], window={"from": origin, "to": origin + 120}, markers=[marker])

    ticks = lambda svg: re.findall(r'class="trend-tick"[^>]*text-anchor="middle">([^<]*)<', svg)
    assert ticks(run) and all(re.fullmatch(r"\d{2}:\d{2}", t) and int(t[-2:]) % 10 == 0 for t in ticks(run))
    assert ticks(plant) and all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", t) for t in ticks(plant))
    assert 'aria-label="high alarm at 00:12: V-101 level LO"' in run
    assert 'aria-label="high alarm at 04:06:22: V-101 level LO"' in plant
    assert "Trend of X.p from 00:00 to 02:00" in run


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


# ---- The held scale ----


@needs_node
def test_a_held_scale_starts_as_the_pens_own_scale():
    result = run_js("return [T.holdScale(null, data.s, {}), T.scaleFor(data.s, {})];", s=[[0, 10], [1, 20]])

    assert result[0] == result[1]


@needs_node
def test_a_held_scale_widens_when_a_reading_leaves_it_and_never_narrows():
    result = run_js(
        """
        let held = T.holdScale(null, [[0, 10], [1, 20]], {});
        const first = held;
        held = T.holdScale(held, [[2, 30]], {});            // a reading above it: widens
        const widened = held;
        held = T.holdScale(held, [[3, 15], [4, 16]], {});   // a quiet stretch: does not narrow
        return { first, widened, quiet: held };
        """,
    )

    assert result["first"] == pytest.approx({"lo": 9.5, "hi": 20.5})
    # A lone reading of 30 scales to 30 +/- 5%; the held scale takes it in.
    assert result["widened"] == pytest.approx({"lo": 9.5, "hi": 31.5})
    assert result["quiet"] == result["widened"]


@needs_node
def test_a_held_scale_takes_in_the_limits_and_survives_an_empty_fetch():
    result = run_js(
        """
        const held = T.holdScale(null, [[0, 0.5]], { warning_hi: 0.8 });
        return { held, empty: T.holdScale(held, [[1, null]], {}), none: T.holdScale(held, [], undefined) };
        """,
    )

    assert result["held"]["hi"] > 0.8
    assert result["empty"] == result["held"] == result["none"]


@needs_node
def test_a_chart_drawn_on_a_held_scale_uses_it_for_the_axis():
    pens = [{"point": "X.p", "samples": [[0, 15], [100, 16]]}]

    own = render(pens)
    held = render(pens, scales={"X.p": {"lo": 0, "hi": 100}})

    labels = lambda svg: re.findall(r'class="trend-tick" x="[\d.]+" y="[\d.]+" text-anchor="end"[^>]*>([^<]*)<', svg)
    assert "100" in labels(held) and "0" in labels(held)
    assert "100" not in labels(own)


@needs_node
def test_a_chart_can_be_given_its_accessible_name():
    svg = render([{"point": "X.p", "samples": [[0, 1], [100, 2]]}], label="Trend of X p")

    assert 'aria-label="Trend of X p"' in svg


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
        " limits: data.limits, focus: data.focus, markers: data.markers, scales: data.scales, label: data.label,"
        " origin: data.origin});",
        **{"width": 640, "window": {"from": 0, "to": 100}, "limits": {}, "focus": pens[0]["point"],
           "markers": [], "pens": pens, "scales": None, "label": None, "origin": None, **extra},
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


def test_a_window_chart_fills_its_window_and_hides_the_pen_number():
    css = TRENDS_CSS.read_text()

    assert re.search(r"\.tw-chart \.trend-svg\s*\{[^}]*width:\s*100%;[^}]*height:\s*100%", css)
    assert re.search(r"\.tw-chart \.trend-pen-number\s*\{[^}]*display:\s*none", css)


# ---- Themes ----


def test_the_stylesheet_uses_tokens_only():
    css = TRENDS_CSS.read_text()
    defined = set(re.findall(r"(--[\w-]+)\s*:", TOKENS_CSS.read_text()))
    defined |= set(re.findall(r"(--[\w-]+)\s*:", css))

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)
    assert set(re.findall(r"var\((--[\w-]+)\)", css)) <= defined


@pytest.mark.parametrize("name", ["trends.js", "trendwindows.js"])
def test_the_script_hard_codes_no_colour(name):
    script = (TRENDS_JS.parent / name).read_text()

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


def test_the_console_loads_the_trend_windows_and_feeds_them_the_stream():
    page = (ROOT / "templates" / "console.html").read_text()

    assert "css/trends.css" in page
    assert page.index("js/trends.js") < page.index("js/trendwindows.js")
    assert "TrendWindows.mount(" in page
    assert re.search(r"try \{ trends\.update\(snapshot\); \} catch", page)
    # The multi-pen panel is gone.
    assert 'id="trends"' not in page and "Trends.mount(" not in page


# ---- Names ----


@needs_node
def test_a_pen_is_named_by_its_id_and_descriptor_and_a_point_without_one_by_its_id(free):
    descriptors = free["points"]["descriptors"]

    names = run_js(
        "return [T.penName('K-101.outlet_pressure', data.d), T.penName('PIC-101.pv', data.d),"
        " T.penName('X-1.unlisted', data.d), T.penName('K-101.outlet_pressure', undefined)];",
        d=descriptors,
    )

    assert names == ["K-101 discharge pressure", "PIC-101 pv", "X-1.unlisted", "K-101.outlet_pressure"]
