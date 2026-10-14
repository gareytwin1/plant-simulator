"""Faceplates (T16-4, T20-10): static/js/faceplate.js and static/css/faceplate.css.

There is no JavaScript test runner in this repo, so faceplate.js runs under
Node (a subprocess, JSON in and out), fed rows from a real `TrainingSession`
through `operator_view`, the only shape a browser receives, and the plant
description and trend listing the console's own routes serve. Commands are
checked end to end: the request faceplate.js builds is posted to the real
action endpoint on that session, the session steps, and the next row - read
back through faceplate.js - shows the change. The panels are built over a
small fake DOM (tests/fake_dom.js): it checks what a click and a submit do,
not how anything looks, which was seen in a browser.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from app import main
from app.api.action import create_action_blueprint
from app.training.session import TrainingSession

ROOT = Path(__file__).resolve().parent.parent
FACEPLATE_JS = ROOT / "static" / "js" / "faceplate.js"
FACEPLATE_CSS = ROOT / "static" / "css" / "faceplate.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"
FAKE_DOM = (ROOT / "tests" / "fake_dom.js").read_text()


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


@pytest.fixture
def plant():
    """What the console reads to know the plant: GET /api/plant and the trend
    listing's words and units, as the browser receives them."""
    http = main.app.test_client()
    http.get("/console")
    try:
        yield {
            "plant": http.get("/api/plant").get_json(),
            "vocab": http.get("/api/trend/points").get_json(),
        }
    finally:
        main.sessions.end(http.get_cookie(main.SESSION_COOKIE).value)


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


def device_model(tag, info, snapshot, vocab):
    return run_js(
        "return F.deviceModel(data.tag, data.info, data.snapshot, data.vocab);",
        tag=tag, info=info, snapshot=snapshot, vocab=vocab,
    )


# ---- A loop faceplate reflects loop state ----


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


@needs_node
def test_a_row_missing_fields_degrades_to_dashes():
    shown = model({"mode": "AUTO"})

    assert (shown["pv"], shown["sp"], shown["out"], shown["outPercent"]) == ("--", "--", "--", None)


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


@needs_node
def test_a_negative_setpoint_is_refused(session):
    row = view(session)["controllers"]["PIC-101"]

    assert parse("sp", "-5", row) == {"error": "Setpoint cannot be negative."}
    assert parse("sp", "195", row) == {"action": "set_setpoint", "value": 195}


# ---- A loop measuring a fraction reads and takes percent (T20-4) ----

LEVEL_ROW = {"pv": 0.5, "sp": 0.55, "out": 0.4, "mode": "AUTO", "out_min": 0.0, "out_max": 1.0, "pv_unit": "fraction"}


@needs_node
def test_a_fraction_pv_and_sp_show_as_a_percent():
    shown = model(LEVEL_ROW)

    assert (shown["pv"], shown["sp"], shown["unit"]) == ("50.0 %", "55.0 %", "%")


@needs_node
@pytest.mark.parametrize("text, value", [("40", 0.4), ("0", 0.0), ("100", 1)])
def test_a_fraction_setpoint_is_entered_in_percent_and_posted_as_the_fraction(text, value):
    assert parse("sp", text, LEVEL_ROW) == {"action": "set_setpoint", "value": pytest.approx(value)}


@needs_node
@pytest.mark.parametrize("text", ["-1", "100.5"])
def test_a_fraction_setpoint_outside_0_to_100_percent_is_refused_before_posting(text):
    assert parse("sp", text, LEVEL_ROW) == {"error": "Setpoint must be between 0 and 100 %."}


@needs_node
def test_a_fraction_setpoint_is_described_in_percent():
    words = run_js("return F.describeCommand('LIC-301', 'set_setpoint', 0.4, data.row);", row=LEVEL_ROW)

    assert words == "setpoint 40.0 % for LIC-301"


@needs_node
def test_a_temperature_setpoint_may_be_below_zero():
    row = dict(LEVEL_ROW, pv=-35.0, sp=-40.0, pv_unit="°F")

    assert parse("sp", "-45", row) == {"action": "set_setpoint", "value": -45}
    assert parse("sp", "-459.67", row) == {"error": "Setpoint must be above absolute zero."}


# ---- There is no tuning on the console ----


@needs_node
@pytest.mark.parametrize("kind", ["kp", "ki", "kd"])
def test_a_gain_is_not_an_entry_a_faceplate_parses(session, kind):
    row = view(session)["controllers"]["PIC-101"]

    assert parse(kind, "0.02", row) == {"error": "Unknown entry."}


def test_no_script_or_page_of_the_console_offers_tuning():
    pages = [*(ROOT / "static" / "js").glob("*.js"), ROOT / "templates" / "console.html", FACEPLATE_CSS]

    for page in pages:
        assert not re.search(r"set_k[pid]|Tuning|faceplate-tuning", page.read_text()), page.name


