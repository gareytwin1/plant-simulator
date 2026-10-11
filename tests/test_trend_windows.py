"""Floating trend windows (T20-11): static/js/trendwindows.js and the window
rules in static/css/trends.css.

trendwindows.js runs under Node (a subprocess, JSON in and out). Its pure half
is run over fixed numbers; its glue over tests/fake_dom.js, fed the trend
listing, history and alarm record recorded from a real `TrainingSession`
through the real trend and alarm blueprints. It checks what a click, a key, a
tick and a snapshot do, not how anything looks, which was seen in a browser
(both themes; phone width is not checked yet).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import config
from tests.test_trend_display import LEVEL, record, step
from tests.test_trend_display import client as client  # noqa: F401 - fixtures used by name
from tests.test_trend_display import session as session  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_JS = ROOT / "static" / "js" / "trendwindows.js"
TRENDS_CSS = ROOT / "static" / "css" / "trends.css"
CONSOLE_HTML = ROOT / "templates" / "console.html"
FAKE_DOM = (ROOT / "tests" / "fake_dom.js").read_text()

NODE = "N-201.pressure"
PV = "PIC-101.pv"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(body, **data):
    """Evaluate `body` (the body of an async function with `TW`, the
    trendwindows.js exports, and `data` in scope) under Node; return its JSON."""
    script = (
        f"const TW = require({json.dumps(str(WINDOWS_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def free(session, client):  # noqa: F811
    snapshot = step(session)

    return {"snapshot": snapshot, **record(client)}


def served(recorded):
    return {key: recorded[key] for key in ("points", "series", "alarms")}


# ---- Geometry ----

BOUNDS = {"width": 1000, "height": 600}


@needs_node
def test_a_window_is_kept_no_smaller_than_its_minimum_and_no_larger_than_the_workspace():
    result = run_js(
        "return [TW.clampGeometry({x: 100, y: 100, w: 10, h: 10}, data.b), TW.clampGeometry({x: 0, y: 0, w: 5000, h: 5000}, data.b)];",
        b=BOUNDS,
    )

    assert result[0]["w"] == 260 and result[0]["h"] == 180
    assert result[1] == {"x": 8, "y": 8, "w": 984, "h": 584}


@needs_node
def test_a_windows_title_bar_never_leaves_the_workspace():
    result = run_js(
        "return [[-500, -500], [5000, 5000], [900, 590]].map(([x, y]) => TW.clampGeometry({x, y, w: 420, h: 290}, data.b));",
        b=BOUNDS,
    )

    for g in result:
        assert 8 <= g["x"] and g["x"] + g["w"] <= 1000 - 8
        # The window may hang off the bottom, but its title bar stays in view.
        assert 8 <= g["y"] <= 600 - 48


@needs_node
def test_arrow_keys_move_shift_resizes_and_alt_takes_a_fine_step():
    result = run_js(
        """
        const g = { x: 100, y: 100, w: 400, h: 300 };
        return {
          right: TW.keyGeometry(g, 'ArrowRight', false, false),
          up: TW.keyGeometry(g, 'ArrowUp', false, false),
          wider: TW.keyGeometry(g, 'ArrowRight', true, false),
          shorter: TW.keyGeometry(g, 'ArrowUp', true, false),
          fine: TW.keyGeometry(g, 'ArrowLeft', false, true),
          other: TW.keyGeometry(g, 'a', false, false),
        };
        """,
    )

    assert result["right"] == {"x": 116, "y": 100, "w": 400, "h": 300}
    assert result["up"] == {"x": 100, "y": 84, "w": 400, "h": 300}
    assert result["wider"] == {"x": 100, "y": 100, "w": 416, "h": 300}
    assert result["shorter"] == {"x": 100, "y": 100, "w": 400, "h": 284}
    assert result["fine"] == {"x": 98, "y": 100, "w": 400, "h": 300}
    assert result["other"] is None


@needs_node
def test_a_new_window_steps_off_one_already_in_its_place():
    result = run_js("return TW.cascade({x: 100, y: 100}, [{x: 100, y: 100}, {x: 128, y: 128}, {x: 400, y: 10}]);")

    assert result == {"x": 156, "y": 156}


# ---- Values, units and requests ----


@needs_node
def test_a_fraction_shows_as_a_percent_and_any_other_unit_as_it_is():
    result = run_js(
        """
        return {
          level: TW.valueParts(0.4567, 'fraction'), pressure: TW.valueParts(201.34, 'psia'),
          bare: TW.valueParts(3, ''), missing: TW.valueParts(null, 'psia'), zero: TW.valueParts(-0.01, 'psia'),
          samples: TW.displaySamples([[0, 0.5], [1, null]], 'fraction'),
          limits: TW.displayLimits({ warning_hi: 0.8 }, 'fraction'),
          kept: TW.displayLimits({ warning_hi: 330 }, 'psia'), none: TW.displayLimits(undefined, 'psia'),
        };
        """,
    )

    assert result["level"] == {"number": "45.7", "unit": "%"}
    assert result["pressure"] == {"number": "201.3", "unit": "psia"}
    assert result["bare"] == {"number": "3.0", "unit": ""}
    assert result["missing"] == {"number": "--", "unit": ""}
    assert result["zero"] == {"number": "0.0", "unit": "psia"}
    assert result["samples"] == [[0, 50], [1, None]]
    assert result["limits"] == {"warning_hi": 80}
    assert result["kept"] == {"warning_hi": 330}
    assert result["none"] is None


@needs_node
def test_one_request_serves_every_window_at_the_widest_span_with_a_point_per_pixel(session, client):  # noqa: F811
    step(session, 90)
    sim_time = session.training_scheduler.snapshot().sim_time
    plan = run_js(
        "return TW.fetchPlan([{point: data.a, span: 1, width: 300}, {point: data.b, span: 5, width: 500}], data.t, data.max);",
        a=LEVEL, b=PV, t=sim_time, max=config.TREND_MAX_POINTS,
    )

    assert plan["tags"] == [LEVEL, PV]
    assert plan["window"] == {"from": max(0, sim_time - 300), "to": sim_time}
    # The 1 minute window is a fifth of the fetch, and still gets its 226 plot pixels.
    assert "max_points=1130" in plan["url"]
    response = client.get(plan["url"])
    assert response.status_code == 200
    assert set(response.get_json()) == {LEVEL, PV}


@needs_node
def test_the_requested_points_stay_inside_the_servers_bound():
    plan = run_js("return TW.fetchPlan([{point: 'X.a', span: 1, width: 900}, {point: 'X.b', span: 30, width: 300}], 4000, 2000);")

    assert plan["url"].endswith("max_points=2000")


@needs_node
def test_a_window_is_sliced_out_of_the_shared_fetch():
    assert run_js("return TW.sliceTo([[0, 1], [50, 2], [100, 3]], {from: 40, to: 100});") == [[50, 2], [100, 3]]


@needs_node
def test_a_windows_markers_are_its_own_points_alarms_only():
    entries = [
        {"type": "alarm", "sim_time": 10, "tag": "K-101", "priority": "high", "message": "a",
         "data": {"pv": "discharge pressure", "severity": "ALARM"}},
        {"type": "alarm", "sim_time": 20, "tag": "K-101", "priority": "high", "message": "b",
         "data": {"pv": "suction pressure", "severity": "ALARM"}},
        {"type": "alarm", "sim_time": 30, "tag": "LSHH-101", "priority": "critical", "message": "c", "data": {}},
        {"type": "alarm", "sim_time": 40, "tag": "K-101", "priority": "low", "message": "d",
         "data": {"point": "K-101.outlet_pressure"}},
        {"type": "alarm", "sim_time": 50, "tag": "K-101", "priority": "low", "message": "e",
         "data": {"point": "K-101.inlet_pressure", "pv": "discharge pressure"}},
    ]

    result = run_js(
        "return TW.pointMarkers(data.e, 'K-101.outlet_pressure', 'discharge pressure', {from: 0, to: 100}).map(m => m.message);",
        e=entries,
    )

    assert result == ["a", "d"]


# ---- The glue, over a fake DOM ----

SCENE = (
    FAKE_DOM
    + """
