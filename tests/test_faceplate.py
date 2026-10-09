"""Controller faceplates (T16-4): static/js/faceplate.js and static/css/faceplate.css.

There is no JavaScript test runner in this repo, so the pure half of
faceplate.js runs under Node (a subprocess, JSON in and out), fed controllers
rows from a real `TrainingSession` through `operator_view`, the only shape a
browser receives. Commands are checked end to end: the request faceplate.js
builds is posted to the real action endpoint on that session, the session
steps, and the next row - read back through faceplate.js - shows the change.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from app.api.action import create_action_blueprint
from app.training.session import TrainingSession

ROOT = Path(__file__).resolve().parent.parent
FACEPLATE_JS = ROOT / "static" / "js" / "faceplate.js"
FACEPLATE_CSS = ROOT / "static" / "css" / "faceplate.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(body, **data):
    """Evaluate `body` (the body of an async function with `F`, the
    faceplate.js exports, and `data` in scope) under Node; return its JSON."""
    script = (
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
def session():
    session = TrainingSession()

    yield session

    session.end()


@pytest.fixture
def client(session):
    app = Flask(__name__)
    app.register_blueprint(create_action_blueprint(apply=session.act))

    return app.test_client()


def view(session):
    return session.operator_view(session.training_scheduler.snapshot())


def step(session):
    session.training_scheduler.step_once()

    return view(session)


def send(client, entry):
    """Post the request faceplate.js builds for one parsed entry or action."""
    request = run_js(
        "return F.buildActionRequest(data.tag, data.action, data.value);",
        tag="PIC-101", action=entry["action"], value=entry.get("value"),
    )
    init = request["init"]

    return client.open(
        request["url"], method=init["method"], headers=init["headers"], data=init["body"],
    )


def parse(kind, text, row):
    return run_js("return F.parseEntry(data.kind, data.text, data.row);", kind=kind, text=text, row=row)


def model(row):
    return run_js("return F.model('PIC-101', data.row);", row=row)


# ---- Faceplate reflects loop state ----


@needs_node
def test_the_faceplate_shows_the_loop_as_the_snapshot_publishes_it(session):
    row = view(session)["controllers"]["PIC-101"]

    shown = model(row)

    percent = (row["out"] - row["out_min"]) / (row["out_max"] - row["out_min"]) * 100
    assert shown["mode"] == "MANUAL" and shown["modeLabel"] == "MAN" and shown["manual"] is True
    assert shown["pv"] == f"{row['pv']:.1f} psia"
    assert shown["sp"] == f"{row['sp']:.1f} psia"
    assert shown["out"] == f"{percent:.1f} %"
    assert shown["outPercent"] == pytest.approx(percent)
    assert shown["tunable"] is True
    assert shown["gains"] == {"kp": "0.01", "ki": "0.005", "kd": "0"}


@needs_node
def test_one_faceplate_per_published_loop_in_snapshot_order(session):
    snapshot = view(session)

    assert run_js("return F.controllerTags(data.snapshot);", snapshot=snapshot) == list(snapshot["controllers"])
    assert run_js("return F.controllerTags({});") == []


@needs_node
def test_a_row_missing_fields_degrades_to_dashes():
    shown = model({"mode": "AUTO"})

    assert (shown["pv"], shown["sp"], shown["out"], shown["outPercent"]) == ("--", "--", "--", None)
    assert shown["gains"] == {"kp": "--", "ki": "--", "kd": "--"}
    assert shown["tunable"] is False


# ---- Manual output entry is bounded ----


@needs_node
@pytest.mark.parametrize("text", ["100.5", "-1", "150"])
def test_an_output_outside_0_to_100_percent_is_refused_before_posting(session, text):
    row = view(session)["controllers"]["PIC-101"]

    assert parse("out", text, row) == {"error": "Output must be between 0 and 100 %."}


@needs_node
@pytest.mark.parametrize("text", ["", "abc", "1e", "0x10", "Infinity"])
def test_an_entry_that_is_not_a_plain_number_is_refused(session, text):
    row = view(session)["controllers"]["PIC-101"]

    assert parse("out", text, row) == {"error": "Enter a number."}


@needs_node
def test_an_output_maps_percent_onto_the_loops_range(session):
    row = view(session)["controllers"]["PIC-101"]

    assert parse("out", "60", row) == {"action": "set_output", "value": pytest.approx(0.6)}
    assert parse("out", "100", row) == {"action": "set_output", "value": row["out_max"]}
    assert parse("out", "0", row) == {"action": "set_output", "value": row["out_min"]}
    assert parse("out", "50", dict(row, out_min=0.2, out_max=0.6)) == {
        "action": "set_output", "value": pytest.approx(0.4),
    }


@needs_node
def test_an_output_entry_outside_manual_is_refused(session):
    row = dict(view(session)["controllers"]["PIC-101"], mode="AUTO")

    assert parse("out", "50", row) == {"error": "Switch to MAN to set the output."}


@needs_node
def test_the_server_bounds_the_output_too(session, client):
    send(client, {"action": "manual"})

    for value in (1.5, -0.1):
        refused = send(client, {"action": "set_output", "value": value})
        assert refused.status_code == 400
        assert "output range" in refused.get_json()["error"]


# ---- Tuning editable where permitted ----


@needs_node
def test_tuning_is_parsed_only_for_a_tunable_loop(session):
    row = view(session)["controllers"]["PIC-101"]

    assert parse("kp", "0.02", row) == {"action": "set_kp", "value": 0.02}
    assert parse("kd", "-1", row) == {"error": "Gains cannot be negative."}
    assert parse("ki", "0.01", dict(row, tunable=False)) == {"error": "Tuning is locked for this loop."}


@needs_node
def test_a_negative_setpoint_is_refused(session):
    row = view(session)["controllers"]["PIC-101"]

    assert parse("sp", "-5", row) == {"error": "Setpoint cannot be negative."}
    assert parse("sp", "195", row) == {"action": "set_setpoint", "value": 195}


# ---- Mode change round-trips to the engine ----


@needs_node
def test_mode_switching_round_trips_to_the_engine(session, client):
    assert model(view(session)["controllers"]["PIC-101"])["mode"] == "MANUAL"

    assert send(client, {"action": "auto"}).status_code == 200
    assert model(step(session)["controllers"]["PIC-101"])["modeLabel"] == "AUTO"
    assert session.free.engine.loops["PIC-101"].loop.mode.name == "AUTO"

    assert send(client, {"action": "manual"}).status_code == 200
    assert model(step(session)["controllers"]["PIC-101"])["modeLabel"] == "MAN"


@needs_node
def test_entries_round_trip_to_the_engine(session, client):
    row = view(session)["controllers"]["PIC-101"]

    assert send(client, parse("out", "70", row)).status_code == 200
    assert send(client, parse("sp", "195", row)).status_code == 200
    assert send(client, parse("kp", "0.02", row)).status_code == 200

    shown = model(step(session)["controllers"]["PIC-101"])
    assert shown["out"] == "70.0 %"
    assert shown["sp"] == "195.0 psia"
    assert shown["gains"]["kp"] == "0.02"


@needs_node
def test_a_refusal_comes_back_as_text_the_faceplate_can_show(session, client):
    refused = send(client, {"action": "set_kp", "value": -1.0})

    assert refused.status_code == 400
    assert "non-negative" in refused.get_json()["error"]


# ---- Styling follows the console standards ----


def test_the_stylesheet_uses_tokens_only():
    css = FACEPLATE_CSS.read_text()
    # A name the stylesheet declares itself (a spacing variable) is its own;
    # every other var() must be a token.
    defined = set(re.findall(r"(--[\w-]+)\s*:", TOKENS_CSS.read_text()))
    defined |= set(re.findall(r"(--[\w-]+)\s*:", css))

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)
    assert set(re.findall(r"var\((--[\w-]+)\)", css)) <= defined


# ---- The browser glue, over a minimal DOM ----
#
# Just enough of a DOM for faceplate.js's glue: elements with attributes,
# children, text, listeners and a style, and tag-name queries. It checks what
# a click and a submit do, not how anything looks; that was seen in a browser.

FAKE_DOM = """
class Node {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.children = [];
    this.listeners = {}; this.style = {}; this._text = ''; this.value = ''; this.disabled = false;
    this.placeholder = ''; this.ownerDocument = doc; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(t) { this.children = []; this._text = String(t); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  fire(type) { (this.listeners[type] || []).forEach(fn => fn({ preventDefault() {} })); }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  querySelectorAll(sel) { const tags = sel.split(',').map(s => s.trim().toUpperCase());
    return this.all().filter(n => tags.includes(n.tagName)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const doc = { createElement: tag => new Node(tag) };
const container = new Node('div');
const posted = [];
let reply = { ok: true, status: 200, body: { ok: true } };
const fetch = (url, init) => { posted.push({ url, body: JSON.parse(init.body) });
  return Promise.resolve({ ok: reply.ok, status: reply.status, json: () => Promise.resolve(reply.body) }); };
const plates = F.mount(container, { fetch });
const settle = () => new Promise(r => setTimeout(r, 0));
const plate = tag => container.children.find(c => c.getAttribute('data-tag') === tag);
const find = (tag, pred) => plate(tag).all().find(pred);
const form = (tag, kind) => find(tag, n => n.getAttribute('data-entry') === kind);
const message = tag => find(tag, n => n.getAttribute('class') === 'faceplate-message');
"""


def run_glue(body, **data):
    return run_js(FAKE_DOM + body, **data)


@needs_node
def test_a_mode_button_posts_its_action(session):
    result = run_glue(
        """
        plates.update(data.snapshot);
        find('PIC-101', n => n.getAttribute('data-action') === 'auto').fire('click');
        await settle();
        return posted;
        """,
        snapshot=view(session),
    )

    assert result == [{"url": "/api/action", "body": {"target": "PIC-101", "action": "auto", "value": None}}]


@needs_node
def test_a_bad_output_entry_is_shown_and_never_posted(session):
    result = run_glue(
        """
        plates.update(data.snapshot);
        const out = form('PIC-101', 'out');
        out.querySelector('input').value = '150';
        out.fire('submit');
        await settle();
        return { posted, message: message('PIC-101').textContent,
                 kind: message('PIC-101').getAttribute('data-kind') };
        """,
        snapshot=view(session),
    )

    assert result == {"posted": [], "message": "Output must be between 0 and 100 %.", "kind": "refused"}


@needs_node
def test_a_server_refusal_is_shown_and_the_entry_kept(session):
    result = run_glue(
        """
        reply = { ok: false, status: 400, body: { error: 'PIC-101 is not open to operator tuning' } };
        plates.update(data.snapshot);
        const kp = form('PIC-101', 'kp');
        kp.querySelector('input').value = '0.02';
        kp.fire('submit');
        await settle(); await settle();
        return { message: message('PIC-101').textContent, kept: kp.querySelector('input').value };
        """,
        snapshot=view(session),
    )

    assert result == {"message": "PIC-101 is not open to operator tuning", "kept": "0.02"}


@needs_node
def test_an_accepted_entry_is_cleared_and_a_new_snapshot_never_overwrites_typing(session):
    result = run_glue(
        """
        plates.update(data.snapshot);
        const sp = form('PIC-101', 'sp');
        sp.querySelector('input').value = '195';
        sp.fire('submit');
        await settle(); await settle();
        const cleared = sp.querySelector('input').value;
        const out = form('PIC-101', 'out');
        out.querySelector('input').value = '4';
        plates.update(data.snapshot);
        return { cleared, typing: out.querySelector('input').value, posted,
                 sameElement: plate('PIC-101') === container.children[0] };
        """,
        snapshot=view(session),
    )

    assert result["cleared"] == ""
    assert result["typing"] == "4"
    assert result["posted"][0]["body"] == {"target": "PIC-101", "action": "set_setpoint", "value": 195}
    assert result["sameElement"] is True


@needs_node
def test_controls_follow_mode_and_tunability(session):
    snapshot = view(session)
    auto_locked = json.loads(json.dumps(snapshot))
    auto_locked["controllers"]["PIC-101"].update(mode="AUTO", tunable=False)

    result = run_glue(
        """
        const state = () => ({
          outEnabled: !form('PIC-101', 'out').querySelector('input').disabled,
          kpEnabled: !form('PIC-101', 'kp').querySelector('input').disabled,
          summary: find('PIC-101', n => n.tagName === 'SUMMARY').textContent,
          manPressed: find('PIC-101', n => n.getAttribute('data-action') === 'manual').getAttribute('aria-pressed'),
          bar: find('PIC-101', n => n.getAttribute('class') === 'faceplate-bar-fill').style.width,
        });
        plates.update(data.manual);
        const manual = state();
        plates.update(data.auto);
        return { manual, auto: state() };
        """,
        manual=snapshot,
        auto=auto_locked,
    )

    assert result["manual"] == {
        "outEnabled": True, "kpEnabled": True, "summary": "Tuning", "manPressed": "true", "bar": "50%",
    }
    assert result["auto"]["outEnabled"] is False
    assert result["auto"]["kpEnabled"] is False
    assert result["auto"]["summary"] == "Tuning (locked)"
    assert result["auto"]["manPressed"] == "false"


@needs_node
def test_live_values_carry_the_attribute_that_dims_them_when_stale(session):
    result = run_glue(
        """
        plates.update(data.snapshot);
        return ['faceplate-mode', 'faceplate-values', 'faceplate-bar'].map(cls =>
          find('PIC-101', n => n.getAttribute('class') === cls).getAttribute('data-live-value'));
        """,
        snapshot=view(session),
    )

    assert result == ["", "", ""]


@needs_node
def test_a_plant_with_no_loops_says_so():
    result = run_glue(
        """
        plates.update({ controllers: {} });
        return container.textContent;
        """,
    )

    assert result == "No controllers in this plant."


@needs_node
def test_text_typed_while_a_command_is_in_flight_is_not_cleared(session):
    result = run_glue(
        """
        plates.update(data.snapshot);
        const sp = form('PIC-101', 'sp');
        const input = sp.querySelector('input');
        input.value = '195';
        sp.fire('submit');
        input.value = '196';   // typed before the reply comes back
        await settle(); await settle();
        return { input: input.value, posted };
        """,
        snapshot=view(session),
    )

    assert result["input"] == "196"
    assert result["posted"][0]["body"]["value"] == 195


@needs_node
def test_a_snapshot_with_no_controllers_section_leaves_the_faceplates_alone(session):
    result = run_glue(
        """
        plates.update(data.snapshot);
        const first = plate('PIC-101');
        const out = form('PIC-101', 'out').querySelector('input');
        out.value = '40';
        plates.update({});
        plates.update({ controllers: undefined });
        const kept = { same: plate('PIC-101') === first, typed: out.value };
        plates.update({ controllers: {} });
        return { kept, afterEmpty: container.textContent };
        """,
        snapshot=view(session),
    )

    assert result["kept"] == {"same": True, "typed": "40"}
    assert result["afterEmpty"] == "No controllers in this plant."


@needs_node
def test_the_output_bar_is_a_valid_meter_or_hidden_from_assistive_tech(session):
    snapshot = view(session)
    unknown = json.loads(json.dumps(snapshot))
    del unknown["controllers"]["PIC-101"]["out"]

    result = run_glue(
        """
        const bar = () => find('PIC-101', n => n.getAttribute('role') === 'meter');
        const read = () => ({ now: bar().getAttribute('aria-valuenow'), hidden: bar().getAttribute('aria-hidden') });
        plates.update(data.known);
        const known = read();
        plates.update(data.unknown);
        const missing = read();
        plates.update(data.known);
        return { known, missing, back: read() };
        """,
        known=snapshot,
        unknown=unknown,
    )

    assert result["known"] == {"now": "50.0", "hidden": None}
    assert result["missing"] == {"now": None, "hidden": "true"}
    assert result["back"] == result["known"]
