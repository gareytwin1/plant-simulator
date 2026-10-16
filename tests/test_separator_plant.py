"""
The separator plant - T20-17.

`config/plants/separator.yaml` is a two-phase feed into drum V-301 with three
loops in AUTO: FIC-301 on the feed, LIC-301 on the drain and PIC-301 on the
overhead, and a shutdown valve XV-301 on the feed (T20-18). Its header
derives the design point - 50 GPM carrying 10 SCFM into a drum at level 0.5
and 200 psia - and these tests hold the plant to it: it loads there and stays
there, the loops reject a disturbance they have the authority for, a fault
beyond that authority reaches a limit inside the eight minutes the prototype
sized, and every interlock trips and resets.

Faults are set on the device the way a scenario sets them: a restriction is
a malfunction on a writable parameter (C8), a lost signal is the valve's own
T7-3 failure mode.
"""

import json
from pathlib import Path

import pytest

from app.controls.modes import Mode
from app.disturbances.malfunction import Malfunction, MalfunctionRegistry
from app.engine.engine import Engine
from app.engine.persistence import capture_state, restore_state
from app.equipment.registry import EquipmentRegistry
from app.plant.loader import load_plant_file
from app.training.runtime import PlantRuntime

CONFIG = Path(__file__).resolve().parent.parent / "config"
PLANT = CONFIG / "plants" / "separator.yaml"
CONDITION = CONFIG / "initial_conditions" / "separator_normal.json"

DT = 1.0
FAULT_AT = 30
# The prototype sized every fault beyond the loops' authority to cross a
# limit within eight simulated minutes.
AUTHORITY_HORIZON = 480

FEED = 50.0
FEED_GAS = 10.0
LEVEL = 0.5
PRESSURE = 200.0


def runtime():
    return PlantRuntime.from_plant(load_plant_file(PLANT))


def drum(snapshot):
    return snapshot.equipment["V-301"]


def malfunction(rt, tag, parameter, value):
    """A step malfunction on one writable parameter, applied now."""
    equipment = EquipmentRegistry()
    for device in rt.engine.equipment.values():
        equipment.register(device)

    registry = MalfunctionRegistry(equipment)
    registry.add(Malfunction(tag, parameter, value))
    registry.update(rt.snapshot())


def header_pressure(rt, pressure):
    rt.engine.topologies["liquid"].node("N-301").set_boundary_pressure(pressure)


# --------------------------------------------------------------------------
# What the plant is
# --------------------------------------------------------------------------


def test_the_plant_is_one_feed_one_drum_and_three_loop_valves_in_auto_behind_a_shutdown_valve():
    rt = runtime()
    snapshot = rt.snapshot()

    assert set(rt.engine.equipment) == {"XV-301", "FV-301", "LV-301", "PV-301", "V-301"}
    assert {tag: (row["pv_unit"], row["mode"]) for tag, row in snapshot.controllers.items()} == {
        "FIC-301": ("GPM", "AUTO"),
        "LIC-301": ("fraction", "AUTO"),
        "PIC-301": ("psia", "AUTO"),
    }
    assert {tag: binding.out_tag for tag, binding in rt.engine.loops.items()} == {
        "FIC-301": "FV-301",
        "LIC-301": "LV-301",
        "PIC-301": "PV-301",
    }


def test_the_feed_is_one_liquid_valve_carrying_gas_at_the_ratio():
    plant = load_plant_file(PLANT)
    vessel = plant.devices["V-301"]

    assert vessel.feed_gas_ratio == pytest.approx(FEED_GAS / FEED)
    assert {name: (port.node.id, port.phase) for name, port in vessel.ports.items()} == {
        "feed": ("N-302", "liquid"),
        "drain": ("N-302", "liquid"),
        "vapor_out": ("N-312", "vapor"),
    }
    assert plant.devices["FV-301"].fail_action == "open"
    assert plant.devices["LV-301"].fail_action == "open"