# ---- Mode change and entries round-trip to the engine ----


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

    shown = model(step(session)["controllers"]["PIC-101"])
    assert shown["out"] == "70.0 %"
    assert shown["sp"] == "195.0 psia"


@needs_node
def test_a_refusal_comes_back_as_text_the_faceplate_can_show(session, client):
    refused = client.post("/api/action", json={"target": "PIC-101", "action": "set_output", "value": 9.0})

    assert refused.status_code == 400
    assert "output range" in refused.get_json()["error"]


# ---- A device faceplate shows what the plant lists for it ----


@needs_node
def test_a_device_lists_its_numeric_readings_in_the_plants_order(session, plant):
    info = plant["plant"]["equipment"]["K-101"]
    shown = device_model("K-101", info, view(session), plant["vocab"])

    assert [r["point"] for r in shown["readings"]] == [p for p in info["points"] if p != "K-101.running"]
    assert shown["kind"] == "Gas compressor"
    assert shown["drivenBy"] is None


@needs_node
def test_a_reading_carries_the_trend_listings_words_and_unit(session, plant):
    snapshot = view(session)
    shown = {r["point"]: r for r in device_model("K-101", plant["plant"]["equipment"]["K-101"], snapshot, plant["vocab"])["readings"]}

    assert shown["K-101.outlet_pressure"]["label"] == "Discharge pressure"
    assert shown["K-101.outlet_pressure"]["text"] == f"{snapshot['equipment']['K-101']['outlet_pressure']:.1f} psia"
    # A 0..1 fraction reads as a percent, as the schematic's own readings do.
    assert shown["K-101.load"]["text"] == f"{snapshot['equipment']['K-101']['load'] * 100:.1f} %"
    # A stream the device sits on is one of its readings, named so it is not "Flow" twice.
    assert (shown["K-101.flow"]["label"], shown["B-K-101.flow"]["label"]) == ("Flow", "Flow (stream)")
    assert shown["B-K-101.flow"]["text"] == f"{snapshot['streams']['B-K-101']['flow']:.1f} SCFM"


@needs_node
def test_a_reading_with_no_listing_shows_its_field_and_a_bare_number(session, plant):
    info = plant["plant"]["equipment"]["V-101"]
    shown = device_model("V-101", info, view(session), None)

    assert [r["label"] for r in shown["readings"]] == ["Level", "Pressure"]
    assert shown["readings"][0]["text"] == f"{view(session)['equipment']['V-101']['level']:.1f}"


@needs_node
def test_a_device_shows_its_run_state_and_worst_band(session, plant):
    snapshot = view(session)
    info = plant["plant"]["equipment"]["K-101"]
    banded = json.loads(json.dumps(snapshot))
    banded["envelope"] = {"K-101.outlet_pressure": {"band": "hi"}, "K-101.flow": {"band": "hihihi"}}
    banded["equipment"]["K-101"]["running"] = False

    quiet = device_model("K-101", info, snapshot, plant["vocab"])
    loud = device_model("K-101", info, banded, plant["vocab"])

    assert (quiet["stateText"], quiet["severity"], quiet["bandText"]) == ("▶ RUN", None, "")
    assert (loud["stateText"], loud["severity"], loud["bandText"]) == ("■ STOP", "trip", "▲ HIHI")
    by_point = {r["point"]: r for r in loud["readings"]}
    assert (by_point["K-101.outlet_pressure"]["severity"], by_point["K-101.outlet_pressure"]["bandText"]) == ("warning", "● HI")
    assert by_point["K-101.load"]["severity"] is None


@needs_node
def test_a_device_a_tripped_interlock_holds_shows_trip(session, plant):
    tripped = json.loads(json.dumps(view(session)))
    tripped["interlocks"]["PSHH-101"]["state"] = "tripped"
    tripped["equipment"]["K-101"]["running"] = False

    shown = device_model("K-101", plant["plant"]["equipment"]["K-101"], tripped, plant["vocab"])

    assert (shown["state"], shown["stateText"]) == ("tripped", "✖ TRIP")

    # A valve has no run flag and so no badge, until an interlock holds it.
    lv = plant["plant"]["equipment"]["LV-101"]
    assert device_model("LV-101", lv, tripped, plant["vocab"])["stateText"] == ""
    tripped["interlocks"]["LSLL-101"]["state"] = "tripped"
    assert device_model("LV-101", lv, tripped, plant["vocab"])["stateText"] == "✖ TRIP"


@needs_node
def test_a_device_says_which_loop_drives_it(session, plant):
    driven = device_model("PV-101", plant["plant"]["equipment"]["PV-101"], view(session), plant["vocab"])
    free = device_model("P-101", plant["plant"]["equipment"]["P-101"], view(session), plant["vocab"])

    assert driven["drivenBy"] == "PIC-101"
    assert free["drivenBy"] is None


