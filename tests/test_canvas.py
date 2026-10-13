"""Canvas selection (T20-10): static/js/canvas.js and the selection rules in
static/css/console.css.

canvas.js runs under Node (a subprocess, JSON in and out). The placement rule
is pure and is run over fixed rectangles. The glue is run over the small fake
DOM in tests/fake_dom.js, fed the plant description and trend listing the
console's own routes serve and a snapshot from a real `TrainingSession`: it
checks what a click, a key and a snapshot do, not how anything looks, which
was seen in a browser (both themes; phone width is not checked yet).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import main

ROOT = Path(__file__).resolve().parent.parent
CANVAS_JS = ROOT / "static" / "js" / "canvas.js"
FACEPLATE_JS = ROOT / "static" / "js" / "faceplate.js"
CONSOLE_CSS = ROOT / "static" / "css" / "console.css"
GRAPHIC = ROOT / "static" / "graphics" / "olefins_lite.svg"
FAKE_DOM = (ROOT / "tests" / "fake_dom.js").read_text()


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(body, **data):
    """Evaluate `body` (the body of an async function with `C`, the canvas.js
    exports, `F`, faceplate.js's, and `data` in scope) under Node; return its
    JSON."""
    script = (
        f"const C = require({json.dumps(str(CANVAS_JS))});"
        f"const F = require({json.dumps(str(FACEPLATE_JS))});"
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
def console():
    """What the console receives: the plant description, the trend listing and
    a snapshot, as the browser gets them from the live routes."""
    http = main.app.test_client()
    http.get("/console")
    cookie = http.get_cookie(main.SESSION_COOKIE).value
    session = main.sessions.get(cookie)
    try:
        yield {
            "plant": http.get("/api/plant").get_json(),
            "vocab": http.get("/api/trend/points").get_json(),
            "snapshot": session.operator_view(session.training_scheduler.snapshot()),
        }
    finally:
        main.sessions.end(cookie)


# ---- Placement is a pure rule over rectangles ----

PLACE = """
const rect = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });
const box = (p, w, h) => rect(p.x, p.y, w, h);
const cover = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
  * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