const W = { left: 100, top: 50, width: 1000, height: 600 };
const rect = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h });
function make(tag, attrs, parent) {
  const n = doc.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
  if (parent) parent.appendChild(n);
  return n;
}

const storageData = {};
const storage = { getItem: k => (k in storageData ? storageData[k] : null), setItem: (k, v) => { storageData[k] = v; } };
if (data.stored) storageData[TW.STORAGE_KEY] = JSON.stringify(data.stored);

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
    for (const tag of params.get('tags').split(',')) out[tag] = (served.series[tag] || []).filter(([t]) => t >= from && t <= to);
    return json(out);
  }
  return { ok: false, status: 404, json: async () => ({}) };
};

async function boot(extra) {
  const workspace = make('section', {}, doc.body);
  workspace.screenRect = rect(W.left, W.top, W.width, W.height);
  const layer = make('div', {}, workspace);
  const tray = make('div', {}, workspace);
  const tw = TW.mount(Object.assign({ workspace, layer, tray, fetch, storage, pollMs: 0 }, extra));
  if (data.snapshot) await tw.update(data.snapshot);
  return { tw, workspace, layer, tray };
}
const open = async (s, point, opener, anchor) => { const r = s.tw.open(point, opener, anchor); await s.tw.refresh(); return r; };
const win = (s, point) => s.layer.children.find(n => n.getAttribute('data-point') === point);
const part = (w, cls) => w.querySelector('.' + cls);
const geometry = w => ({ x: parseFloat(w.style.left), y: parseFloat(w.style.top), w: parseFloat(w.style.width), h: parseFloat(w.style.height) });
const pathOf = w => { const m = part(w, 'tw-chart').innerHTML.match(/<path class="trend-pen"[^>]*\\sd="([^"]*)"/); return m ? m[1] : null; };
const yTicks = w => [...part(w, 'tw-chart').innerHTML.matchAll(/class="trend-tick"[^>]*text-anchor="end"[^>]*>([^<]*)</g)].map(m => m[1]);
"""
)


def run_scene(body, recorded, **data):
    return run_js(SCENE + body, served=served(recorded), snapshot=recorded["snapshot"], **data)


@needs_node
def test_a_window_shows_its_point_its_value_and_unit_and_its_bands(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        const w = win(s, '{LEVEL}');
        return {{ windows: s.layer.children.length, label: w.getAttribute('aria-label'), role: w.getAttribute('role'),
                 name: part(w, 'tw-name').textContent, value: part(w, 'tw-value').textContent,
                 chart: part(w, 'tw-chart').innerHTML, pressed: w.querySelectorAll('[data-span]').map(b => [b.textContent, b.getAttribute('aria-pressed')]),
                 focused: doc.activeElement === part(w, 'tw-bar') }};
        """,
        free,
    )

    level = free["snapshot"]["equipment"]["V-101"]["level"] * 100
    assert result["windows"] == 1
    assert result["role"] == "dialog" and result["label"] == "Trend of V-101 level"
    assert result["name"] == "V-101 level"
    assert result["value"] == f"{level:.1f}%"
    # Limits and axis in percent, the band labelled as the alarms spell it.
    assert "HI 80" in result["chart"] and "LOLO 10" in result["chart"]
    assert 'aria-label="Trend of V-101 level in %, the last 10 minutes"' in result["chart"]
    assert result["pressed"] == [["1m", "false"], ["5m", "false"], ["10m", "true"], ["30m", "false"]]
    assert result["focused"] is True