def test_the_feed_shutdown_valve_runs_wide_open_upstream_of_the_feed_valve_and_fails_closed():
    plant = load_plant_file(PLANT)
    shutdown, feed = plant.devices["XV-301"], plant.devices["FV-301"]

    assert shutdown.ports["outlet"].node is feed.ports["inlet"].node
    assert shutdown.position == shutdown.position_target == 1.0
    assert shutdown.fail_action == "closed"
    assert shutdown.min_position < feed.min_position


def test_limits_and_interlocks_guard_level_and_pressure():
    rt = runtime()

    assert set(rt.engine.limits) == {("V-301", "level"), ("V-301", "pressure")}
    status = rt.snapshot().interlocks

    assert {tag: (row.state, row.reset, row.devices) for tag, row in status.items()} == {
        "LSHH-301": ("normal", "manual", ("XV-301",)),
        "LSLL-301": ("normal", "manual", ("LV-301",)),
        "PSHH-301": ("normal", "manual", ("XV-301",)),
    }


# --------------------------------------------------------------------------
# The design point
# --------------------------------------------------------------------------


def test_a_freshly_loaded_drum_sits_at_its_design_point_with_nothing_moving():
    rt = runtime()
    first = rt.step(DT)

    assert drum(first)["level"] == LEVEL
    assert drum(first)["pressure"] == pytest.approx(PRESSURE, abs=1e-9)
    assert first.streams["B-XV-301"]["flow"] == pytest.approx(FEED, abs=1e-9)
    assert first.streams["B-FV-301"]["flow"] == pytest.approx(FEED, abs=1e-9)
    assert first.streams["B-LV-301"]["flow"] == pytest.approx(FEED, abs=1e-9)
    assert first.streams["B-PV-301"]["flow"] == pytest.approx(FEED_GAS, abs=1e-9)
    assert drum(first)["feed_gas_flow"] == pytest.approx(FEED_GAS, abs=1e-9)
    for tag, row in first.controllers.items():
        valve = rt.engine.equipment[rt.engine.loops[tag].out_tag]
        assert row["out"] == pytest.approx(valve.position, abs=1e-9), tag
    assert first.envelope == {}


def test_with_no_disturbance_every_loop_holds_its_setpoint_for_10000_steps_and_the_balances_close():
    """The ledger is tests/test_mass_balance.py's: the flow standing before
    each step is what that step integrates."""
    rt = runtime()
    vessel = rt.engine.equipment["V-301"]
    start_volume, start_gas = vessel.volume, vessel.gas_inventory
    liquid = gas = 0.0

    for _ in range(10_000):
        liquid += (vessel.inlet_flow - vessel.outlet_flow) * DT / 60.0
        gas += (vessel.gas_inlet_flow + vessel.feed_gas_flow - vessel.gas_outlet_flow) * DT / 60.0
        snapshot = rt.step(DT)

    assert drum(snapshot)["level"] == pytest.approx(LEVEL, abs=1e-9)
    assert drum(snapshot)["pressure"] == pytest.approx(PRESSURE, abs=1e-6)
    assert snapshot.streams["B-FV-301"]["flow"] == pytest.approx(FEED, abs=1e-6)
    for row in snapshot.controllers.values():
        assert row["pv"] == pytest.approx(row["sp"], rel=1e-6)
    assert vessel.volume - start_volume == pytest.approx(liquid, abs=1e-6)
    assert vessel.gas_inventory - start_gas == pytest.approx(gas, abs=1e-6)
    assert snapshot.envelope == {}
    assert rt.trips.tripped == ()


# --------------------------------------------------------------------------
# Disturbances within the loops' authority (Checkpoint D's remaining gate)
# --------------------------------------------------------------------------