const bounds = { width: 500, height: 400 };
const size = { w: 100, h: 100 };
const anchor = rect(200, 150, 100, 100);
"""


@needs_node
def test_a_panel_sits_beside_its_anchor_when_there_is_room():
    result = run_js(
        PLACE + "return C.place({ size, bounds, anchor, avoid: [], others: [] });",
    )

    # To the right, centred on the anchor, a gap clear of it.
    assert result == {"x": 314, "y": 150}


@needs_node
def test_a_panel_moves_to_the_other_side_when_the_first_has_no_room():
    result = run_js(
        PLACE + "return C.place({ size, bounds, anchor: rect(380, 150, 100, 100), avoid: [], others: [] });",
    )

    assert result == {"x": 266, "y": 150}


@needs_node
def test_a_panel_goes_above_or_below_when_the_sides_are_full():
    result = run_js(
        PLACE
        + """
        const wide = rect(8, 150, 484, 100);
        return {
          below: C.place({ size, bounds, anchor: wide, avoid: [], others: [] }),
          above: C.place({ size, bounds, anchor: wide, avoid: [{ rect: rect(0, 264, 500, 136), weight: 3 }], others: [] }),
        };
        """,
    )

    assert result["below"]["y"] == 264
    assert result["above"]["y"] == 36


@needs_node
def test_a_panel_stays_clear_of_open_windows_when_it_can():
    result = run_js(
        PLACE
        + """
        const window_ = rect(314, 100, 160, 200);   // where the panel would go
        const p = C.place({ size, bounds, anchor, avoid: [{ rect: window_, weight: 3 }], others: [] });
        return { p, window: cover(box(p, 100, 100), window_), anchor: cover(box(p, 100, 100), anchor) };
        """,
    )

    assert result["window"] == 0 and result["anchor"] == 0
    assert result["p"] != {"x": 314, "y": 150}


@needs_node
def test_a_faceplate_takes_the_least_covered_side_rather_than_a_corner_across_the_canvas():
    result = run_js(
        PLACE
        + """
        // A small thing sits on every side of the anchor; the far corner is empty.
        const near = [rect(314, 150, 100, 100), rect(86, 150, 100, 100), rect(200, 264, 100, 100), rect(200, 36, 100, 100)]
          .map((r, i) => i === 3 ? rect(r.left, r.top, 30, 100) : r);
        const open = C.place({ size, bounds, anchor, avoid: [], others: near });
        const beside = C.place({ size, bounds, anchor, avoid: [], others: near, besideOnly: true });
        return { open, beside };
        """,
    )

    # Free to roam, the panel leaves for the empty corner; as a faceplate it stays above the anchor,
    # where the least is covered.
    assert result["open"] == {"x": 8, "y": 8}
    assert result["beside"] == {"x": 200, "y": 36}


@needs_node
def test_a_panel_covers_the_lighter_thing_when_nothing_is_free():
    # The whole workspace is taken: heavy on the right half, light on the left.
    result = run_js(
        PLACE
        + """
        const right = rect(250, 0, 250, 400);
        const left = rect(0, 0, 250, 400);
        const p = C.place({ size, bounds, anchor: rect(230, 150, 40, 100), avoid: [{ rect: right, weight: 5 }, { rect: left, weight: 1 }], others: [] });
        return { right: cover(box(p, 100, 100), right), left: cover(box(p, 100, 100), left) };
        """,
    )

    assert result["right"] == 0
    assert result["left"] == 100 * 100


@needs_node
def test_a_panel_never_covers_its_anchor_while_any_free_spot_exists():
    result = run_js(
        PLACE
        + """
        const worst = [];
        for (let x = 0; x <= 400; x += 50) for (let y = 0; y <= 300; y += 50) {
          const a = rect(x, y, 100, 100);
          const others = [rect(150, 150, 60, 60), rect(300, 50, 60, 60)];
          const p = C.place({ size, bounds, anchor: a, avoid: [], others });
          if (cover(box(p, 100, 100), a) > 0) worst.push([x, y]);
        }
        return worst;
        """,
    )

    assert result == []


@needs_node
def test_a_panel_stays_inside_the_workspace():
    result = run_js(
        PLACE
        + """
        const out = [];
        for (const a of [rect(0, 0, 40, 40), rect(460, 360, 40, 40), rect(230, 0, 40, 40), rect(0, 180, 40, 40)]) {
          const p = C.place({ size, bounds, anchor: a, avoid: [], others: [] });
          out.push(p.x >= 8 && p.y >= 8 && p.x + 100 <= 492 && p.y + 100 <= 392);
        }
        return out;
        """,
    )

    assert result == [True] * 4


@needs_node
def test_a_workspace_smaller_than_the_panel_pins_it_to_the_corner():
    result = run_js(
        PLACE + "return C.place({ size: { w: 330, h: 260 }, bounds: { width: 300, height: 200 }, anchor, avoid: [], others: [] });",
    )

    assert result == {"x": 8, "y": 8}


# ---- What the plant says ----


@needs_node
def test_a_reading_belongs_to_the_device_that_lists_its_point(console):
    result = run_js(
        """
        const plant = C.descriptionOf(data.plant);
        return ['equipment.V-101.level', 'streams.B-PV-101.flow', 'equipment.K-101.flow', 'nodes.N-201.pressure', 'bogus']
          .map(path => C.ownerOf(plant, path));
        """,
        plant=console["plant"],
    )

    assert result == ["V-101", "PV-101", "K-101", None, None]


@needs_node
def test_a_body_that_names_no_plant_describes_nothing():
    assert run_js("return [C.descriptionOf({}), C.descriptionOf(null), C.descriptionOf({ plant: 3 })];") == [None] * 3


def test_every_device_and_loop_the_graphic_draws_is_one_the_plant_describes(console):
    svg = GRAPHIC.read_text()
    drawn = set(re.findall(r'class="eq" data-tag="([^"]+)"', svg))
    loops = set(re.findall(r'class="ctl" data-controller="([^"]+)"', svg))

    assert drawn == set(console["plant"]["equipment"])
    assert loops == set(console["plant"]["controllers"])


# ---- The glue, over a fake DOM ----

SCENE = (
    FAKE_DOM
    + """
const W = { left: 100, top: 50, width: 1000, height: 600 };
const rect = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h });
const rel = r => ({ left: r.left - W.left, top: r.top - W.top, right: r.right - W.left, bottom: r.bottom - W.top });
const cover = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
  * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
function make(tag, attrs, parent) {
  const n = doc.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
  if (parent) parent.appendChild(n);
  return n;
}

// A drawing of the described plant: a group per device in a row, a balloon per
// loop, one group the plant does not describe, a reading drawn outside any
// device that a device lists, and a node pressure that no device lists.
function drawing(plant) {
  const svg = make('svg');
  Object.keys(plant.equipment).forEach((tag, i) => {
    const g = make('g', { class: 'eq', 'data-tag': tag }, svg);
    g.screenRect = rect(W.left + 80 + i * 140, W.top + 300, 60, 60);
    g.bbox = { x: 80 + i * 140, y: 300, width: 60, height: 60 };
    make('text', { 'data-bind': 'equipment.' + tag + '.' + plant.equipment[tag].points[0].split('.').pop() }, g);
  });
  Object.keys(plant.controllers).forEach(tag => {
    const g = make('g', { class: 'ctl', 'data-controller': tag }, svg);
    make('circle', { class: 'ctl-balloon' }, g).screenRect = rect(W.left + 500, W.top + 40, 40, 40);
    make('text', { 'data-controller-mode': tag }, g).screenRect = rect(W.left + 546, W.top + 50, 40, 16);
  });
  make('g', { class: 'eq', 'data-tag': 'X-999' }, svg);
  make('text', { 'data-bind': 'streams.B-PV-101.flow' }, svg).screenRect = rect(W.left + 60, W.top + 500, 50, 16);
  make('text', { 'data-bind': 'nodes.N-201.pressure' }, svg);
  return svg;
}