@needs_node
def test_a_pressure_window_reads_its_unit_from_the_listing(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{NODE}');
        return part(win(s, '{NODE}'), 'tw-value').textContent;
        """,
        free,
    )

    assert result == f"{free['snapshot']['nodes']['N-201']['pressure']:.1f}psia"
    assert free["points"]["units"][NODE] == "psia"


@needs_node
def test_opening_the_same_point_twice_leaves_one_window_raised_and_focused(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        await open(s, '{NODE}');
        const first = win(s, '{LEVEL}');
        doc.body.focus();
        await open(s, '{LEVEL}');
        return {{ windows: s.layer.children.length, same: win(s, '{LEVEL}') === first,
                 active: s.layer.children.map(n => [n.getAttribute('data-point'), n.getAttribute('data-active')]),
                 top: Number(first.style.zIndex) > Number(win(s, '{NODE}').style.zIndex),
                 focused: doc.activeElement === part(first, 'tw-bar') }};
        """,
        free,
    )

    assert result["windows"] == 2
    assert result["same"] is True
    assert result["active"] == [[LEVEL, "true"], [NODE, "false"]]
    assert result["top"] is True
    assert result["focused"] is True


@needs_node
def test_a_new_window_sits_beside_its_anchor_clear_of_the_faceplate_and_other_windows(free):
    result = run_scene(
        f"""
        const panel = {{ left: 700, top: 50, right: 900, bottom: 200 }};
        const s = await boot({{ obstacles: () => [{{ rect: panel, weight: 4 }}] }});
        await open(s, '{LEVEL}', null, panel);
        await open(s, '{NODE}', null, panel);
        const box = w => {{ const g = geometry(w); return {{ left: g.x, top: g.y, right: g.x + g.w, bottom: g.y + g.h }}; }};
        const cover = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
          * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        const a = box(win(s, '{LEVEL}')), b = box(win(s, '{NODE}'));
        return {{ aPanel: cover(a, panel), bPanel: cover(b, panel), ab: cover(a, b), obstacles: s.tw.obstacles().length }};
        """,
        free,
    )

    assert result == {"aPanel": 0, "bPanel": 0, "ab": 0, "obstacles": 2}


