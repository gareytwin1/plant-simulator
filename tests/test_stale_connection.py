"""Stale connection handling (T16-5): static/js/connection.js and connection.css.

Like alarms.js, the pure half runs under Node (a subprocess, JSON in and out).
`mount` is driven with a fake EventSource, fake elements and a fake clock and
timer, so "within the bound" is exact rather than a sleep. The stale bound
itself is checked against the server's own push cadence: a stream that pushes
every interval must never read as stale.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.api.stream import _dropout_seconds

ROOT = Path(__file__).resolve().parent.parent
CONNECTION_JS = ROOT / "static" / "js" / "connection.js"
CONNECTION_CSS = ROOT / "static" / "css" / "connection.css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

HARNESS = """
class FakeSource {
  constructor(url) { this.url = url; this.readyState = 1; this.closed = false; FakeSource.last = this; }
  close() { this.closed = true; this.readyState = 2; }
}
const clock = { t: 0 };
const timers = [];
const el = () => ({
  attrs: {}, textContent: "",
  setAttribute(k, v) { this.attrs[k] = v; },
});
const consoleEl = el(), indicatorEl = el();
const seen = [];
const snapshots = [];
function mount(options) {
  return C.mount(consoleEl, indicatorEl, Object.assign({
    EventSource: FakeSource,
    intervalSeconds: 1,
    now: () => clock.t,
    setInterval: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearInterval: () => {},
    onStatus: s => seen.push(s),
    onSnapshot: s => snapshots.push(s),
  }, options || {}));
}
const push = (data) => FakeSource.last.onmessage({ data: JSON.stringify(data) });
const tick = (ms) => { clock.t += ms; timers.forEach(t => t.fn()); };
"""


def run_js(body, **data):
    """Evaluate `body` (a function body with `C`, the connection.js exports,
    the harness above and `data` in scope) under Node and return its JSON result."""
    script = (
        f"const C = require({json.dumps(str(CONNECTION_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"{HARNESS}"
        f"const result = (() => {{ {body} }})();"
        "process.stdout.write(JSON.stringify(result === undefined ? null : result));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@needs_node
def test_nothing_received_yet_is_connecting_not_live():
    result = run_js("""
        mount();
        return { attr: consoleEl.attrs["data-connection"], text: indicatorEl.textContent };
    """)

    assert result == {"attr": "connecting", "text": "CONNECTING"}


@needs_node
def test_a_snapshot_makes_the_console_live_and_is_handed_on():
    result = run_js("""
        mount();
        push({ sim_time: 5 });
        return { attr: consoleEl.attrs["data-connection"], snapshots };
    """)

    assert result == {"attr": "live", "snapshots": [{"sim_time": 5}]}


@needs_node
@pytest.mark.parametrize("interval", [0.1, 0.5, 1.0, 2.0])
def test_a_stream_pushing_on_cadence_never_reads_stale(interval):
    result = run_js(
        """
        const m = mount({ intervalSeconds: data.interval });
        push({});
        for (let i = 0; i < 50; i++) {
          tick(data.interval * 1000);
          push({});
          if (m.status() !== "live") return false;
        }
        return true;
        """,
        interval=interval,
    )

    assert result is True


@needs_node
@pytest.mark.parametrize("interval", [0.1, 1.0, 2.0])
def test_silence_reads_stale_within_the_bound(interval):
    bound = run_js("return C.staleAfterMs(data.interval)", interval=interval)
    result = run_js(
        """
        const m = mount({ intervalSeconds: data.interval });
        push({});
        const before = [];
        clock.t += data.bound;
        before.push(m.status());
        clock.t += 1;
        before.push(m.status());
        return before;
        """,
        interval=interval,
        bound=bound,
    )

    assert result == ["live", "stale"]
    # The operator sees a freeze no later than the server gives up on a wedged
    # client, and never before one late push could plausibly arrive.
    assert bound <= _dropout_seconds(interval) * 1000
    assert bound > interval * 1000


@needs_node
def test_the_page_shows_stale_without_any_further_event():
    # A dead stream raises nothing; only the timer can notice.
    result = run_js("""
        mount({ intervalSeconds: 1 });
        push({});
        tick(10000);
        return { attr: consoleEl.attrs["data-connection"], text: indicatorEl.textContent, seen };
    """)

    assert result["attr"] == "stale"
    assert result["text"] == "STALE - DATA NOT UPDATING"
    assert result["seen"] == ["connecting", "live", "stale"]


@needs_node
def test_a_stream_error_is_stale_at_once_and_recovery_clears_it():
    result = run_js("""
        mount();
        push({});
        FakeSource.last.onerror();
        const afterError = consoleEl.attrs["data-connection"];
        push({ sim_time: 9 });
        return { afterError, afterRecovery: consoleEl.attrs["data-connection"] };
    """)

    assert result == {"afterError": "stale", "afterRecovery": "live"}


@needs_node
def test_an_error_before_any_snapshot_is_stale_not_connecting():
    result = run_js("""
        mount();
        FakeSource.last.onerror();
        return consoleEl.attrs["data-connection"];
    """)

    assert result == "stale"


@needs_node
def test_a_browser_that_gives_up_is_closed_and_a_late_message_does_not_clear_it():
    result = run_js("""
        mount();
        push({});
        FakeSource.last.readyState = 2;
        FakeSource.last.onerror();
        const afterClose = consoleEl.attrs["data-connection"];
        push({});
        return { afterClose, afterMessage: consoleEl.attrs["data-connection"], text: indicatorEl.textContent };
    """)

    assert result == {
        "afterClose": "closed",
        "afterMessage": "closed",
        "text": "DISCONNECTED - RELOAD TO RECONNECT",
    }


@needs_node
def test_an_unparseable_event_does_not_count_as_a_live_reading():
    result = run_js("""
        mount({ intervalSeconds: 1 });
        push({});
        FakeSource.last.onmessage({ data: "{not json" });
        tick(10000);
        return { attr: consoleEl.attrs["data-connection"], snapshots: snapshots.length };
    """)

    assert result == {"attr": "stale", "snapshots": 1}


@needs_node
def test_mount_refuses_to_guess_the_push_interval():
    result = run_js("""
        try { C.mount(consoleEl, indicatorEl, { EventSource: FakeSource }); return "mounted"; }
        catch (e) { return e.message; }
    """)

    assert "intervalSeconds" in result


@needs_node
def test_stop_closes_the_source():
    assert run_js("mount().stop(); return FakeSource.last.closed") is True


@needs_node
def test_default_url_is_the_stream_endpoint():
    assert run_js("mount(); return FakeSource.last.url") == "/api/stream"


def test_css_dims_live_values_and_keeps_a_glyph_per_state():
    css = CONNECTION_CSS.read_text()

    for state in ("stale", "closed"):
        assert f'[data-connection="{state}"] [data-live-value]' in css
    for state in ("live", "connecting", "stale", "closed"):
        assert re.search(rf'\[data-connection="{state}"\] \.connection-indicator::before', css)