async function boot(extra, plant) {
  const workspace = make('section');
  workspace.screenRect = rect(W.left, W.top, W.width, W.height);
  doc.body.appendChild(workspace);
  const canvasEl = make('div', {}, workspace);
  const layer = make('div', {}, workspace);
  const svg = drawing(data.plant);
  canvasEl.appendChild(svg);
  const requested = [];
  let plantBody = plant === undefined ? data.plant : plant;
  const fetch = url => {
    requested.push(url);
    const body = url === '/api/plant' ? plantBody : data.vocab;
    if (body === null) return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  };
  const canvas = C.mount(Object.assign({ canvas: canvasEl, workspace, layer, fetch, faceplates: F }, extra));
  await canvas.ready;
  canvas.update(data.snapshot);
  return { canvas, workspace, canvasEl, layer, svg, requested, setPlant: p => { plantBody = p; } };
}
const group = (svg, attr, value) => svg.querySelectorAll('[' + attr + ']').find(n => n.getAttribute(attr) === value);
const panels = scene => scene.layer.children;
const panelRect = scene => {
  const p = panels(scene)[0].element || panels(scene)[0];
  const x = parseFloat(p.style.left), y = parseFloat(p.style.top);
  return { left: x, top: y, right: x + doc.panelSize.w, bottom: y + doc.panelSize.h };
};
"""
)


def run_scene(body, console, **data):
    return run_js(SCENE + body, plant=console["plant"], vocab=console["vocab"], snapshot=console["snapshot"], **data)


@needs_node
def test_devices_and_balloons_the_plant_describes_are_keyboard_buttons(console):
    result = run_scene(
        """
        const s = await boot();
        const state = n => n && ({ tab: n.getAttribute('tabindex'), role: n.getAttribute('role'),
          label: n.getAttribute('aria-label'), kind: n.getAttribute('data-canvas-kind') });
        return {
          device: state(group(s.svg, 'data-tag', 'P-101')),
          balloon: state(group(s.svg, 'data-controller', 'PIC-101')),
          unknown: state(group(s.svg, 'data-tag', 'X-999')),
          hit: !!group(s.svg, 'data-tag', 'P-101').querySelector('.eq-hit'),
          nodeReading: state(s.svg.querySelectorAll('[data-bind]').find(n => n.getAttribute('data-bind') === 'nodes.N-201.pressure')),
        };
        """,
        console,
    )

    assert result["device"] == {"tab": "0", "role": "button", "label": "P-101 Centrifugal pump. Open faceplate.", "kind": "device"}
    assert result["balloon"] == {"tab": "0", "role": "button", "label": "PIC-101 controller. Open faceplate.", "kind": "loop"}
    assert result["hit"] is True
    # Drawn, but not described by the plant: not a button. A node pressure no device lists is inert too.
    assert result["unknown"] == {"tab": None, "role": None, "label": None, "kind": None}
    assert result["nodeReading"] == {"tab": None, "role": None, "label": None, "kind": None}


@needs_node
def test_a_reading_outside_a_device_that_a_device_lists_is_a_button_to_that_device(console):
    result = run_scene(
        """
        const s = await boot();
        const reading = s.svg.querySelectorAll('[data-bind]').find(n => n.getAttribute('data-bind') === 'streams.B-PV-101.flow');
        const inside = group(s.svg, 'data-tag', 'P-101').querySelector('text');
        reading.fire('click');
        const opened = { selected: s.canvas.selected(), marked: reading.getAttribute('data-selected'),
                         device: group(s.svg, 'data-tag', 'PV-101').getAttribute('data-selected') };
        const spot = panelRect(s);
        return { label: reading.getAttribute('aria-label'), inside: inside.getAttribute('role'), opened,
                 covers: cover(spot, rel(reading.screenRect)) };
        """,
        console,
    )

    assert result["label"] == "PV-101 flow. Open PV-101 faceplate."
    assert result["inside"] is None
    assert result["opened"] == {"selected": {"kind": "device", "tag": "PV-101"}, "marked": "true", "device": "true"}
    # The panel sits beside the reading it was opened from, never on it.
    assert result["covers"] == 0


@needs_node
def test_selecting_a_device_opens_one_faceplate_beside_it_never_on_it(console):
    result = run_scene(
        """
        const s = await boot();
        const out = {};
        for (const tag of Object.keys(data.plant.equipment)) {
          const g = group(s.svg, 'data-tag', tag);
          g.fire('click');
          const spot = panelRect(s);
          out[tag] = { panels: panels(s).length, title: panels(s)[0].querySelector('.fp-tag').textContent,
                       on: cover(spot, rel(g.screenRect)), inside: spot.left >= 8 && spot.top >= 8 && spot.right <= W.width - 8 && spot.bottom <= W.height - 8 };
        }
        return out;
        """,
        console,
    )

    assert set(result) == set(console["plant"]["equipment"])
    for tag, shown in result.items():
        assert shown == {"panels": 1, "title": tag, "on": 0, "inside": True}


@needs_node
def test_selecting_a_balloon_opens_the_loops_faceplate_beside_it_and_its_mode_text(console):
    result = run_scene(
        """
        const s = await boot();
        const balloon = group(s.svg, 'data-controller', 'PIC-101');
        balloon.fire('click');
        const spot = panelRect(s);
        return { selected: s.canvas.selected(), kind: panels(s)[0].getAttribute('data-kind'),
                 on: cover(spot, rel(balloon.querySelector('.ctl-balloon').screenRect)),
                 onMode: cover(spot, rel(balloon.querySelector('[data-controller-mode]').screenRect)),
                 marked: balloon.getAttribute('data-selected') };
        """,
        console,
    )

    assert result == {"selected": {"kind": "loop", "tag": "PIC-101"}, "kind": "loop", "on": 0, "onMode": 0, "marked": "true"}


@needs_node
def test_a_selected_item_is_marked_and_the_mark_follows_the_selection(console):
    result = run_scene(
        """
        const s = await boot();
        const marks = () => s.svg.querySelectorAll('[data-canvas-tag]').filter(n => n.getAttribute('data-selected') === 'true')
          .map(n => n.getAttribute('data-canvas-tag'));
        const none = marks();
        group(s.svg, 'data-tag', 'P-101').fire('click');
        const first = marks();
        group(s.svg, 'data-tag', 'K-101').fire('click');
        const second = marks();
        s.canvas.close(false);
        return { none, first, second, closed: marks(), panels: panels(s).length };
        """,
        console,
    )

    assert result == {"none": [], "first": ["P-101"], "second": ["K-101"], "closed": [], "panels": 0}


@needs_node
def test_selecting_what_is_open_again_does_not_stack_a_second_panel(console):
    result = run_scene(
        """
        const s = await boot();
        const g = group(s.svg, 'data-tag', 'P-101');
        g.fire('click');
        const first = panels(s)[0];
        doc.body.focus();
        g.fire('click');
        return { count: panels(s).length, same: panels(s)[0] === first,
                 focused: doc.activeElement === first.querySelector('.fp-close') };
        """,
        console,
    )

    assert result == {"count": 1, "same": True, "focused": True}


@needs_node
def test_enter_and_space_press_a_focused_item_and_other_keys_do_not(console):
    result = run_scene(
        """
        const s = await boot();
        const g = group(s.svg, 'data-tag', 'P-101');
        const inner = g.querySelector('text');
        const log = [];
        inner.fire('keydown', { key: 'Enter' });          // not the focused item itself
        log.push(panels(s).length);
        g.fire('keydown', { key: 'a' });
        log.push(panels(s).length);
        const enter = g.fire('keydown', { key: 'Enter' });
        log.push(panels(s).length, enter.defaultPrevented);
        s.canvas.close(false);
        g.fire('keydown', { key: ' ' });
        log.push(panels(s).length);
        return log;
        """,
        console,
    )

    assert result == [0, 0, 1, True, 1]


@needs_node
def test_a_faceplate_closes_by_its_button_by_escape_and_by_a_click_on_empty_canvas(console):
    result = run_scene(
        """
        const s = await boot();
        const g = group(s.svg, 'data-tag', 'P-101');
        const open = () => g.fire('click');

        open();
        panels(s)[0].querySelector('.fp-close').fire('click');
        const byButton = { panels: panels(s).length, focus: doc.activeElement === g };

        open();
        doc.activeElement.fire('keydown', { key: 'Escape' });
        const byEscape = { panels: panels(s).length, focus: doc.activeElement === g };

        open();
        s.canvasEl.fire('click');
        const byCanvas = { panels: panels(s).length };

        open();
        panels(s)[0].fire('click');                          // a click inside the panel is not "empty"
        const inside = panels(s).length;
        return { byButton, byEscape, byCanvas, inside };
        """,
        console,
    )

    assert result["byButton"] == {"panels": 0, "focus": True}
    assert result["byEscape"] == {"panels": 0, "focus": True}
    assert result["byCanvas"] == {"panels": 0}
    assert result["inside"] == 1


@needs_node
def test_escape_pressed_in_some_other_part_of_the_page_leaves_the_faceplate_open(console):
    result = run_scene(
        """
        const s = await boot();
        group(s.svg, 'data-tag', 'P-101').fire('click');
        const elsewhere = make('input', {}, doc.body);
        elsewhere.focus();
        elsewhere.fire('keydown', { key: 'Escape' });
        return panels(s).length;
        """,
        console,
    )

    assert result == 1


@needs_node
def test_focus_returns_to_the_item_that_opened_the_faceplate_after_a_hop_through_the_loop(console):
    result = run_scene(
        """
        const s = await boot();
        const pv = group(s.svg, 'data-tag', 'PV-101');
        const balloon = group(s.svg, 'data-controller', 'PIC-101');
        pv.fire('click');
        const note = panels(s)[0].textContent;
        panels(s)[0].querySelectorAll('button').find(b => b.getAttribute('data-open') === 'PIC-101').fire('click');
        const hop = { selected: s.canvas.selected(), panels: panels(s).length, kind: panels(s)[0].getAttribute('data-kind') };
        const stays = { panels: panels(s).length, focus: doc.activeElement === panels(s)[0].querySelector('.fp-close') };
        panels(s)[0].querySelector('.fp-close').fire('click');
        return { note, hop, stays, focusOnBalloon: doc.activeElement === balloon, closed: s.canvas.selected() };
        """,
        console,
    )

    assert "PV-101 is driven by PIC-101." in result["note"]
    assert result["hop"] == {"selected": {"kind": "loop", "tag": "PIC-101"}, "panels": 1, "kind": "loop"}
    # The click that hopped also reaches the workspace; it must not close what it opened.
    assert result["stays"] == {"panels": 1, "focus": True}
    assert result["focusOnBalloon"] is True
    assert result["closed"] is None


@needs_node
def test_a_loops_drives_button_opens_the_device_it_drives(console):
    result = run_scene(
        """
        const s = await boot();
        group(s.svg, 'data-controller', 'PIC-101').fire('click');
        panels(s)[0].querySelectorAll('button').find(b => b.getAttribute('data-open') === 'PV-101').fire('click');
        return { selected: s.canvas.selected(), panels: panels(s).length };
        """,
        console,
    )

    assert result == {"selected": {"kind": "device", "tag": "PV-101"}, "panels": 1}


@needs_node
def test_an_open_faceplate_follows_every_snapshot(console):
    moved = json.loads(json.dumps(console["snapshot"]))
    moved["equipment"]["P-101"]["speed"] = 0.25

    result = run_scene(
        """
        const s = await boot();
        group(s.svg, 'data-tag', 'P-101').fire('click');
        const speed = () => panels(s)[0].querySelectorAll('div').find(n => n.getAttribute('data-point') === 'P-101.speed').children[1].textContent;
        const before = speed();
        s.canvas.update(data.moved);
        return { before, after: speed() };
        """,
        console, moved=moved,
    )

    assert result == {"before": "100.0 %", "after": "25.0 %"}


@needs_node
def test_a_command_from_a_faceplate_goes_to_the_action_endpoint(console):
    result = run_scene(
        """
        const posted = [];
        const s = await boot({ fetch: (url, init) => {
          if (url !== '/api/action') return Promise.resolve({ ok: true, json: () => Promise.resolve(url === '/api/plant' ? data.plant : data.vocab) });
          posted.push(JSON.parse(init.body));
          return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
        } });
        group(s.svg, 'data-controller', 'PIC-101').fire('click');
        panels(s)[0].querySelectorAll('button').find(b => b.getAttribute('data-action') === 'auto').fire('click');
        await settle();
        return posted;
        """,
        console,
    )

    assert result == [{"target": "PIC-101", "action": "auto", "value": None}]


@needs_node
def test_the_panel_keeps_clear_of_whatever_the_page_says_to_avoid(console):
    result = run_scene(
        """
        const window_ = rect(0, 0, 0, 0);
        const s = await boot({ obstacles: () => [{ rect: window_, weight: 3 }] });
        const g = group(s.svg, 'data-tag', 'P-101');
        // A window drawn where the panel would go beside P-101 (to its right).
        Object.assign(window_, rel(rect(g.screenRect.right + 14, g.screenRect.top - 100, 330, 260)));
        g.fire('click');
        const spot = panelRect(s);
        return { window: cover(spot, window_), anchor: cover(spot, rel(g.screenRect)) };
        """,
        console,
    )

    assert result == {"window": 0, "anchor": 0}


# ---- Following the plant ----


@needs_node
def test_a_remounted_graphic_is_made_selectable_again(console):
    result = run_scene(
        """
        const s = await boot();
        group(s.svg, 'data-tag', 'P-101').fire('click');
        // graphic.js mounts a fresh SVG: the old buttons are gone.
        const fresh = drawing(data.plant);
        s.canvasEl.textContent = '';
        s.canvasEl.appendChild(fresh);
        const before = group(fresh, 'data-tag', 'P-101').getAttribute('role');
        s.canvas.update(data.snapshot);
        const after = group(fresh, 'data-tag', 'P-101').getAttribute('role');
        const hits = fresh.querySelectorAll('.eq-hit').length;
        group(fresh, 'data-tag', 'P-101').fire('click');            // already open: only refocuses
        s.canvas.close(true);
        return { before, after, hits, panels: panels(s).length, focus: doc.activeElement === group(fresh, 'data-tag', 'P-101') };
        """,
        console,
    )

    assert result == {"before": None, "after": "button", "hits": len(console["plant"]["equipment"]), "panels": 0, "focus": True}


@needs_node
def test_a_different_plant_closes_the_faceplate_and_the_same_plant_does_not(console):
    result = run_scene(
        """
        const s = await boot();
        group(s.svg, 'data-tag', 'P-101').fire('click');
        await s.canvas.refresh();
        const same = { panels: panels(s).length, requests: s.requested.filter(u => u === '/api/plant').length };
        s.setPlant(Object.assign({}, data.plant, { plant: 'another_plant' }));
        await s.canvas.refresh();
        return { same, other: panels(s).length, selected: s.canvas.selected(),
                 marked: s.svg.querySelectorAll('[data-canvas-tag]').filter(n => n.getAttribute('data-selected') === 'true').length };
        """,
        console,
    )

    assert result == {"same": {"panels": 1, "requests": 2}, "other": 0, "selected": None, "marked": 0}


@needs_node
def test_a_graphic_is_not_selectable_until_the_plant_has_been_read(console):
    result = run_scene(
        """
        const s = await boot({}, null);                      // /api/plant fails
        await settle();
        const role = () => group(s.svg, 'data-tag', 'P-101').getAttribute('role');
        group(s.svg, 'data-tag', 'P-101').fire('click');
        const failed = { role: role(), panels: panels(s).length };
        s.setPlant(data.plant);
        s.canvas.update(data.snapshot);                      // the next snapshot tries again
        await settle();
        return { failed, role: role() };
        """,
        console,
    )

    assert result == {"failed": {"role": None, "panels": 0}, "role": "button"}


@needs_node
def test_a_missing_trend_listing_costs_the_words_and_not_the_faceplate(console):
    result = run_scene(
        """
        const s = await boot({ fetch: url => url === '/api/plant'
          ? Promise.resolve({ ok: true, json: () => Promise.resolve(data.plant) })
          : Promise.reject(new Error('down')) });
        group(s.svg, 'data-tag', 'V-101').fire('click');
        return panels(s)[0].querySelectorAll('dt').map(n => n.textContent);
        """,
        console,
    )

    assert result == ["Level", "Pressure"]


@needs_node
def test_the_hit_area_follows_the_drawn_group(console):
    result = run_scene(
        """
        const s = await boot();
        const g = group(s.svg, 'data-tag', 'K-101');
        const hit = g.querySelector('.eq-hit');
        const read = () => ['x', 'y', 'width', 'height'].map(k => parseFloat(hit.getAttribute(k)));
        const first = read();
        g.bbox = { x: 10, y: 20, width: 100, height: 40 };      // a longer reading widens the group
        s.canvas.update(data.snapshot);
        return { first, grown: read(), expected: [g.bbox.x - 6, g.bbox.y - 6, g.bbox.width + 12, g.bbox.height + 12],
                 hits: g.querySelectorAll('.eq-hit').length };
        """,
        console,
    )

    box = result["first"]
    assert box[2:] == [72, 72]
    assert result["grown"] == result["expected"] == [4, 14, 112, 52]
    assert result["hits"] == 1


@needs_node
def test_a_group_that_is_not_rendered_does_not_stop_the_faceplate_updating(console):
    moved = json.loads(json.dumps(console["snapshot"]))
    moved["equipment"]["P-101"]["speed"] = 0.25

    result = run_scene(
        """
        const s = await boot();
        group(s.svg, 'data-tag', 'P-101').fire('click');
        group(s.svg, 'data-tag', 'K-101').getBBox = () => { throw new Error('not rendered'); };
        s.canvas.update(data.moved);
        return panels(s)[0].querySelectorAll('div').find(n => n.getAttribute('data-point') === 'P-101.speed').children[1].textContent;
        """,
        console, moved=moved,
    )

    assert result == "25.0 %"


# ---- Selection looks like a tint, not an underline ----


def selection_css():
    css = CONSOLE_CSS.read_text()
    start = css.index("canvas selection (T20-10)")

    return css[start : css.index("small screens", start)]


def test_selection_and_hover_are_tints_and_nothing_is_underlined():
    css = selection_css()

    assert "text-decoration" not in css
    assert "var(--select-tint)" in css and "var(--hover-tint)" in css
    hover = re.findall(r"[^{}]*:hover[^{}]*\{([^}]*)\}", css)
    assert hover and all(re.fullmatch(r"\s*(fill|stroke): var\(--[\w-]+\);\s*", body) for body in hover)


def test_the_selection_rules_use_tokens_only():
    css = selection_css()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)


# ---- Readings open their trend (T20-11) ----

TRENDS = """
const shown = new Set();
const opened = [];
const trends = { open: (point, opener, anchor) => { opened.push({ point, opener, anchor }); shown.add(point); },
                 isOpen: point => shown.has(point) };