@needs_node
def test_a_device_the_snapshot_lacks_degrades_to_dashes(plant):
    info = plant["plant"]["equipment"]["P-101"]

    shown = device_model("P-101", info, {}, plant["vocab"])

    assert all(r["text"] == "--" for r in shown["readings"])
    assert shown["stateText"] == ""
    assert device_model("X-1", None, {}, None)["readings"] == []


@needs_node
def test_a_command_is_described_in_the_operators_words(session):
    row = view(session)["controllers"]["PIC-101"]

    def words(action, value):
        return run_js("return F.describeCommand('PIC-101', data.action, data.value, data.row);", action=action, value=value, row=row)

    assert words("auto", None) == "switch PIC-101 to AUTO"
    assert words("set_setpoint", 195) == "setpoint 195 psia for PIC-101"
    assert words("set_output", 0.75) == "output 75.0 % for PIC-101"
    assert run_js("return F.acceptedText('output 75.0 % for PIC-101');") == (
        "Output 75.0 % for PIC-101 accepted. The plant responds over the next steps."
    )


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

PANELS = (
    FAKE_DOM
    + """
const posted = [];
let reply = { ok: true, status: 200, body: { ok: true } };
const fetch = (url, init) => { posted.push({ url, body: JSON.parse(init.body) });
  return Promise.resolve({ ok: reply.ok, status: reply.status, json: () => Promise.resolve(reply.body) }); };
const post = F.makePost(fetch);
const opened = [];
const ctx = info => ({ info, vocab: data.vocab, interlocks: data.plant.interlocks, post, close: () => opened.push('close'),
  open: (kind, tag) => opened.push([kind, tag]) });
const loop = () => F.createPanel(doc, 'loop', 'PIC-101', ctx(data.plant.controllers['PIC-101']));
const device = tag => F.createPanel(doc, 'device', tag, ctx(data.plant.equipment[tag]));
const find = (panel, pred) => panel.element.all().find(pred);
const form = (panel, kind) => find(panel, n => n.getAttribute('data-entry') === kind);
const message = panel => find(panel, n => n.classes.includes('fp-feedback'));
const input = (panel, kind) => form(panel, kind).querySelector('input');
const button = (panel, action) => find(panel, n => n.getAttribute('data-action') === action);
"""
)


def run_panels(body, plant, **data):
    return run_js(PANELS + body, plant=plant["plant"], vocab=plant["vocab"], **data)


@needs_node
def test_a_mode_button_posts_its_action(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        button(panel, 'auto').fire('click');
        await settle();
        return posted;
        """,
        plant, snapshot=view(session),
    )

    assert result == [{"url": "/api/action", "body": {"target": "PIC-101", "action": "auto", "value": None}}]


@needs_node
def test_a_bad_output_entry_is_shown_and_never_posted(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        input(panel, 'out').value = '150';
        form(panel, 'out').fire('submit');
        await settle();
        return { posted, message: message(panel).textContent, kind: message(panel).getAttribute('data-kind') };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"posted": [], "message": "Output must be between 0 and 100 %.", "kind": "rejected"}


@needs_node
def test_a_server_refusal_is_shown_and_the_entry_kept(session, plant):
    result = run_panels(
        """
        reply = { ok: false, status: 400, body: { error: 'PIC-101 setpoint is outside the PV range' } };
        const panel = loop();
        panel.update(data.snapshot);
        input(panel, 'sp').value = '9999';
        form(panel, 'sp').fire('submit');
        await settle(); await settle();
        return { message: message(panel).textContent, kind: message(panel).getAttribute('data-kind'),
                 kept: input(panel, 'sp').value };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"message": "PIC-101 setpoint is outside the PV range", "kind": "rejected", "kept": "9999"}


@needs_node
def test_an_accepted_entry_reads_accepted_never_done_and_the_value_waits_for_the_snapshot(session, plant):
    after = json.loads(json.dumps(view(session)))
    after["controllers"]["PIC-101"]["sp"] = 195.0

    result = run_panels(
        """
        const panel = loop();
        panel.update(data.before);
        const shown = () => find(panel, n => n.classes.includes('fp-reading') && n.textContent.startsWith('Setpoint'))
          .textContent;
        input(panel, 'sp').value = '195';
        form(panel, 'sp').fire('submit');
        const sending = message(panel).textContent;
        await settle(); await settle();
        const accepted = { text: message(panel).textContent, kind: message(panel).getAttribute('data-kind'),
                           value: shown(), cleared: input(panel, 'sp').value };
        panel.update(data.after);
        return { sending, accepted, carried: shown(), posted };
        """,
        plant, before=view(session), after=after,
    )

    assert result["sending"] == "Sending setpoint 195 psia for PIC-101…"
    assert result["accepted"]["kind"] == "accepted"
    assert result["accepted"]["text"] == "Setpoint 195 psia for PIC-101 accepted. The plant responds over the next steps."
    assert "done" not in result["accepted"]["text"].lower()
    # Accepted is not carried out: the faceplate still shows the plant's own value.
    assert result["accepted"]["value"] == "Setpoint (SP)200.0 psia"
    assert result["accepted"]["cleared"] == ""
    assert result["carried"] == "Setpoint (SP)195.0 psia"
    assert result["posted"][0]["body"] == {"target": "PIC-101", "action": "set_setpoint", "value": 195}