@needs_node
def test_arrow_keys_on_the_title_bar_move_and_resize_and_keep_it_in_the_workspace(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        const w = win(s, '{LEVEL}');
        const bar = part(w, 'tw-bar');
        const start = geometry(w);
        // An arrow on a title-bar button does not move the window.
        w.querySelector('[data-tw="close"]').fire('keydown', {{ key: 'ArrowRight' }});
        const unmoved = geometry(w);
        const right = bar.fire('keydown', {{ key: 'ArrowRight' }});
        const moved = geometry(w);
        bar.fire('keydown', {{ key: 'ArrowDown', shiftKey: true }});
        const resized = geometry(w);
        for (let i = 0; i < 200; i++) bar.fire('keydown', {{ key: 'ArrowRight' }});
        for (let i = 0; i < 200; i++) bar.fire('keydown', {{ key: 'ArrowDown' }});
        const far = geometry(w);
        for (let i = 0; i < 200; i++) bar.fire('keydown', {{ key: 'ArrowLeft' }});
        for (let i = 0; i < 200; i++) bar.fire('keydown', {{ key: 'ArrowUp' }});
        const near = geometry(w);
        for (let i = 0; i < 100; i++) bar.fire('keydown', {{ key: 'ArrowLeft', shiftKey: true }});
        return {{ start, unmoved, moved, resized, far, near, small: geometry(w), prevented: right.defaultPrevented,
                 stored: JSON.parse(storageData[TW.STORAGE_KEY])['{LEVEL}'] }};
        """,
        free,
    )

    start, moved, resized = result["start"], result["moved"], result["resized"]
    assert result["unmoved"] == start
    assert moved == {**start, "x": start["x"] + 16}
    assert resized == {**moved, "h": moved["h"] + 16}
    assert result["prevented"] is True
    far = result["far"]
    assert far["x"] + far["w"] == 1000 - 8 and far["y"] == 600 - 48
    assert result["near"]["x"] == 8 and result["near"]["y"] == 8
    assert result["small"]["w"] == 260
    assert result["stored"] == {**result["small"], "span": 10}


@needs_node
def test_a_window_is_dragged_by_its_title_bar_and_resized_by_its_grip(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        const w = win(s, '{LEVEL}');
        const start = geometry(w);
        const drag = (node, dx, dy) => {{
          node.fire('pointerdown', {{ button: 0, clientX: 500, clientY: 300, pointerId: 1 }});
          node.fire('pointermove', {{ clientX: 500 + dx, clientY: 300 + dy }});
          node.fire('pointerup', {{}});
        }};
        drag(part(w, 'tw-bar'), -40, 30);
        const moved = geometry(w);
        drag(part(w, 'tw-resize'), 50, 20);
        const resized = geometry(w);
        // A press on a title-bar button is not a drag.
        const minimise = w.querySelector('[data-tw="minimise"]');
        minimise.fire('pointerdown', {{ button: 0, clientX: 500, clientY: 300, pointerId: 1 }});
        part(w, 'tw-bar').fire('pointermove', {{ clientX: 600, clientY: 400 }});
        return {{ start, moved, resized, pressed: geometry(w), stored: JSON.parse(storageData[TW.STORAGE_KEY])['{LEVEL}'] }};
        """,
        free,
    )

    start = result["start"]
    assert result["moved"] == {**start, "x": start["x"] - 40, "y": start["y"] + 30}
    assert result["resized"] == {**result["moved"], "w": start["w"] + 50, "h": start["h"] + 20}
    assert result["pressed"] == result["resized"]
    assert result["stored"] == {**result["resized"], "span": 10}


