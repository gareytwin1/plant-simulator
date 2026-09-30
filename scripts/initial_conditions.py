#!/usr/bin/env python3
"""Regenerate config/initial_conditions/*.json (T12-2).

Each file is `capture_state` of the reference plant
(config/plants/olefins_lite.yaml, built the way a session builds it, with no
instruments) after a recipe below has driven it somewhere worth starting from.
`restore_state` puts one back onto a freshly built engine of that plant. A file
fits that plant only: `restore_state` refuses any other by its tags, nodes,
branches and loops.

Run from the repository root after a change to the plant or to a device that
moves what a recipe settles to:

    python -m scripts.initial_conditions

A recipe uses only devices' public setters and `step`, so a regenerated file
differs from the committed one only when the physics did. Settling takes about
30 s in all, which is why no test regenerates: tests/test_initial_conditions.py
pins what each condition does instead, and fails when a change to the plant
moves one.

The plant has no check valve, and a control valve never seals (its travel
floors at `min_position`). Both shape the conditions below:

  - A stopped P-101 backflows out of V-101 into N-101 at any liquid level, so
    liquid inventory has no steady state with the pump stopped short of an
    empty vessel. `cold_shutdown` is drained, and V-101.level reads lo_lo.
  - A stopped K-101 is a pure resistance between the discharge header (305
    psia) and the vessel, so headspace pressure settles where that flow meets
    PV-101's vent, not near atmospheric. `cold_shutdown` starts on that
    balance (a closed-form value, not one the vessel would reach in an hour).
"""

import json
from collections.abc import Callable
from pathlib import Path

from app.engine.engine import Engine
from app.engine.persistence import capture_state
from app.plant.loader import load_plant_file

ROOT = Path(__file__).resolve().parent.parent
PLANT = ROOT / "config" / "plants" / "olefins_lite.yaml"
OUTPUT = ROOT / "config" / "initial_conditions"

DT = 1.0
SETTLE_WINDOW = 500
SETTLE_TOLERANCE = 1e-6
SETTLE_LIMIT = 60_000

# The lo band on V-101.level opens at 0.2 (the plant's `limits`), reached about
# 260 s after the trip, and lo_lo at 0.1 follows within about two minutes.
TRIP_HORIZON = 270


def build() -> Engine:
    return Engine.from_plant(load_plant_file(PLANT))


def _design_point(engine: Engine) -> None:
    """Both machines running at their own design targets, with V-101 and every
    valve at the plant's design values. `olefins_lite.yaml` derives that this
    is the plant's own steady state."""
    pump = engine.equipment["P-101"]
    compressor = engine.equipment["K-101"]
    vessel = engine.equipment["V-101"]

    pump.start()
    pump.speed = pump.speed_target
    compressor.start()
    compressor.load = compressor.load_target
    vessel.level = 0.5
    vessel.pressure = 200.0


def _settle(engine: Engine) -> None:
    """Step until level and headspace pressure both move less than the
    tolerance over a window. Every recipe that must hold steady ends here."""
    vessel = engine.equipment["V-101"]

    for _ in range(SETTLE_LIMIT // SETTLE_WINDOW):
        level, pressure = vessel.level, vessel.pressure

        for _ in range(SETTLE_WINDOW):
            engine.step(DT)

        if (
            abs(vessel.level - level) < SETTLE_TOLERANCE
            and abs(vessel.pressure - pressure) < SETTLE_TOLERANCE
        ):
            return

    raise RuntimeError(f"plant did not settle within {SETTLE_LIMIT} s")


def cold_shutdown() -> Engine:
    """Both machines stopped, V-101 drained, the plant at rest.

    Headspace pressure starts on the balance between K-101's backflow from the
    header and PV-101's vent, both at the design valve position: with
    K_k = 0.04 + 0.01 (machine plus FV-201) and K_v = 1.0 (PV-101),
    (305 - P) / K_k = (P - 100) / K_v."""
    engine = build()
    engine.equipment["V-101"].level = 0.0
    engine.equipment["V-101"].pressure = (305.0 + 0.05 * 100.0) / 1.05

    _settle(engine)

    return engine


def hot_standby() -> Engine:
    """Running and circulating with the plant throttled back: P-101 at 0.9
    speed (just enough head to clear the 20 psi liquid gap), K-101 loaded, and
    LV-101 and PV-101 at their minimum travel. V-101 holds a little over half
    full and the feed and drain both pass the same small flow."""
    engine = build()
    _design_point(engine)
    engine.equipment["P-101"].speed_target = 0.9

    lv, pv = engine.equipment["LV-101"], engine.equipment["PV-101"]
    lv.set_position_target(lv.min_position)
    # PV-101 is PIC-101's output, and the engine re-posts a MANUAL loop's
    # output every step, so the valve only moves if the loop is told to.
    engine.loops["PIC-101"].loop.manual_output = pv.min_position

    _settle(engine)

    return engine


def normal_operation() -> Engine:
    """The design point, running: 50 GPM through P-101 and LV-101, V-101 at
    level 0.5 and 200 psia, PIC-101 as configured (MANUAL)."""
    engine = build()
    _design_point(engine)

    _settle(engine)

    return engine


def feed_pump_trip() -> Engine:
    """An upset in progress: P-101 tripped from normal operation and the
    separator draining backwards through it, with the low level alarm already
    active and the level still falling. Not steady, by intent."""
    engine = build()
    _design_point(engine)
    _settle(engine)

    engine.equipment["P-101"].stop()

    for _ in range(TRIP_HORIZON):
        engine.step(DT)

    return engine


CONDITIONS: dict[str, Callable[[], Engine]] = {
    "cold_shutdown": cold_shutdown,
    "hot_standby": hot_standby,
    "normal_operation": normal_operation,
    "feed_pump_trip": feed_pump_trip,
}


def render(name: str) -> str:
    return json.dumps(capture_state(CONDITIONS[name]()), indent=1) + "\n"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    for name in CONDITIONS:
        (OUTPUT / f"{name}.json").write_text(render(name))
        print(f"wrote {name}.json")


if __name__ == "__main__":
    main()