const reading = (s, path) => s.svg.querySelectorAll('[data-bind]').find(n => n.getAttribute('data-bind') === path);
"""


@needs_node
def test_with_trend_windows_a_reading_outside_a_device_is_a_button_to_its_trend(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        const node = reading(s, 'nodes.N-201.pressure');
        const stream = reading(s, 'streams.B-PV-101.flow');
        const named = node.getAttribute('aria-label');
        node.textContent = '201.3 psia';
        s.canvas.update(data.snapshot);
        const kept = node.getAttribute('aria-label') === named;
        const inside = group(s.svg, 'data-tag', 'P-101').querySelector('text');
        const state = n => ({ role: n.getAttribute('role'), tab: n.getAttribute('tabindex'), kind: n.getAttribute('data-canvas-kind'),
                              tag: n.getAttribute('data-canvas-tag'), label: n.getAttribute('aria-label') });
        node.fire('click');
        const enter = stream.fire('keydown', { key: 'Enter' });
        return { kept, node: state(node), stream: state(stream), inside: inside.getAttribute('role'),
                 opened: opened.map(o => ({ point: o.point, opener: o.opener === node ? 'node' : o.opener === stream ? 'stream' : '?',
                                            anchor: o.anchor })),
                 enter: enter.defaultPrevented, panels: panels(s).length };
        """,
        console,
    )

    assert result["node"] == {"role": "button", "tab": "0", "kind": "trend", "tag": "N-201.pressure",
                              "label": "N-201 pressure. Open trend."}
    assert result["stream"]["kind"] == "trend" and result["stream"]["tag"] == "B-PV-101.flow"
    # The name does not follow the value, so a screen reader is not told again every snapshot.
    assert result["kept"] is True
    # A reading inside a device stays part of the device's button.
    assert result["inside"] is None
    # A trend opens beside the reading it was opened from; no faceplate opens.
    assert result["opened"] == [
        # A reading with no size (not laid out) has nothing to sit beside.
        {"point": "N-201.pressure", "opener": "node", "anchor": None},
        {"point": "B-PV-101.flow", "opener": "stream", "anchor": {"left": 60, "top": 500, "right": 110, "bottom": 516}},
    ]
    assert result["enter"] is True
    assert result["panels"] == 0