@needs_node
def test_a_new_snapshot_never_overwrites_what_is_being_typed(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        const element = panel.element;
        input(panel, 'out').value = '4';
        panel.update(data.snapshot);
        return { typing: input(panel, 'out').value, same: panel.element === element };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"typing": "4", "same": True}


@needs_node
def test_text_typed_while_a_command_is_in_flight_is_not_cleared(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        const entry = input(panel, 'sp');
        entry.value = '195';
        form(panel, 'sp').fire('submit');
        entry.value = '196';   // typed before the reply comes back
        await settle(); await settle();
        return { input: entry.value, posted };
        """,
        plant, snapshot=view(session),
    )

    assert result["input"] == "196"
    assert result["posted"][0]["body"]["value"] == 195


@needs_node
def test_output_entry_is_disabled_outside_manual_and_says_why(session, plant):
    snapshot = view(session)
    auto = json.loads(json.dumps(snapshot))
    auto["controllers"]["PIC-101"]["mode"] = "AUTO"

    result = run_panels(
        """
        const panel = loop();
        const state = () => ({
          disabled: input(panel, 'out').disabled,
          setDisabled: form(panel, 'out').querySelector('button').disabled,
          note: find(panel, n => n.getAttribute('id') === 'fp-PIC-101-out-note').textContent,
          described: input(panel, 'out').getAttribute('aria-describedby'),
          manPressed: button(panel, 'manual').getAttribute('aria-pressed'),
          bar: find(panel, n => n.classes.includes('fp-bar-fill')).style.width,
          spDisabled: input(panel, 'sp').disabled,
        });
        panel.update(data.manual);
        const manual = state();
        panel.update(data.auto);
        return { manual, auto: state() };
        """,
        plant, manual=snapshot, auto=auto,
    )

    assert result["manual"] == {
        "disabled": False, "setDisabled": False, "note": "", "manPressed": "true", "bar": "50%", "spDisabled": False,
        "described": "fp-PIC-101-out-note fp-PIC-101-out-unit",
    }
    assert result["auto"]["disabled"] is True and result["auto"]["setDisabled"] is True
    assert result["auto"]["note"] == "Output is set by the loop in AUTO. Switch to MAN to set it."
    assert result["auto"]["manPressed"] == "false"
    # The note the input is described by exists, so the reason is announced with the control.
    assert result["auto"]["described"].startswith("fp-PIC-101-out-note")
    assert result["auto"]["spDisabled"] is False


@needs_node
def test_a_fraction_loops_setpoint_entry_is_in_percent_and_posts_the_fraction(session, plant):
    snapshot = json.loads(json.dumps(view(session)))
    snapshot["controllers"]["PIC-101"].update(pv=0.5, sp=0.55, pv_unit="fraction")
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        const entry = form(panel, 'sp');
        const shown = { label: entry.querySelector('label').textContent,
                        unit: entry.querySelector('.visually-hidden').textContent,
                        placeholder: input(panel, 'sp').placeholder };
        input(panel, 'sp').value = '40';
        entry.fire('submit');
        await settle();
        return { shown, posted };
        """,
        plant, snapshot=snapshot,
    )

    assert result["shown"] == {"label": "Setpoint (%)", "unit": "percent", "placeholder": "55.0"}
    assert result["posted"] == [
        {"url": "/api/action", "body": {"target": "PIC-101", "action": "set_setpoint", "value": 0.4}},
    ]


@needs_node
def test_a_loop_in_engineering_units_keeps_its_setpoint_entry_in_that_unit(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        const entry = form(panel, 'sp');
        return { label: entry.querySelector('label').textContent,
                 unit: entry.querySelector('.visually-hidden').textContent };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"label": "Setpoint", "unit": "psia"}


@needs_node
def test_a_set_button_is_named_set_and_its_unit_is_described_outside_it(session, plant):
    result = run_panels(
        """
        const panel = loop();
        return ['sp', 'out'].map(kind => ({
          button: form(panel, kind).querySelector('button').textContent,
          unit: find(panel, n => n.getAttribute('id') === 'fp-PIC-101-' + kind + '-unit').parentNode === form(panel, kind),
        }));
        """,
        plant,
    )

    assert result == [{"button": "Set", "unit": True}] * 2