@needs_node
def test_a_minimised_window_sits_in_the_tray_with_its_value_and_comes_back(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        const w = win(s, '{LEVEL}');
        const emptyTray = s.tray.getAttribute('hidden');
        w.querySelector('[data-tw="minimise"]').fire('click');
        const chip = s.tray.querySelector('.tray-chip');
        const minimised = {{ hidden: w.getAttribute('hidden'), tray: s.tray.getAttribute('hidden'), text: chip.textContent,
                            label: chip.getAttribute('aria-label'), focused: doc.activeElement === chip }};
        // The tray follows the snapshot.
        await s.tw.update({{ ...data.snapshot, equipment: {{ ...data.snapshot.equipment, 'V-101': {{ ...data.snapshot.equipment['V-101'], level: 0.25 }} }} }});
        const followed = s.tray.querySelector('.tray-chip').textContent;
        calls.length = 0;
        await s.tw.poll();
        const ticked = calls.length;
        chip.fire('click');
        await s.tw.refresh();
        return {{ emptyTray, minimised, followed, ticked, restored: {{ hidden: w.getAttribute('hidden'), tray: s.tray.getAttribute('hidden'),
                 chips: s.tray.querySelectorAll('.tray-chip').length, focused: doc.activeElement === part(w, 'tw-bar'),
                 drawn: pathOf(w) !== null }} }};
        """,
        free,
    )

    level = free["snapshot"]["equipment"]["V-101"]["level"] * 100
    assert result["emptyTray"] == ""
    assert result["minimised"] == {"hidden": "", "tray": None, "text": f"V-101 level {level:.1f}%",
                                   "label": "Restore trend of V-101 level", "focused": True}
    assert result["followed"] == "V-101 level 25.0%"
    # Nothing shown, nothing fetched.
    assert result["ticked"] == 0
    assert result["restored"] == {"hidden": None, "tray": "", "chips": 0, "focused": True, "drawn": True}


@needs_node
def test_closing_a_window_returns_focus_to_what_opened_it(free):
    result = run_scene(
        f"""
        const s = await boot();
        const reading = make('text', {{ tabindex: '0' }}, s.workspace);
        await open(s, '{LEVEL}', reading);
        win(s, '{LEVEL}').querySelector('[data-tw="close"]').fire('click');
        const closed = {{ windows: s.layer.children.length, focus: doc.activeElement === reading, open: s.tw.isOpen('{LEVEL}') }};
        // An opener that is gone takes focus nowhere.
        const gone = make('text', {{}}, s.workspace);
        await open(s, '{NODE}', gone);
        gone.detach();
        doc.body.focus();
        win(s, '{NODE}').querySelector('[data-tw="close"]').fire('click');
        return {{ closed, gone: doc.activeElement === doc.body }};
        """,
        free,
    )

    assert result == {"closed": {"windows": 0, "focus": True, "open": False}, "gone": True}


@needs_node
def test_each_points_geometry_and_span_come_back_in_the_same_session(free):
    stored = {LEVEL: {"x": 120, "y": 90, "w": 380, "h": 250, "span": 30}}
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        const w = win(s, '{LEVEL}');
        const back = {{ geometry: geometry(w), pressed: w.querySelector('[aria-pressed="true"]').textContent }};
        w.querySelector('[data-span="5"]').fire('click');
        w.querySelector('[data-tw="close"]').fire('click');
        await open(s, '{LEVEL}');
        return {{ back, reopened: win(s, '{LEVEL}').querySelector('[aria-pressed="true"]').textContent,
                 stored: JSON.parse(storageData[TW.STORAGE_KEY]) }};
        """,
        free, stored=stored,
    )

    assert result["back"] == {"geometry": {"x": 120, "y": 90, "w": 380, "h": 250}, "pressed": "30m"}
    assert result["reopened"] == "5m"
    assert result["stored"][LEVEL] == {"x": 120, "y": 90, "w": 380, "h": 250, "span": 5}


