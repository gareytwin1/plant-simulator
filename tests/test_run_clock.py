"""How a simulated time reads on a page (T20-23): static/js/clock.js.

Free play shows the plant clock; a scenario shows run time from the
`started_at` its result carries. clock.js is pure, so it runs under Node
(a subprocess, JSON in and out), fed bodies from the real scenario API.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_scenario_api import KEY, api  # noqa: F401  (api is a fixture)

ROOT = Path(__file__).resolve().parent.parent
CLOCK_JS = ROOT / "static" / "js" / "clock.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(body, **data):
    script = (
        f"const C = require({json.dumps(str(CLOCK_JS))});"
        "const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"(async () => {{ {body} }})()"
        ".then(r => process.stdout.write(JSON.stringify(r === undefined ? null : r)))"
        ".catch(e => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@needs_node
@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "00:00:00"), (59.9, "00:00:59"), (14501.7, "04:01:41"), (90061, "25:01:01"), (-3, "00:00:00")],
)
def test_with_no_origin_a_time_reads_on_the_plant_clock_as_before(seconds, text):
    assert run_js("return [C.plant(data.s), C.format(data.s, null), C.format(data.s)];", s=seconds) == [text] * 3


@needs_node
@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "00:00"), (12, "00:12"), (11.9999999999, "00:12"), (12.7, "00:12"), (599, "09:59"), (6000, "100:00")],
)
def test_run_time_is_the_ribbons_mm_ss(seconds, text):
    assert run_js("return C.run(data.s);", s=seconds) == text


@needs_node
@pytest.mark.parametrize(("seconds", "text"), [(-0.5, "-00:01"), (-5, "-00:05"), (-65, "-01:05")])
def test_a_time_before_the_run_began_reads_as_negative_run_time_rather_than_wrapping(seconds, text):
    assert run_js("return C.run(data.s);", s=seconds) == text


@needs_node
def test_with_an_origin_a_plant_clock_time_reads_as_run_time():
    # A plant clock of 14770 s is 04:06:10; twelve seconds into a run that began there.
    assert run_js("return [C.format(14782, 14770), C.format(14765, 14770)];") == ["00:12", "-00:05"]


@needs_node
def test_the_origin_follows_the_real_scenario_api_and_clears_in_free_play(api):  # noqa: F811
    client, runner = api
    loaded = client.post("/api/scenario/load", json={"scenario": KEY})
    started = client.post("/api/scenario/start")
    runner.step(1.0)
    polled = client.get("/api/scenario/result")
    client.post("/api/scenario/abort")
    assert client.post("/api/scenario/unload").status_code == 200
    idle = client.get("/api/scenario/result")
    responses = [[r.status_code, r.get_json()] for r in (loaded, started, polled, idle)]

    got = run_js(
        """
        const origin = C.follow("");
        const seen = [origin.get()];
        for (const [status, body] of data.responses) {
          const changed = origin.result(status, body);
          seen.push([changed, origin.get()]);
        }
        // A failure says nothing either way, so the origin stays.
        const kept = C.follow("14770.5");
        return { seen, failed: [kept.result(500, null), kept.result(0, null), kept.get()],
                 blank: C.follow("x").get() };
        """,
        responses=responses,
    )

    zero = loaded.get_json()["started_at"]
    assert got["seen"] == [None, [True, zero], [False, zero], [False, zero], [True, None]]
    assert got["failed"] == [False, False, 14770.5]
    assert got["blank"] is None