@needs_node
def test_a_loop_faceplate_has_the_mode_and_two_entries_and_nothing_to_tune(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        return {
          entries: panel.element.all().filter(n => n.getAttribute('data-entry')).map(n => n.getAttribute('data-entry')),
          actions: panel.element.all().filter(n => n.getAttribute('data-action')).map(n => n.getAttribute('data-action')),
          inputs: panel.element.all().filter(n => n.tagName === 'INPUT').length,
          text: panel.element.textContent,
        };
        """,
        plant, snapshot=view(session),
    )

    assert result["entries"] == ["sp", "out"]
    assert result["actions"] == ["manual", "auto"]
    assert result["inputs"] == 2
    assert "uning" not in result["text"]


@needs_node
def test_a_loop_faceplate_names_its_service_and_offers_the_device_it_drives(session, plant):
    result = run_panels(
        """
        const info = Object.assign({}, data.plant.controllers['PIC-101'], { service: 'Gas header pressure' });
        const panel = F.createPanel(doc, 'loop', 'PIC-101', ctx(info));
        panel.update(data.snapshot);
        const drives = find(panel, n => n.getAttribute('data-open') === 'PV-101');
        drives.fire('click');
        return { kind: find(panel, n => n.classes.includes('fp-kind')).textContent, opened,
                 wiring: drives.parentNode.textContent };
        """,
        plant, snapshot=view(session),
    )

    assert result["kind"] == "Controller · Gas header pressure"
    assert result["opened"] == [["device", "PV-101"]]
    assert result["wiring"] == "Measures N-201.pressure · Drives PV-101"


@needs_node
def test_live_values_carry_the_attribute_that_dims_them_when_stale(session, plant):
    result = run_panels(
        """
        const loopPanel = loop();
        const devicePanel = device('K-101');
        return [loopPanel, devicePanel].map(p => p.element.all()
          .filter(n => n.getAttribute('data-live-value') !== null).map(n => n.classes[0]));
        """,
        plant,
    )

    assert result == [["fp-badges", "fp-readings", "fp-bar"], ["fp-badges", "fp-readings", "fp-bar-caption", "fp-bar"]]


@needs_node
def test_a_snapshot_with_no_controllers_section_degrades_and_keeps_the_typing(session, plant):
    result = run_panels(
        """
        const panel = loop();
        panel.update(data.snapshot);
        input(panel, 'out').value = '40';
        panel.update({});
        panel.update({ controllers: undefined });
        return { typed: input(panel, 'out').value,
                 pv: find(panel, n => n.classes.includes('fp-reading')).textContent };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"typed": "40", "pv": "Process value (PV)--"}


@needs_node
def test_the_output_bar_is_a_valid_meter_or_hidden_from_assistive_tech(session, plant):
    snapshot = view(session)
    unknown = json.loads(json.dumps(snapshot))
    del unknown["controllers"]["PIC-101"]["out"]

    result = run_panels(
        """
        const panel = loop();
        const bar = () => find(panel, n => n.getAttribute('role') === 'meter');
        const read = () => ({ now: bar().getAttribute('aria-valuenow'), hidden: bar().getAttribute('aria-hidden') });
        panel.update(data.known);
        const known = read();
        panel.update(data.unknown);
        const missing = read();
        panel.update(data.known);
        return { known, missing, back: read() };
        """,
        plant, known=snapshot, unknown=unknown,
    )

    assert result["known"] == {"now": "50.0", "hidden": None}
    assert result["missing"] == {"now": None, "hidden": "true"}
    assert result["back"] == result["known"]


@needs_node
def test_a_device_driven_by_a_loop_says_so_and_opens_the_loop(session, plant):
    result = run_panels(
        """
        const panel = device('PV-101');
        panel.update(data.snapshot);
        const open = find(panel, n => n.getAttribute('data-open') === 'PIC-101');
        open.fire('click');
        return { note: find(panel, n => n.classes.includes('fp-note')).textContent, label: open.textContent, opened,
                 buttons: panel.element.all().filter(n => n.tagName === 'BUTTON').map(n => n.textContent) };
        """,
        plant, snapshot=view(session),
    )

    assert result["note"].startswith("PV-101 is driven by PIC-101.")
    assert result["label"] == "Open PIC-101"
    assert result["opened"] == [["loop", "PIC-101"]]
    assert result["buttons"] == ["✕", "Open PIC-101"]


@needs_node
def test_a_device_no_loop_drives_offers_the_actions_the_plant_lists(session, plant):
    result = run_panels(
        """
        const panel = device('P-101');
        panel.update(data.snapshot);
        return { buttons: panel.element.all().filter(n => n.tagName === 'BUTTON').map(n => n.textContent),
                 entries: panel.element.all().filter(n => n.tagName === 'FORM').map(n => n.getAttribute('data-entry')),
                 kind: find(panel, n => n.classes.includes('fp-kind')).textContent,
                 badges: find(panel, n => n.classes.includes('fp-badges')).children.map(n => n.textContent) };
        """,
        plant, snapshot=view(session),
    )

    assert plant["plant"]["equipment"]["P-101"]["actions"] == ["start", "stop", "set_speed_target"]
    assert result == {
        "buttons": ["✕", "▶Start P-101", "■Stop P-101", "Set", "Reset LSHH-101"],  # hidden until it trips
        "entries": ["set_speed_target"],
        "kind": "Centrifugal pump",
        "badges": ["▶ RUN"],
    }