@needs_node
def test_storage_that_throws_does_not_stop_a_window(free):
    result = run_scene(
        f"""
        const hostile = {{ getItem() {{ throw new Error('blocked'); }}, setItem() {{ throw new Error('blocked'); }} }};
        const s = await boot({{ storage: hostile }});
        await open(s, '{LEVEL}');
        part(win(s, '{LEVEL}'), 'tw-bar').fire('keydown', {{ key: 'ArrowRight' }});
        return pathOf(win(s, '{LEVEL}')) !== null;
        """,
        free,
    )

    assert result is True


@needs_node
def test_with_four_windows_open_a_tick_makes_three_requests_inside_the_rate_limit(free):
    points = [LEVEL, NODE, PV, "K-101.outlet_pressure"]
    result = run_scene(
        """
        const s = await boot();
        for (const point of data.points) await open(s, point);
        calls.length = 0;
        await s.tw.poll();
        return { calls, drawn: data.points.map(p => pathOf(win(s, p)) !== null) };
        """,
        free, points=points,
    )

    assert sorted(c.split("?")[0] for c in result["calls"]) == ["/api/alarms/history", "/api/trend", "/api/trend/points"]
    trend = next(c for c in result["calls"] if c.startswith("/api/trend?"))
    assert trend.split("tags=")[1].split("&")[0] == ",".join(points)
    assert result["drawn"] == [True] * 4
    # Three requests per tick, whatever the window count: a sliver of the shared bucket.
    per_second = 3 / (run_js("return TW.TICK_MS;") / 1000)
    assert per_second <= config.API_RATE_PER_SECOND / 10


@needs_node
def test_no_window_open_or_a_hidden_tab_makes_no_requests(free):
    result = run_scene(
        f"""
        const s = await boot();
        await s.tw.poll();
        const none = calls.length;
        await open(s, '{LEVEL}');
        calls.length = 0;
        globalThis.document = {{ hidden: true }};
        await s.tw.poll();
        const hidden = calls.length;
        globalThis.document = {{ hidden: false }};
        await s.tw.poll();
        delete globalThis.document;
        return {{ none, hidden, visible: calls.length }};
        """,
        free,
    )

    assert result == {"none": 0, "hidden": 0, "visible": 3}


@needs_node
def test_a_tick_while_the_last_one_is_still_waiting_is_skipped(free):
    result = run_scene(
        f"""
        let sent = 0;
        const slow = async url => {{ sent += 1; await new Promise(r => setTimeout(r, 30)); return fetch(url); }};
        const s = await boot({{ fetch: slow }});
        await open(s, '{LEVEL}');
        sent = 0;
        await Promise.all([s.tw.poll(), s.tw.poll()]);
        return {{ sent, drawn: pathOf(win(s, '{LEVEL}')) !== null }};
        """,
        free,
    )

    assert result == {"sent": 3, "drawn": True}