WITHIN_AUTHORITY = {
    "feed header up 15 psi": lambda rt: header_pressure(rt, 101.0),
    "feed header down 10 psi": lambda rt: header_pressure(rt, 76.0),
    "feed valve restricted to 75%": lambda rt: malfunction(rt, "FV-301", "capacity", 15.0),
    "gas-rich feed": lambda rt: malfunction(rt, "V-301", "feed_gas_ratio", 0.25),
}


@pytest.mark.parametrize("disturb", WITHIN_AUTHORITY.values(), ids=WITHIN_AUTHORITY)
def test_a_feed_disturbance_within_the_loops_authority_is_rejected_in_auto(disturb):
    """Checkpoint D: a loop holds its setpoint through an injected upstream
    disturbance. No limit is reached on the way, and the feed, level and
    pressure all come back to their setpoints with every loop still in AUTO."""
    rt = runtime()
    for _ in range(FAULT_AT):
        rt.step(DT)
    disturb(rt)
    excursion = {}

    for _ in range(1200):
        snapshot = rt.step(DT)
        excursion |= snapshot.envelope

    assert excursion == {}
    assert snapshot.streams["B-FV-301"]["flow"] == pytest.approx(FEED, abs=0.01)
    assert drum(snapshot)["level"] == pytest.approx(LEVEL, abs=0.001)
    assert drum(snapshot)["pressure"] == pytest.approx(PRESSURE, abs=0.05)
    assert {row["mode"] for row in snapshot.controllers.values()} == {"AUTO"}


# --------------------------------------------------------------------------
# Faults beyond their authority
# --------------------------------------------------------------------------

BEYOND_AUTHORITY = {
    "drain restricted": (lambda rt: malfunction(rt, "LV-301", "capacity", 7.0), "V-301.level", "hi"),
    "vent restricted": (lambda rt: malfunction(rt, "PV-301", "capacity", 0.6), "V-301.pressure", "hi"),
    "feed valve signal lost": (lambda rt: rt.engine.equipment["FV-301"].lose_signal(), "V-301.level", "hi"),
    "drain valve signal lost": (lambda rt: rt.engine.equipment["LV-301"].lose_signal(), "V-301.level", "lo"),
}


@pytest.mark.parametrize("fault, point, band", BEYOND_AUTHORITY.values(), ids=BEYOND_AUTHORITY)
def test_a_fault_beyond_the_loops_authority_reaches_a_limit_within_eight_minutes(fault, point, band):
    rt = runtime()
    for _ in range(FAULT_AT):
        rt.step(DT)
    fault(rt)

    for _ in range(AUTHORITY_HORIZON):
        snapshot = rt.step(DT)
        if point in snapshot.envelope:
            break

    assert snapshot.envelope.get(point, {}).get("band") == band
    assert {row["mode"] for row in snapshot.controllers.values()} == {"AUTO"}


# --------------------------------------------------------------------------
# Interlocks
# --------------------------------------------------------------------------


def run_until(rt, done, limit):
    for _ in range(limit):
        snapshot = rt.step(DT)
        if done(snapshot):
            return snapshot
    raise AssertionError("never happened")


def drain_wide_open(rt):
    loop = rt.engine.loops["LIC-301"].loop
    loop.mode = Mode.MANUAL
    loop.manual_output = 1.0


INTERLOCKS = {
    # A restricted drain fills the drum; a full drum shuts in the feed.
    "LSHH-301": (lambda rt: malfunction(rt, "LV-301", "capacity", 7.0), "XV-301"),
    # A drain an operator holds wide open empties it; an empty drum shuts in the drain.
    "LSLL-301": (drain_wide_open, "LV-301"),
    # A restricted vent pressurises it; an over-pressured drum shuts in the feed.
    "PSHH-301": (lambda rt: malfunction(rt, "PV-301", "capacity", 0.6), "XV-301"),
}