@needs_node
def test_a_device_with_no_actions_says_so(session, plant):
    result = run_panels(
        """
        const panel = device('V-101');
        panel.update(data.snapshot);
        return { buttons: panel.element.all().filter(n => n.tagName === 'BUTTON').map(n => n.textContent),
                 notes: panel.element.all().filter(n => n.classes.includes('fp-note')).map(n => n.textContent) };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"buttons": ["✕"], "notes": ["No operator actions on V-101. It follows what flows in and out."]}


@needs_node
def test_a_loop_driven_device_offers_no_action_only_its_loop(session, plant):
    result = run_panels(
        """
        const panel = device('PV-101');
        panel.update(data.snapshot);
        return { actions: panel.element.all().filter(n => n.getAttribute('data-equip-action') !== null).length,
                 forms: panel.element.all().filter(n => n.tagName === 'FORM').length };
        """,
        plant, snapshot=view(session),
    )

    assert plant["plant"]["equipment"]["PV-101"]["actions"] == []
    assert result == {"actions": 0, "forms": 0}


@needs_node
def test_start_is_disabled_while_the_device_runs_and_stop_while_it_is_stopped(session, plant):
    stopped = json.loads(json.dumps(view(session)))
    stopped["equipment"]["P-101"]["running"] = False

    result = run_panels(
        """
        const panel = device('P-101');
        const states = () => ['start', 'stop'].map(a => find(panel, n => n.getAttribute('data-equip-action') === a).disabled);
        panel.update(data.running);
        const running = states();
        panel.update(data.stopped);
        return { running, stopped: states() };
        """,
        plant, running=view(session), stopped=stopped,
    )

    assert result == {"running": [True, False], "stopped": [False, True]}


@needs_node
def test_start_and_stop_post_their_action_and_read_accepted_never_done(session, plant):
    result = run_panels(
        """
        const panel = device('P-101');
        panel.update(data.snapshot);
        find(panel, n => n.getAttribute('data-equip-action') === 'stop').fire('click');
        const pending = message(panel).textContent;
        await settle();
        return { posted, pending, after: message(panel).textContent, kind: message(panel).getAttribute('data-kind') };
        """,
        plant, snapshot=view(session),
    )

    assert result["posted"] == [{"url": "/api/action", "body": {"target": "P-101", "action": "stop", "value": None}}]
    assert result["pending"] == "Sending stop P-101…"
    assert result["after"] == "Stop P-101 accepted. The plant responds over the next steps."
    assert result["kind"] == "accepted"


@needs_node
def test_a_target_is_entered_in_percent_and_posted_as_a_fraction(session, plant):
    result = run_panels(
        """
        const panel = device('P-101');
        panel.update(data.snapshot);
        const field = panel.element.all().find(n => n.tagName === 'INPUT');
        field.value = '80';
        panel.element.all().find(n => n.tagName === 'FORM').fire('submit');
        await settle();
        return { posted, message: message(panel).textContent, left: field.value };
        """,
        plant, snapshot=view(session),
    )

    assert result["posted"] == [{"url": "/api/action", "body": {"target": "P-101", "action": "set_speed_target", "value": 0.8}}]
    assert result["message"] == "Speed target 80.0 % for P-101 accepted. The plant responds over the next steps."
    assert result["left"] == ""


@pytest.mark.parametrize(("text", "expected"), [("150", "Target must be between 0 and 100 %."), ("-1", "Target must be between 0 and 100 %."), ("fast", "Enter a number."), ("", "Enter a number.")])
@needs_node
def test_a_bad_target_is_shown_and_never_posted(session, plant, text, expected):
    result = run_panels(
        """
        const panel = device('P-101');
        panel.update(data.snapshot);
        panel.element.all().find(n => n.tagName === 'INPUT').value = data.text;
        panel.element.all().find(n => n.tagName === 'FORM').fire('submit');
        await settle();
        return { posted, message: message(panel).textContent };
        """,
        plant, snapshot=view(session), text=text,
    )

    assert result == {"posted": [], "message": expected}


@needs_node
def test_a_hundred_percent_target_posts_exactly_one():
    assert run_js("return F.parseTarget('100');") == {"value": 1}
    assert run_js("return F.parseTarget(' 57.5 ');") == {"value": 0.575}


@needs_node
def test_the_bar_shows_the_actual_value_and_the_target_together(session, plant):
    snapshot = json.loads(json.dumps(view(session)))
    snapshot["equipment"]["P-101"]["speed"] = 0.5
    snapshot["equipment"]["P-101"]["speed_target"] = 0.9

    result = run_panels(
        """
        const panel = device('P-101');
        panel.update(data.snapshot);
        const bar = find(panel, n => n.getAttribute('role') === 'meter');
        return { caption: find(panel, n => n.classes.includes('fp-bar-caption')).textContent,
                 fill: find(panel, n => n.classes.includes('fp-bar-fill')).style.width,
                 mark: find(panel, n => n.classes.includes('fp-bar-target')).style.left,
                 now: bar.getAttribute('aria-valuenow'),
                 placeholder: panel.element.all().find(n => n.tagName === 'INPUT').placeholder };
        """,
        plant, snapshot=snapshot,
    )

    assert result == {
        "caption": "Speed 50.0 % · target 90.0 %",
        "fill": "50%",
        "mark": "calc(90% - 1px)",
        "now": "50.0",
        "placeholder": "90.0",
    }


@needs_node
def test_a_refused_device_action_shows_the_servers_words(session, plant):
    result = run_panels(
        """
        reply = { ok: false, status: 400, body: { error: 'P-101 is driven by loop FIC-101; command it through FIC-101' } };
        const panel = device('P-101');
        panel.update(data.snapshot);
        find(panel, n => n.getAttribute('data-equip-action') === 'start').fire('click');
        await settle(); await settle();
        return { message: message(panel).textContent, kind: message(panel).getAttribute('data-kind') };
        """,
        plant, snapshot=view(session),
    )

    assert result == {"message": "P-101 is driven by loop FIC-101; command it through FIC-101", "kind": "rejected"}


def tripped_view(session, name="PSHH-101", reset="manual"):
    tripped = json.loads(json.dumps(view(session)))
    tripped["interlocks"][name]["state"] = "tripped"
    tripped["interlocks"][name]["reset"] = reset

    return tripped


@needs_node
def test_a_device_a_tripped_manual_interlock_holds_offers_its_reset(session, plant):
    result = run_panels(
        """
        const panel = device('K-101');
        const reset = () => panel.element.all().filter(n => n.getAttribute('data-reset') !== null);
        panel.update(data.normal);
        const before = reset().map(n => n.getAttribute('hidden') !== null);
        panel.update(data.tripped);
        const after = reset().map(n => [n.getAttribute('data-reset'), n.getAttribute('hidden') !== null, n.textContent]);
        reset()[0].fire('click');
        await settle();
        return { before, after, posted, message: message(panel).textContent };
        """,
        plant, normal=view(session), tripped=tripped_view(session),
    )

    assert plant["plant"]["interlocks"]["PSHH-101"]["devices"] == ["K-101"]
    assert result["before"] == [True]
    assert result["after"] == [["PSHH-101", False, "Reset PSHH-101"]]
    assert result["posted"] == [{"url": "/api/action", "body": {"target": "PSHH-101", "action": "reset", "value": None}}]
    assert result["message"] == "Reset PSHH-101 accepted. The plant responds over the next steps."


@needs_node
def test_reset_is_not_offered_for_an_automatic_interlock(session, plant):
    result = run_panels(
        """
        const panel = device('K-101');
        panel.update(data.snapshot);
        return panel.element.all().filter(n => n.getAttribute('data-reset') !== null).map(n => n.getAttribute('hidden') !== null);
        """,
        plant, snapshot=tripped_view(session, reset="auto"),
    )

    assert result == [True]


@needs_node
def test_a_reset_the_server_refuses_says_so_on_the_faceplate(session, plant):
    result = run_panels(
        """
        reply = { ok: false, status: 400, body: { error: 'PSHH-101 was not reset: its condition still holds' } };
        const panel = device('K-101');
        panel.update(data.snapshot);
        panel.element.all().find(n => n.getAttribute('data-reset') !== null).fire('click');
        await settle(); await settle();
        return { message: message(panel).textContent, kind: message(panel).getAttribute('data-kind') };
        """,
        plant, snapshot=tripped_view(session),
    )

    assert result == {"message": "PSHH-101 was not reset: its condition still holds", "kind": "rejected"}


@needs_node
def test_a_loop_driven_device_a_tripped_interlock_holds_still_offers_the_reset(session, plant):
    result = run_panels(
        """
        const panel = device('LV-101');
        panel.update(data.snapshot);
        return panel.element.all().filter(n => n.getAttribute('data-reset') !== null).map(n => [n.getAttribute('data-reset'), n.getAttribute('hidden') !== null]);
        """,
        plant, snapshot=tripped_view(session, name="LSLL-101"),
    )

    assert result == [["LSLL-101", False]]


@needs_node
def test_a_device_panel_rewrites_its_rows_in_place_and_keeps_a_row_for_a_missing_value(session, plant):
    snapshot = view(session)
    moved = json.loads(json.dumps(snapshot))
    moved["equipment"]["P-101"]["speed"] = 0.25
    gone = json.loads(json.dumps(moved))
    del gone["equipment"]["P-101"]["speed"]

    result = run_panels(
        """
        const panel = device('P-101');
        const rows = () => panel.element.all().filter(n => n.classes.includes('fp-reading'));
        const speed = () => rows().find(n => n.getAttribute('data-point') === 'P-101.speed').children[1].textContent;
        panel.update(data.snapshot);
        const first = rows()[0];
        const before = { speed: speed(), count: rows().length };
        panel.update(data.moved);
        const after = { speed: speed(), same: rows()[0] === first };
        panel.update(data.gone);
        return { before, after, missing: speed(), same: rows()[0] === first, count: rows().length };
        """,
        plant, snapshot=snapshot, moved=moved, gone=gone,
    )

    assert result["before"]["speed"] == "100.0 %"
    assert result["after"] == {"speed": "25.0 %", "same": True}
    assert (result["missing"], result["same"], result["count"]) == ("--", True, result["before"]["count"])


@needs_node
def test_every_reading_on_either_panel_has_a_trend_button_that_opens_its_point(session, plant):
    result = run_panels(
        """
        const trended = [];
        const withTrend = info => Object.assign(ctx(info), { trend: (point, b) => trended.push([point, b.getAttribute('data-trend')]) });
        const dev = F.createPanel(doc, 'device', 'K-101', withTrend(data.plant.equipment['K-101']));
        dev.update(data.snapshot);
        const lp = F.createPanel(doc, 'loop', 'PIC-101', withTrend(data.plant.controllers['PIC-101']));
        lp.update(data.snapshot);
        const buttons = p => p.element.querySelectorAll('[data-trend]');
        const rows = p => p.element.querySelectorAll('.fp-reading').map(r => r.getAttribute('data-point'));
        buttons(dev)[0].fire('click');
        buttons(lp)[0].fire('click');
        return { device: buttons(dev).map(b => b.getAttribute('data-trend')), deviceRows: rows(dev),
                 loop: buttons(lp).map(b => b.getAttribute('data-trend')),
                 label: buttons(dev)[0].getAttribute('aria-label'), text: buttons(dev)[0].textContent, trended };
        """,
        plant, snapshot=view(session),
    )

    assert result["device"] == result["deviceRows"] and result["device"]
    assert result["loop"] == ["PIC-101.pv", "PIC-101.sp", "PIC-101.out"]
    assert result["label"] == "Show trend of K-101 flow"
    assert result["text"] == "Trend"
    assert result["trended"] == [[result["device"][0]] * 2, ["PIC-101.pv"] * 2]


@needs_node
def test_without_trend_windows_a_panel_has_no_trend_button(session, plant):
    result = run_panels(
        """
        const dev = device('K-101');
        dev.update(data.snapshot);
        const lp = loop();
        lp.update(data.snapshot);
        return dev.element.querySelectorAll('[data-trend]').length + lp.element.querySelectorAll('[data-trend]').length;
        """,
        plant, snapshot=view(session),
    )

    assert result == 0


@needs_node
def test_a_loops_point_reads_from_the_controllers_section():
    snapshot = {"controllers": {"PIC-101": {"pv": 201.5}}, "equipment": {"K-101": {"flow": 3.0}}}

    assert run_js(
        "return [F.pointValue(data.s, 'PIC-101.pv'), F.pointValue(data.s, 'K-101.flow'), F.pointValue(data.s, 'X.y')];",
        s=snapshot,
    ) == [201.5, 3.0, None]


@needs_node
def test_a_point_the_trend_listing_does_not_publish_has_no_trend_button(session, plant):
    result = run_panels(
        """
        const trend = () => {};
        const vocab = { ...data.vocab, points: data.vocab.points.filter(p => p !== 'K-101.flow' && p !== 'PIC-101.sp') };
        const make = (kind, tag, info, v) => F.createPanel(doc, kind, tag, { ...ctx(info), vocab: v, trend });
        const buttons = p => p.element.querySelectorAll('[data-trend]').map(b => b.getAttribute('data-trend'));
        const dev = make('device', 'K-101', data.plant.equipment['K-101'], vocab);
        dev.update(data.snapshot);
        const lp = make('loop', 'PIC-101', data.plant.controllers['PIC-101'], vocab);
        lp.update(data.snapshot);
        const none = make('device', 'K-101', data.plant.equipment['K-101'], {});
        none.update(data.snapshot);
        return { device: buttons(dev), loop: buttons(lp), none: buttons(none), rows: dev.element.querySelectorAll('.fp-reading').length };
        """,
        plant, snapshot=view(session),
    )

    assert "K-101.flow" not in result["device"] and len(result["device"]) == result["rows"] - 1
    assert result["loop"] == ["PIC-101.pv", "PIC-101.out"]
    # No listing (it failed to load), no buttons.
    assert result["none"] == []