@needs_node
def test_a_failing_or_hanging_server_is_reported_in_the_window_and_the_next_tick_recovers(free):
    result = run_scene(
        f"""
        let mode = 'fail';
        const flaky = url => mode === 'fail' ? Promise.resolve({{ ok: false, status: 500, json: async () => ({{}}) }})
          : mode === 'hang' ? new Promise(() => {{}}) : fetch(url);
        const s = await boot({{ fetch: flaky, timeoutMs: 40 }});
        s.tw.open('{LEVEL}');
        await s.tw.refresh();
        const status = () => part(win(s, '{LEVEL}'), 'tw-status').textContent;
        const failed = status();
        mode = 'hang';
        await s.tw.poll();
        const hung = status();
        mode = 'ok';
        await s.tw.poll();
        return {{ failed, hung, recovered: status(), drawn: pathOf(win(s, '{LEVEL}')) !== null }};
        """,
        free,
    )

    assert result == {"failed": "Trend unavailable: HTTP 500", "hung": "Trend unavailable: timed out", "recovered": "", "drawn": True}


@needs_node
def test_a_history_swap_redraws_every_open_window_from_the_new_history(session, client, free):  # noqa: F811
    session.load("pump-trip")
    step(session, 15)
    loaded = record(client)

    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        await open(s, '{NODE}');
        const before = [pathOf(win(s, '{LEVEL}')), pathOf(win(s, '{NODE}'))];
        served = data.after;
        await s.tw.update({{ ...data.snapshot, sim_time: data.after_time }});
        await s.tw.poll();
        return {{ before, after: [pathOf(win(s, '{LEVEL}')), pathOf(win(s, '{NODE}'))] }};
        """,
        free, after=served(loaded), after_time=session.training_scheduler.snapshot().sim_time,
    )

    for before, after in zip(result["before"], result["after"]):
        assert before and after and before != after
        assert after.count("M") == 1


@needs_node
def test_the_scale_widens_when_a_reading_leaves_it_and_holds_until_the_span_changes(free):
    quiet = json.loads(json.dumps(served(free)))
    spike = json.loads(json.dumps(quiet))
    spike["series"][NODE][-1][1] = spike["series"][NODE][-1][1] + 500

    result = run_js(
        SCENE
        + f"""
        const s = await boot();
        await open(s, '{NODE}');
        const w = win(s, '{NODE}');
        const first = yTicks(w);
        served = data.spike;
        await s.tw.poll();
        const widened = yTicks(w);
        served = data.quiet;
        await s.tw.poll();
        const held = yTicks(w);
        w.querySelector('[data-span="5"]').fire('click');
        await s.tw.refresh();
        return {{ first, widened, held, reset: yTicks(w) }};
        """,
        served=quiet, spike=spike, quiet=quiet, snapshot=free["snapshot"],
    )

    top = lambda ticks: max(float(t) for t in ticks)
    assert top(result["widened"]) > top(result["first"]) + 300
    assert result["held"] == result["widened"]
    assert top(result["reset"]) < top(result["widened"]) - 300


@needs_node
def test_a_reset_drops_the_held_scale_even_when_time_moves_on(free):
    quiet = json.loads(json.dumps(served(free)))
    spike = json.loads(json.dumps(quiet))
    spike["series"][NODE][-1][1] += 500

    result = run_js(
        SCENE
        + f"""
        const s = await boot();
        await open(s, '{NODE}');
        const w = win(s, '{NODE}');
        served = data.spike;
        await s.tw.poll();
        const widened = yTicks(w);
        // Another history, ahead of the old one: time did not go back.
        served = data.quiet;
        await s.tw.update({{ ...data.snapshot, sim_time: data.snapshot.sim_time + 10 }});
        await s.tw.reset();
        return {{ widened, reset: yTicks(w) }};
        """,
        served=quiet, spike=spike, quiet=quiet, snapshot=free["snapshot"],
    )

    top = lambda ticks: max(float(t) for t in ticks)
    assert top(result["reset"]) < top(result["widened"]) - 300


@needs_node
def test_a_scenario_alarm_shows_on_its_points_window_only(session, client):  # noqa: F811
    session.load("pump-trip")
    session.start()
    snapshot = step(session, 120)
    recorded = {"snapshot": snapshot, **record(client)}
    raised = [e for e in recorded["alarms"] if e["type"] == "alarm" and e["tag"] == "V-101"]
    assert raised, "the scenario raises a V-101 level alarm"

    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        await open(s, '{NODE}');
        const markers = p => (part(win(s, p), 'tw-chart').innerHTML.match(/class="trend-marker"/g) || []).length;
        return [markers('{LEVEL}'), markers('{NODE}')];
        """,
        recorded,
    )

    assert result[0] >= 1
    assert result[1] == 0