def test_a_trip_shuts_in_the_feed_through_xv_301_while_fv_301_sits_wide_open_on_a_lost_signal():
    """The fault the shutdown valve exists for. A lost FV-301 signal alone
    settles the drum short of its trip, so a restricted drain is added to
    reach LSHH-301: FV-301 then ignores the trip, and only XV-301 can act."""
    rt = runtime()
    feed, shutdown = rt.engine.equipment["FV-301"], rt.engine.equipment["XV-301"]
    condition = rt.trips.interlocks["LSHH-301"].definition.condition
    for _ in range(FAULT_AT):
        rt.step(DT)
    feed.lose_signal()
    malfunction(rt, "LV-301", "capacity", 7.0)

    run_until(rt, lambda s: s.interlocks["LSHH-301"].state == "tripped", 900)
    run_until(rt, lambda s: shutdown.position == pytest.approx(shutdown.min_position), 60)

    assert feed.signal_ok is False
    assert feed.position == pytest.approx(1.0)

    snapshot = run_until(rt, lambda s: not condition.is_met(drum(s)["level"]), 900)
    assert drum(snapshot)["level"] < 0.9
    # The trip system sees the cleared level one step after the snapshot.
    rt.step(DT)
    rt.act("LSHH-301", "reset", None)

    assert rt.trips.tripped == ()


@pytest.mark.parametrize("tag", INTERLOCKS)
def test_every_interlock_trips_shuts_its_valve_and_resets_once_its_condition_clears(tag):
    fault, valve_tag = INTERLOCKS[tag]
    rt = runtime()
    condition = rt.trips.interlocks[tag].definition.condition
    valve = rt.engine.equipment[valve_tag]

    def met(snapshot):
        return condition.is_met(snapshot.equipment[condition.tag][condition.variable])

    fault(rt)
    run_until(rt, lambda s: s.interlocks[tag].state == "tripped", 900)

    assert rt.trips.tripped == (tag,)
    with pytest.raises(ValueError, match="its condition still holds"):
        rt.act(tag, "reset", None)

    # The trip outranks the loop on the valve and drives it to minimum travel.
    run_until(rt, lambda s: valve.position == valve.min_position, 60)

    # Shut in, the drum comes back inside the trip bound and the trip resets.
    run_until(rt, lambda s: not met(s), 900)
    # The trip system judges a condition on the snapshot before each step, so
    # it sees the cleared value one step after the snapshot that shows it.
    rt.step(DT)
    rt.act(tag, "reset", None)

    assert rt.trips.tripped == ()
    assert rt.step(DT).interlocks[tag].state == "normal"


# --------------------------------------------------------------------------
# Determinism and the saved design point
# --------------------------------------------------------------------------


def test_same_config_and_steps_give_bit_identical_state():
    def trace():
        rt = runtime()
        snapshots = []
        for step in range(900):
            if step == FAULT_AT:
                malfunction(rt, "LV-301", "capacity", 7.0)
            snapshots.append(rt.step(DT))
        return capture_state(rt.engine), [(s.equipment, s.streams, s.controllers, s.envelope) for s in snapshots]

    first, again = trace(), trace()

    assert json.dumps(first[0]) == json.dumps(again[0])
    assert first[1] == again[1]


def restored():
    engine = Engine.from_plant(load_plant_file(PLANT))
    restore_state(engine, json.loads(CONDITION.read_text()))

    return engine


def test_the_saved_design_point_restores_onto_a_fresh_separator_and_captures_back_to_the_file():
    assert capture_state(restored()) == json.loads(CONDITION.read_text())


def test_the_saved_design_point_holds_steady_in_auto_with_no_band():
    engine = restored()

    for _ in range(300):
        snapshot = engine.step(DT)

    assert drum(snapshot)["level"] == pytest.approx(LEVEL, abs=1e-6)
    assert drum(snapshot)["pressure"] == pytest.approx(PRESSURE, abs=1e-6)
    assert snapshot.streams["B-FV-301"]["flow"] == pytest.approx(FEED, abs=1e-6)
    assert {row["mode"] for row in snapshot.controllers.values()} == {"AUTO"}
    assert snapshot.envelope == {}