@needs_node
def test_a_reading_whose_trend_is_open_is_marked(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        const node = reading(s, 'nodes.N-201.pressure');
        const before = node.getAttribute('data-selected');
        shown.add('N-201.pressure');
        s.canvas.mark();
        const open = node.getAttribute('data-selected');
        shown.delete('N-201.pressure');
        s.canvas.mark();
        return [before, open, node.getAttribute('data-selected')];
        """,
        console,
    )

    assert result == ["false", "true", "false"]


@needs_node
def test_a_faceplates_trend_button_opens_the_trend_beside_the_panel(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        group(s.svg, 'data-tag', 'K-101').fire('click');
        const panel = panels(s)[0];
        panel.screenRect = rect(W.left + 400, W.top + 100, 330, 260);
        const button = panel.querySelectorAll('[data-trend]')[0];
        button.fire('click');
        return { point: opened[0].point, opener: opened[0].opener === button, anchor: opened[0].anchor,
                 panelRect: s.canvas.panelRect(), panels: panels(s).length };
        """,
        console,
    )

    assert result["point"] == console["plant"]["equipment"]["K-101"]["points"][0]
    assert result["opener"] is True
    assert result["anchor"] == {"left": 400, "top": 100, "right": 730, "bottom": 360}
    assert result["panelRect"] == result["anchor"]
    # The faceplate stays open.
    assert result["panels"] == 1


