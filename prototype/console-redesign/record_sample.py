"""Record the console prototype's sample data from the real engine.

Loads the prototype separator (separator.yaml) into the production engine and
runtime, steps it by hand (no wall clock, no server) and writes what the
console would have read: operator-view snapshots, the alarm history, trend
points with their limits and descriptors, and each point's history. The
prototype replays this file; it never talks to a running app.

Every loop runs in AUTO. PIC-301 is the engine's own; FIC-301 and LIC-301
are proposed loops the engine cannot run yet (controllers.pv takes only a
node), so `ProposedLoop` runs them here as PI controllers, writing their
valve targets through `PlantRuntime.act` each step.

The scenarios here are review samples, not production scenario files: each
is free play plus one fault at 30 s that a loop in AUTO cannot correct on its
own, set on the device as a malfunction or T7-3 failure mode would set it.
No operator action follows.

    python prototype/console-redesign/record_sample.py [seconds]

Historical: it ran against the app at 9b2cf16 and imports app internals, so
it may stop working as the app changes. Its output, sample-data.js, is the
frozen record and is not to be regenerated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.api.alarms import _serialize
from app.api.visibility import operator_view
from app.engine.engine import Engine
from app.main import PHASE_LABELS
from app.plant.loader import load_plant, read_plant_config
from app.training.runtime import PlantRuntime

HERE = Path(__file__).resolve().parent
PLANT = HERE / "separator.yaml"
DT = 1.0
FRAME_EVERY_S = 2.0
FAULT_AT_S = 30.0
FREE_PLAY = "free"

# Each fault is (tag, attribute, value) on the device: a capacity cut is a
# restriction (a malfunction-writable parameter), signal_ok False a lost
# signal that sends the valve to its fail position.
SCENARIOS = [
    {
        "key": "sep-rising-level",
        "title": "Rising drum level",
        "difficulty": "medium",
        "time_limit_s": 900.0,
        "briefing": "Thirty seconds into the run V-301 level starts to climb towards its high alarm. Find out why and bring it back to its setpoint.",
        "fault": [("LV-301", "capacity", 7.0)],
    },
    {
        "key": "sep-gas-rich-feed",
        "title": "Climbing drum pressure",
        "difficulty": "easy",
        "time_limit_s": 600.0,
        "briefing": "Thirty seconds into the run V-301 pressure starts to rise while the level holds. Keep the drum below its high-high pressure trip.",
        "fault": [("PV-301", "capacity", 0.6)],
    },
    {
        "key": "sep-feed-surge",
        "title": "Feed surge",
        "difficulty": "medium",
        "time_limit_s": 900.0,
        "briefing": "Thirty seconds into the run more feed arrives than the drum passes on, and level and pressure both climb. Bring the drum back under control.",
        "fault": [("FV-301", "signal_ok", False), ("FV-302", "signal_ok", False)],
    },
    {
        "key": "sep-feed-loss",
        "title": "Drum emptying",
        "difficulty": "hard",
        "time_limit_s": 600.0,
        "briefing": "Thirty seconds into the run V-301 level starts to drop. Hold the drum above its low-low trip.",
        "fault": [("LV-301", "signal_ok", False)],
        "fails_below_level": 0.1,
    },
]


class ProposedLoop:
    """A PI loop in AUTO on a snapshot reading, driving valve targets. Starts
    bumpless from the valves' design position; the integral holds while the
    output is saturated."""

    def __init__(self, read, sp: float, valves: tuple[str, ...], bias: float, kp: float, ki: float, direct: bool) -> None:
        self.read, self.sp, self.valves = read, sp, valves
        self.bias, self.kp, self.ki, self.sign = bias, kp, ki, 1.0 if direct else -1.0
        self.integral = 0.0

    def step(self, runtime: PlantRuntime, snapshot, dt: float) -> None:
        error = self.sign * (self.read(snapshot) - self.sp)
        raw = self.bias + self.kp * error + self.ki * (self.integral + error * dt)
        out = min(1.0, max(0.0, raw))
        if out == raw:
            self.integral += error * dt
        for valve in self.valves:
            runtime.act(valve, "set_position_target", out)


def proposed_loops() -> list[ProposedLoop]:
    return [
        # FIC-301: feed liquid flow; FV-302 moves with FV-301 (the two-phase feed).
        ProposedLoop(lambda s: s.streams["B-FV-301"]["flow"], 50.0, ("FV-301", "FV-302"),
                     bias=0.5, kp=0.005, ki=0.002, direct=False),
        # LIC-301: drum level on the drain.
        ProposedLoop(lambda s: s.equipment["V-301"]["level"], 0.5, ("LV-301",),
                     bias=5 / 7, kp=1.0, ki=0.02, direct=True),
    ]


def record(scenario: dict[str, object] | None, seconds: float) -> dict[str, object]:
    plant = load_plant(read_plant_config(PLANT))
    engine = Engine.from_plant(plant)
    runtime = PlantRuntime(engine, plant)

    snapshot = runtime.snapshot()
    start = snapshot.sim_time
    frames = [operator_view(snapshot, engine.equipment)]
    steps_per_frame = max(1, round(FRAME_EVERY_S / DT))
    loops = proposed_loops()
    fault_done = scenario is None
    phase = "idle" if scenario is None else "running"

    while snapshot.sim_time - start < seconds:
        for _ in range(steps_per_frame):
            if not fault_done and snapshot.sim_time - start >= FAULT_AT_S:
                for tag, attribute, value in scenario["fault"]:  # type: ignore[index]
                    setattr(engine.equipment[tag], attribute, value)
                fault_done = True
            for loop in loops:
                loop.step(runtime, snapshot, DT)
            snapshot = runtime.step(DT)
        frames.append(operator_view(snapshot, engine.equipment))
        floor = scenario.get("fails_below_level") if scenario else None
        if isinstance(floor, float) and snapshot.equipment["V-301"]["level"] <= floor:
            phase = "complete"  # the sample objective failed: the run ends here
            break

    points = runtime.trend_points()
    history = runtime.trend_history(points)

    return {
        "result": None if scenario is None else {"phase": phase},
        "frames": frames,
        "alarms": [_serialize(entry) for entry in runtime.alarm_entries()],
        "trend_points": {
            "points": list(points),
            "descriptors": runtime.trend_descriptors(),
            "limits": runtime.trend_limits(),
        },
        "trend_history": {
            point: [[s.timestamp, s.value] for s in samples if s.timestamp >= start]
            for point, samples in history.items()
        },
    }


def write(seconds: float) -> None:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    data = {
        "source": {
            "note": "Recorded from the real engine on the prototype separator; sample data and sample scenarios for review only.",
            "commit": commit,
            "seconds": seconds,
            "frame_every_s": FRAME_EVERY_S,
        },
        "catalogue": [
            {k: s[k] for k in ("key", "title", "difficulty", "briefing", "time_limit_s")} for s in SCENARIOS
        ],
        "phase_labels": PHASE_LABELS,
        "recordings": {FREE_PLAY: record(None, seconds)} | {s["key"]: record(s, seconds) for s in SCENARIOS},
    }
    out = HERE / "sample-data.js"
    out.write_text(
        "/* Generated by record_sample.py - sample data, do not edit. */\n"
        "window.PROTOTYPE_SAMPLE = " + json.dumps(data, separators=(",", ":")) + ";\n"
    )
    for key, rec in data["recordings"].items():
        alarms = [a["message"] for a in rec["alarms"] if a.get("type") == "alarm"]
        print(f"{key}: {len(rec['frames'])} frames, alarms {alarms}, result {rec['result']}")


if __name__ == "__main__":
    write(float(sys.argv[1]) if len(sys.argv) > 1 else 480.0)