@needs_node
def test_a_window_whose_point_the_plant_no_longer_publishes_closes(free):
    gone = json.loads(json.dumps(served(free)))
    gone["points"]["points"] = [p for p in gone["points"]["points"] if p != NODE]

    result = run_js(
        SCENE
        + f"""
        let changes = 0;
        const s = await boot({{ onChange: () => {{ changes += 1; }} }});
        await open(s, '{LEVEL}');
        await open(s, '{NODE}');
        const opened = changes;
        served = data.gone;
        await s.tw.poll();
        return {{ open: s.layer.children.map(n => n.getAttribute('data-point')), isOpen: s.tw.isOpen('{NODE}'),
                 opened, closed: changes - opened }};
        """,
        served=served(free), gone=gone, snapshot=free["snapshot"],
    )

    # The canvas is told each time, so it can mark the readings whose trend is open.
    assert result == {"open": [LEVEL], "isOpen": False, "opened": 2, "closed": 1}


@needs_node
def test_no_more_windows_open_than_one_request_can_carry(free):
    capped = json.loads(json.dumps(served(free)))
    capped["points"]["max_tags"] = 2

    result = run_js(
        SCENE
        + f"""
        const s = await boot();
        await s.tw.refresh();
        const results = [s.tw.open('{LEVEL}'), s.tw.open('{NODE}'), s.tw.open('{PV}')];
        await s.tw.refresh();
        const refused = {{ results, windows: s.layer.children.length, status: s.tray.querySelector('.tray-status').textContent,
                          tray: s.tray.getAttribute('hidden') }};
        win(s, '{NODE}').querySelector('[data-tw="close"]').fire('click');
        return {{ refused, after: s.tw.open('{PV}'), status: s.tray.querySelector('.tray-status').textContent }};
        """,
        served=capped, snapshot=free["snapshot"],
    )

    assert result["refused"] == {"results": [True, True, False], "windows": 2,
                                 "status": "At most 2 trends can be open. Close one to open another.", "tray": None}
    assert result["after"] is True
    assert result["status"] == ""


@needs_node
def test_before_the_first_snapshot_a_window_waits_for_the_plant(free):
    result = run_js(
        SCENE
        + f"""
        const s = await boot();
        await open(s, '{LEVEL}');
        const waiting = part(win(s, '{LEVEL}'), 'tw-status').textContent;
        await s.tw.update(data.later);
        await s.tw.refresh();
        return {{ waiting, after: part(win(s, '{LEVEL}'), 'tw-status').textContent, drawn: pathOf(win(s, '{LEVEL}')) !== null }};
        """,
        served=served(free), snapshot=None, later=free["snapshot"],
    )

    assert result == {"waiting": "Waiting for the plant.", "after": "", "drawn": True}


@needs_node
def test_a_loop_points_value_reads_from_the_controllers_row(free):
    result = run_scene(
        f"""
        const s = await boot();
        await open(s, '{PV}');
        return part(win(s, '{PV}'), 'tw-value').textContent;
        """,
        free,
    )

    assert result == f"{free['snapshot']['controllers']['PIC-101']['pv']:.1f}psia"


# ---- The page and the stylesheet ----


def test_the_console_has_a_window_layer_and_tray_below_the_faceplate_layer():
    page = CONSOLE_HTML.read_text()
    css = TRENDS_CSS.read_text()

    assert page.index('id="window-layer"') < page.index('id="trend-tray"') < page.index('id="faceplate-layer"')
    assert re.search(r"\.window-layer\s*\{[^}]*z-index:\s*30", css)
    assert re.search(r"\.tray\s*\{[^}]*z-index:\s*35", css)
    assert "trends: trends" in page and "obstacles: trends.obstacles" in page


def test_a_hidden_window_and_tray_are_not_drawn():
    css = TRENDS_CSS.read_text()

    assert re.search(r"\.trend-window\[hidden\]\s*\{[^}]*display:\s*none", css)
    assert re.search(r"\.tray\[hidden\]\s*\{[^}]*display:\s*none", css)
