"""Process graphic (T16-3): static/js/graphic.js and static/graphics/plant.svg.

There is no JavaScript test runner in this repo, so the pure half of graphic.js
runs under Node (a subprocess, JSON in and out). It is fed the real SVG - every
element that carries a data-* binding is parsed out of the file and handed to
Node as an element-like - and snapshots taken from a real `TrainingSession`
through `operator_view`, the only shape a browser receives. Nothing is
restated here: the graphic is right when the file on disk, bound to what the
plant publishes, says the right thing.
"""

import copy
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.api.visibility import VISIBLE
from app.training.session import TrainingSession

ROOT = Path(__file__).resolve().parent.parent
GRAPHIC_JS = ROOT / "static" / "js" / "graphic.js"
PLANT_SVG = ROOT / "static" / "graphics" / "plant.svg"

BOUND = (
    "data-tag",
    "data-state-of",
    "data-band-of",
    "data-bind",
    "data-flow",
    "data-fill",
    "data-unplaced",
)

# A phone is 360 CSS px wide; 10px is the smallest text this console reads.
PHONE_WIDTH_PX = 360
MIN_TEXT_PX = 10

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

PRELUDE = """
const G = require(%s);
class El {
  constructor(attrs, text) { this.attrs = Object.assign({}, attrs); this.textContent = text; }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
}
const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const els = (data.elements || []).map(e => new El(e.attrs, e.text));
const dump = () => JSON.parse(JSON.stringify(els.map(e => ({ attrs: e.attrs, text: e.textContent }))));
"""