@needs_node
def test_escape_in_a_trend_window_leaves_the_faceplate_open(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        group(s.svg, 'data-tag', 'P-101').fire('click');
        const windowBar = make('div', { tabindex: '0' }, make('div', {}, s.workspace));
        windowBar.focus();
        windowBar.fire('keydown', { key: 'Escape' });
        const fromWindow = panels(s).length;
        group(s.svg, 'data-tag', 'P-101').focus();
        doc.activeElement.fire('keydown', { key: 'Escape' });
        return { fromWindow, fromCanvas: panels(s).length };
        """,
        console,
    )

    assert result == {"fromWindow": 1, "fromCanvas": 0}


@needs_node
def test_a_click_in_a_trend_window_leaves_the_faceplate_open(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        group(s.svg, 'data-tag', 'P-101').fire('click');
        make('button', {}, make('div', {}, make('div', {}, s.workspace))).fire('click');
        const fromWindow = panels(s).length;
        s.workspace.fire('click');
        return { fromWindow, fromGutter: panels(s).length };
        """,
        console,
    )

    assert result == {"fromWindow": 1, "fromGutter": 0}


# ---- Locate and Show trend for the alarm overlay (T20-12) ----

# Timers are held so a test can let Locate's tint expire without waiting for it.
HELD_TIMERS = """
const realSetTimeout = globalThis.setTimeout;
const timers = [];
globalThis.setTimeout = fn => timers.push(fn);
globalThis.clearTimeout = id => { timers[id - 1] = null; };
const release = () => { globalThis.setTimeout = realSetTimeout; };
const located = s => s.svg.querySelectorAll('[data-located]').map(n => n.getAttribute('data-canvas-tag'));
"""


@needs_node
def test_locate_tints_the_alarms_reading_or_device_and_focuses_it_without_opening_anything(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        """
        + HELD_TIMERS
        + """
        const out = {};
        out.reading = [s.canvas.locate('N-201', 'N-201.pressure'), located(s), doc.activeElement.getAttribute('data-canvas-tag')];
        // Moving on to a device takes the tint off the reading.
        out.device = [s.canvas.locate('P-101', 'P-101.discharge_pressure'), located(s), doc.activeElement.getAttribute('data-canvas-tag')];
        // A point the graphic does not draw falls back to the tag's own mark.
        out.fallback = [s.canvas.locate('P-101', 'P-101.no_such_point'), located(s)];
        // A node with no reading of the named variable falls back to a reading of the node.
        out.node = [s.canvas.locate('N-201', 'N-201.no_such_point'), located(s)];
        out.missing = [s.canvas.locate('X-999', null), located(s)];
        timers.filter(Boolean).forEach(fn => fn());
        out.expired = located(s);
        release();
        out.opened = opened.length;
        out.panels = panels(s).length;
        return out;
        """,
        console,
    )

    assert result["reading"] == [True, ["N-201.pressure"], "N-201.pressure"]
    assert result["device"] == [True, ["P-101"], "P-101"]
    assert result["fallback"] == [True, ["P-101"]]
    assert result["node"][0] is True and result["node"][1] and result["node"][1][0].startswith("N-201.")
    # Nothing to point at: the previous tint is gone and nothing new appears.
    assert result["missing"] == [False, []]
    assert result["expired"] == []
    assert (result["opened"], result["panels"]) == (0, 0)


@needs_node
def test_show_trend_opens_the_alarms_point_beside_its_mark(console):
    result = run_scene(
        TRENDS
        + """
        const s = await boot({ trends });
        const node = reading(s, 'nodes.N-201.pressure');
        const stream = reading(s, 'streams.B-PV-101.flow');
        const out = {};
        out.node = s.canvas.showTrend('N-201', 'N-201.pressure');
        out.stream = s.canvas.showTrend('B-PV-101', 'B-PV-101.flow');
        out.device = s.canvas.showTrend('P-101', 'P-101.discharge_pressure');
        out.noPoint = s.canvas.showTrend('P-101', null);
        out.opened = opened.map(o => ({ point: o.point, opener: o.opener === node ? 'node' : o.opener === stream ? 'stream'
          : o.opener.getAttribute('data-canvas-tag'), anchor: o.anchor }));
        return out;
        """,
        console,
    )

    assert result["noPoint"] is False
    assert result["opened"] == [
        {"point": "N-201.pressure", "opener": "node", "anchor": None},
        {"point": "B-PV-101.flow", "opener": "stream", "anchor": {"left": 60, "top": 500, "right": 110, "bottom": 516}},
        # A device is anchored by its own group.
        {"point": "P-101.discharge_pressure", "opener": "P-101",
         "anchor": {"left": 500, "top": 300, "right": 560, "bottom": 360}},
    ]


@needs_node
def test_without_trend_windows_show_trend_does_nothing(console):
    result = run_scene(
        """
        const s = await boot();
        return s.canvas.showTrend('N-201', 'N-201.pressure');
        """,
        console,
    )

    assert result is False


def test_a_located_mark_is_tinted_like_a_selection():
    css = CONSOLE_CSS.read_text()

    for selector in ('.plant-graphic .eq[data-located="true"] .body', '.plant-graphic .ctl[data-located="true"] .ctl-balloon',
                     'text[data-canvas-reading][data-located="true"]'):
        assert selector in css