def run_js(body, **data):
    """Evaluate `body` (the body of an async function with `G`, the graphic.js
    exports, `els`, the SVG's bound elements as element-likes, `dump()` and
    `data` in scope) under Node and return its JSON result."""
    script = (
        PRELUDE % json.dumps(str(GRAPHIC_JS))
        + f"(async () => {{ {body} }})()"
        + ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        + ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def bound_elements(svg_text):
    """Every element of the SVG with a binding attribute, as the JSON the Node
    prelude turns into element-likes, in document order."""
    elements = []
    for node in ET.fromstring(svg_text).iter():
        if any(name in node.attrib for name in BOUND):
            elements.append({"attrs": dict(node.attrib), "text": (node.text or "").strip()})
    return elements


class Rendered:
    """The SVG's bound elements after `update`, queried by what they bind."""

    def __init__(self, elements, unplaced):
        self.elements = elements
        self.unplaced = unplaced

    def _only(self, **match):
        found = [
            e
            for e in self.elements
            if all(e["attrs"].get(k.replace("_", "-")) == v for k, v in match.items())
        ]
        assert found, f"nothing in the graphic matches {match}"
        return found

    def tag(self, tag):
        return self._only(data_tag=tag)[0]["attrs"]

    def bind(self, path):
        element = self._only(data_bind=path)[0]
        return element["text"], element["attrs"]

    def state_text(self, tag):
        return self._only(data_state_of=tag)[0]["text"]

    def band_text(self, tag):
        return self._only(data_band_of=tag)[0]["text"]

    def flow(self, path):
        return {e["attrs"]["data-flow-dir"] for e in self._only(data_flow=path)}

    def fill(self, path):
        return self._only(data_fill=path)[0]["attrs"]

    def note(self):
        return self._only(data_unplaced="")[0]["text"]


def render(snapshot, svg_text=None):
    elements = bound_elements(svg_text if svg_text is not None else PLANT_SVG.read_text())
    result = run_js(
        "const r = G.update(els, data.snapshot); return { elements: dump(), unplaced: r.unplaced };",
        elements=elements,
        snapshot=snapshot,
    )
    return Rendered(result["elements"], result["unplaced"])


@pytest.fixture(scope="module")
def views():
    """Operator views of the plant: free play as it starts, then the
    pump_trip scenario at the first band on V-101's level (`lo`, a warning) and at
    its trip band (`lololo`). P-101.flow sits in its own trip band from the
    moment the pump stops and backflows."""
    normal = TrainingSession()
    normal_view = normal.operator_view(normal.step(1.0))

    session = TrainingSession()
    session.load("pump_trip")
    session.start()
    out = {"normal": normal_view}
    for _ in range(600):
        view = session.operator_view(session.step(1.0))
        level = view["envelope"].get("V-101.level", {}).get("band")
        if "alarm" not in out and level == "lo":
            out["alarm"] = view
        if level == "lololo":
            out["tripped"] = view
            break
    assert set(out) == {"normal", "alarm", "tripped"}
    return out


@needs_node
def test_normal_plant_shows_running_machines_forward_flow_and_no_band(views):
    shown = render(views["normal"])

    assert shown.tag("K-101")["data-state"] == "running"
    assert shown.tag("P-101")["data-state"] == "running"
    assert shown.state_text("P-101") == "▶ RUN"
    assert shown.flow("streams.B-P-101.flow") == {"forward"}
    assert shown.flow("streams.B-LV-101.flow") == {"forward"}
    assert shown.bind("equipment.V-101.level")[0] == "50%"
    assert shown.bind("equipment.P-101.flow")[0] == "50.0 GPM"
    assert {e["attrs"].get("data-band", "none") for e in shown.elements} == {"none"}
    assert shown.unplaced == []
    assert shown.note() == ""


@needs_node
def test_alarm_state_shows_stopped_pump_reverse_flow_and_the_band_on_the_vessel(views):
    shown = render(views["alarm"])

    assert shown.tag("P-101")["data-state"] == "stopped"
    assert shown.state_text("P-101") == "■ STOP"
    assert shown.flow("streams.B-P-101.flow") == {"reverse"}
    assert shown.tag("V-101")["data-band"] == "warning"
    assert shown.band_text("V-101") == "● LO"
    assert shown.bind("equipment.V-101.level")[1]["data-band"] == "warning"
    assert shown.tag("P-101")["data-band"] == "trip"
    assert shown.band_text("P-101") == "▲ LOLOLO"
    assert shown.bind("equipment.P-101.flow")[1]["data-band"] == "trip"
    assert shown.tag("K-101")["data-band"] == "none"


@needs_node
def test_tripped_state_shows_the_trip_band_on_the_point_that_crossed_it(views):
    shown = render(views["tripped"])

    assert shown.tag("V-101")["data-band"] == "trip"
    assert shown.band_text("V-101") == "▲ LOLOLO"
    assert shown.bind("equipment.V-101.level")[1]["data-band"] == "trip"
    assert shown.bind("equipment.V-101.pressure")[1]["data-band"] == "none"
    assert shown.tag("P-101")["data-state"] == "stopped"


@needs_node
def test_an_alarm_band_reads_as_the_alarm_severity_on_its_own_device(views):
    snapshot = copy.deepcopy(views["normal"])
    snapshot["envelope"] = {"K-101.outlet_pressure": {"band": "hihi", "since": 14500.0}}

    shown = render(snapshot)

    assert shown.tag("K-101")["data-band"] == "alarm"
    assert shown.band_text("K-101") == "◆ HIHI"
    assert shown.tag("P-101")["data-band"] == "none"


@needs_node
def test_the_worst_band_on_a_device_wins(views):
    snapshot = copy.deepcopy(views["normal"])
    snapshot["envelope"] = {
        "V-101.level": {"band": "hi", "since": 1.0},
        "V-101.pressure": {"band": "hihihi", "since": 2.0},
    }

    shown = render(snapshot)

    assert shown.tag("V-101")["data-band"] == "trip"
    assert shown.band_text("V-101") == "▲ HIHIHI"
    assert shown.bind("equipment.V-101.level")[1]["data-band"] == "warning"


@needs_node
def test_vessel_fill_height_follows_level(views):
    shown = render(views["tripped"])
    attrs = shown.fill("equipment.V-101.level")
    level = views["tripped"]["equipment"]["V-101"]["level"]

    assert float(attrs["height"]) == pytest.approx(float(attrs["data-h0"]) * level)
    assert float(attrs["y"]) + float(attrs["height"]) == pytest.approx(
        float(attrs["data-y0"]) + float(attrs["data-h0"])
    )


@needs_node
def test_new_equipment_binds_from_markup_alone(views):
    svg = PLANT_SVG.read_text().replace(
        "</svg>",
        '<g class="eq" data-tag="P-102">'
        '<text data-bind="equipment.P-102.speed" data-format="percent">--</text>'
        '<text data-state-of="P-102">--</text></g></svg>',
    )
    snapshot = copy.deepcopy(views["normal"])
    snapshot["equipment"]["P-102"] = {"running": False, "speed": 0.25, "speed_target": 1.0}

    shown = render(snapshot, svg)

    assert shown.tag("P-102")["data-state"] == "stopped"
    assert shown.bind("equipment.P-102.speed")[0] == "25%"
    assert shown.state_text("P-102") == "■ STOP"
    assert shown.unplaced == []


@needs_node
def test_equipment_the_graphic_does_not_draw_is_listed_not_dropped(views):
    snapshot = copy.deepcopy(views["normal"])
    snapshot["equipment"]["P-102"] = {"running": True, "speed": 1.0}

    shown = render(snapshot)

    assert shown.unplaced == ["P-102"]
    assert shown.note() == "Not on graphic: P-102"


@needs_node
def test_a_tag_the_snapshot_lacks_degrades_to_unknown_without_error(views):
    snapshot = copy.deepcopy(views["normal"])
    del snapshot["equipment"]["K-101"]

    shown = render(snapshot)

    assert shown.tag("K-101")["data-state"] == "unknown"
    assert shown.state_text("K-101") == "--"
    assert shown.bind("equipment.K-101.load")[0] == "--"
    assert shown.bind("equipment.K-101.load")[1]["data-missing"] == "true"
    assert shown.tag("P-101")["data-state"] == "running"


@needs_node
@pytest.mark.parametrize("snapshot", [None, {}, {"equipment": None, "streams": []}, "garbage"])
def test_an_empty_or_malformed_snapshot_draws_placeholders_and_does_not_throw(snapshot):
    shown = render(snapshot)

    assert {e["attrs"]["data-state"] for e in shown.elements if "data-tag" in e["attrs"]} == {
        "unknown"
    }
    assert {e["text"] for e in shown.elements if "data-bind" in e["attrs"]} == {"--"}
    assert shown.flow("streams.B-P-101.flow") == {"unknown"}
    assert shown.fill("equipment.V-101.level")["height"] == "0"
    assert shown.unplaced == []


@needs_node
def test_a_missing_value_is_marked_and_a_present_one_is_not(views):
    snapshot = copy.deepcopy(views["normal"])
    snapshot["equipment"]["P-101"]["flow"] = None

    shown = render(snapshot)

    assert shown.bind("equipment.P-101.flow")[0] == "--"
    assert shown.bind("equipment.P-101.flow")[1]["data-missing"] == "true"
    assert "data-missing" not in shown.bind("streams.B-LV-101.flow")[1]


@needs_node
def test_a_value_that_recovers_clears_its_missing_mark(views):
    result = run_js(
        "G.update(els, {}); const first = dump();"
        "G.update(els, data.snapshot);"
        "return { first, second: dump() };",
        elements=bound_elements(PLANT_SVG.read_text()),
        snapshot=views["normal"],
    )

    def level(rows):
        return next(r for r in rows if r["attrs"].get("data-bind") == "equipment.V-101.level")

    assert level(result["first"])["attrs"]["data-missing"] == "true"
    assert "data-missing" not in level(result["second"])["attrs"]


@needs_node
def test_formatting_rounds_and_never_shows_negative_zero():
    out = run_js(
        "const f = G.formatValue; return ["
        "f(-0.04, 'fixed:1'), f(49.96, 'fixed:0', 'psia'), f(0.5, 'percent'),"
        "f(1.25), f(null), f(undefined), f(NaN), f('1'), f(Infinity, 'percent')];"
    )

    assert out == ["0.0", "50 psia", "50%", "1.3", "--", "--", "--", "--", "--"]


@needs_node
def test_flow_is_drawn_moving_only_when_clear_of_a_stopped_machines_residue():
    out = run_js(
        "return [50, -45, 0.055, -0.055, 0, null].map(G.flowDirection);",
    )

    assert out == ["forward", "reverse", "none", "none", "none", "unknown"]


@needs_node
def test_mount_loads_the_svg_and_applies_a_snapshot_given_before_it_arrived(views):
    svg = PLANT_SVG.read_text()
    out = run_js(
        """
        const container = {
          innerHTML: '', textContent: '',
          querySelectorAll() { return els; },
          setAttribute() {},
        };
        const fetch = async (url) => ({ ok: true, text: async () => data.svg });
        const mounted = G.mount(container, { fetch });
        mounted.update(data.snapshot);
        await mounted.ready;
        return { html: container.innerHTML === data.svg, rows: dump() };
        """,
        elements=bound_elements(svg),
        svg=svg,
        snapshot=views["normal"],
    )

    assert out["html"]
    state = next(r for r in out["rows"] if r["attrs"].get("data-tag") == "K-101")
    assert state["attrs"]["data-state"] == "running"


@needs_node
def test_a_binding_error_leaves_the_loaded_graphic_in_place():
    out = run_js(
        """
        const container = {
          innerHTML: '', textContent: '',
          querySelectorAll() { throw new Error('boom'); },
          setAttribute() {},
        };
        const fetch = async () => ({ ok: true, text: async () => '<svg></svg>' });
        const mounted = G.mount(container, { fetch });
        const failure = await mounted.ready.then(() => null, e => e.message);
        return { html: container.innerHTML, text: container.textContent, failure };
        """
    )

    assert out == {"html": "<svg></svg>", "text": "", "failure": "boom"}


@needs_node
def test_mount_says_so_when_the_svg_cannot_be_loaded():
    out = run_js(
        """
        const roles = [];
        const container = { innerHTML: '', textContent: '', setAttribute(k, v) { roles.push([k, v]); } };
        const fetch = async () => ({ ok: false, status: 404 });
        const mounted = G.mount(container, { fetch });
        mounted.update({});
        await mounted.ready;
        return { text: container.textContent, roles };
        """
    )

    assert out == {"text": "Process graphic unavailable", "roles": [["role", "status"]]}


# ---- the file itself, against what the plant publishes -----------------------


def svg_paths(attribute):
    return [
        node.attrib[attribute]
        for node in ET.fromstring(PLANT_SVG.read_text()).iter()
        if attribute in node.attrib
    ]


def resolve(view, path):
    node = view
    for part in path.split("."):
        node = node[part]
    return node


def test_every_binding_in_the_svg_resolves_to_a_number_on_the_real_plant(views):
    paths = svg_paths("data-bind") + svg_paths("data-flow") + svg_paths("data-fill")

    assert paths
    for path in paths:
        value = resolve(views["normal"], path)
        assert isinstance(value, (int, float)), path


def test_every_device_the_svg_draws_is_in_the_plant_and_every_one_in_it_is_drawn(views):
    drawn = {tag for tag in svg_paths("data-tag")}

    assert drawn == set(views["normal"]["equipment"])


def test_every_solved_node_the_plant_publishes_has_a_pressure_readout(views):
    shown = {path.split(".")[1] for path in svg_paths("data-bind") if path.startswith("nodes.")}

    assert shown == set(views["normal"]["nodes"])


def test_the_svg_binds_only_fields_the_operator_view_publishes(views):
    visible = set().union(*VISIBLE.values())
    fields = {
        path.split(".")[2] for path in svg_paths("data-bind") if path.startswith("equipment.")
    } | {path.split(".")[2] for path in svg_paths("data-fill")}

    assert fields <= visible
    for view in views.values():
        for row in view["equipment"].values():
            assert set(row) <= visible


# Which plant each graphic draws; T20-2 makes the plant choose its graphic.
GRAPHIC_PLANTS = {"plant.svg": "olefins_lite"}


def test_every_unit_a_graphic_labels_is_the_servers_unit_for_that_point():
    session = TrainingSession()
    try:
        units = {"olefins_lite": session.trend_units()}
    finally:
        session.end()

    graphics = sorted((ROOT / "static" / "graphics").glob("*.svg"))
    assert graphics
    for graphic in graphics:
        assert graphic.name in GRAPHIC_PLANTS, f"{graphic.name} draws no known plant"
        served = units[GRAPHIC_PLANTS[graphic.name]]
        labelled = 0

        for node in ET.fromstring(graphic.read_text()).iter():
            path = node.attrib.get("data-bind")
            if path is None:
                continue
            point = path.split(".", 1)[1]
            if node.attrib.get("data-format") == "percent":
                assert served[point] == "fraction", (graphic.name, point)
                labelled += 1
            elif "data-unit" in node.attrib:
                assert node.attrib["data-unit"] == served[point], (graphic.name, point)
                labelled += 1

        assert labelled, graphic.name


def test_the_svg_is_well_formed_responsive_and_legible_at_phone_width():
    text = PLANT_SVG.read_text()
    root = ET.fromstring(text)
    x, y, width, height = (float(n) for n in root.attrib["viewBox"].split())
    sizes = [float(n) for n in re.findall(r"font-size:\s*([\d.]+)px", text)]

    assert "width" not in root.attrib and "height" not in root.attrib
    assert sizes
    assert min(sizes) * PHONE_WIDTH_PX / width >= MIN_TEXT_PX
    assert height / width < 1.0


def test_the_svg_takes_colour_from_tokens_never_a_literal():
    text = PLANT_SVG.read_text()

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text)
    assert not re.search(r"\b(?:rgb|hsl)a?\(", text)
    used = set(re.findall(r"var\((--[a-z-]+)\)", text))
    defined = set(re.findall(r"(--[a-z-]+):", (ROOT / "static" / "css" / "tokens.css").read_text()))
    assert used <= defined


def test_every_state_and_band_the_script_can_set_has_a_style_in_the_svg():
    text = PLANT_SVG.read_text()

    for state in ("running", "stopped", "unknown"):
        assert f'data-state="{state}"' in text
    for band in ("warning", "alarm", "trip"):
        assert f'data-band="{band}"' in text
    for direction in ("forward", "reverse", "none"):
        assert f'data-flow-dir="{direction}"' in text
