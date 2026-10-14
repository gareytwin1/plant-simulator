"""
A two-phase feed through one valve and one line - T20-5 (ADR 0002, Amendment 4).

The feed is solved as liquid in GPM, and the gas it carries enters the
vessel's gas space at a fixed ratio:

    feed_gas_flow = feed_gas_ratio * max(0, inlet_flow)    [SCFM]

`tests/fixtures/two_phase_feed.yaml` is the prototype separator with its
vapour inlet replaced by the ratio, and its header carries the closed-form
design point: 50 GPM of liquid carrying 10 SCFM of gas into a drum at level
0.5 and 200 psia, drained and vented at exactly those rates.
"""

import json
from pathlib import Path

import pytest
import yaml

from app import config
from app.disturbances.malfunction import Malfunction, MalfunctionRegistry
from app.engine.engine import Engine
from app.engine.persistence import capture_state
from app.equipment.registry import EquipmentRegistry
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant
from app.plant.units import unit_lookup
from app.scenarios.runner import ScenarioLibrary, ScenarioRunner, _check_feed_gas_malfunction
from app.training.runtime import PlantRuntime

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two_phase_feed.yaml"

DT = 1.0
RATIO = 0.2
FEED = 50.0
FEED_GAS = 10.0


def separator_config():
    return yaml.safe_load(FIXTURE.read_text())


def running(cfg):
    plant = load_plant(cfg)
    engine = Engine.from_plant(plant)
    engine.start()

    return plant, engine


def run(engine, steps):
    snapshot = engine.snapshot()

    for _ in range(steps):
        snapshot = engine.step(DT)

    return snapshot


def vessel_row(snapshot):
    return snapshot.equipment["V-301"]


# --------------------------------------------------------------------------
# The vessel's own arithmetic
# --------------------------------------------------------------------------


def gas_vessel(ratio=RATIO):
    vessel = Vessel()
    vessel.activate_gas()
    vessel.feed_gas_ratio = ratio

    return vessel


def test_feed_gas_is_the_ratio_times_the_liquid_inflow():
    vessel = gas_vessel()
    vessel.inlet_flow = FEED

    assert vessel.feed_gas_flow == pytest.approx(FEED_GAS)


def test_liquid_running_backwards_carries_no_gas():
    vessel = gas_vessel()
    vessel.inlet_flow = -FEED

    assert vessel.feed_gas_flow == pytest.approx(0.0)


def test_feed_gas_enters_the_gas_balance():
    """10 SCFM into 100 ft^3 for 60 s is 10 scf, which is P_std * 10 / 100
    of pressure: exactly what a gas inlet of 10 SCFM would add."""
    released = gas_vessel()
    released.inlet_flow = FEED
    piped = gas_vessel(ratio=0.0)
    piped.gas_inlet_flow = FEED_GAS

    released.integrate(60.0)
    piped.integrate(60.0)

    assert released.pressure == pytest.approx(piped.pressure, rel=1e-15)


def test_a_zero_ratio_leaves_the_gas_balance_bit_for_bit():
    """Every plant before this amendment has a ratio of zero, so its gas
    balance must be the old expression exactly, not merely close to it."""
    vessel = gas_vessel(ratio=0.0)
    vessel.inlet_flow = FEED
    vessel.gas_inlet_flow = 3.0
    vessel.gas_outlet_flow = 1.0
    start = vessel.pressure

    vessel.integrate(DT)

    assert vessel.pressure == (
        start + config.STANDARD_PRESSURE * (3.0 - 1.0) * DT / 60.0 / vessel.gas_volume
    )


def test_the_ratio_is_never_negative():
    with pytest.raises(ValueError, match="feed_gas_ratio"):
        Vessel().feed_gas_ratio = -0.1


def test_a_vessel_without_a_gas_phase_integrates_no_feed_gas():
    vessel = Vessel()
    vessel.feed_gas_ratio = RATIO
    vessel.inlet_flow = FEED
    start = vessel.pressure

    vessel.integrate(DT)

    assert vessel.pressure == start
    assert "feed_gas_flow" not in vessel.get_state()


def test_the_ratio_and_the_release_are_published_in_their_units():
    _, engine = running(separator_config())
    unit = unit_lookup(engine.equipment, engine.topologies, engine.couplings)
    row = vessel_row(engine.snapshot())

    assert row["feed_gas_ratio"] == pytest.approx(RATIO)
    assert row["feed_gas_flow"] == pytest.approx(FEED_GAS)
    assert unit("equipment", "V-301", "feed_gas_ratio") == "SCFM/GPM"
    assert unit("equipment", "V-301", "feed_gas_flow") == "SCFM"


# --------------------------------------------------------------------------
# The plant
# --------------------------------------------------------------------------


def test_a_liquid_only_vessel_with_a_ratio_is_refused_at_build():
    cfg = separator_config()
    vessel = next(item for item in cfg["equipment"] if item["tag"] == "V-301")
    del vessel["ports"]["vapor_out"]
    cfg["nodes"] = [node for node in cfg["nodes"] if node["id"] not in {"N-312", "N-313"}]
    cfg["equipment"] = [item for item in cfg["equipment"] if item["tag"] != "PV-301"]

    with pytest.raises(ValueError, match="no vapor port for the feed gas"):
        Engine.from_plant(load_plant(cfg))


@pytest.mark.parametrize(
    ("dropped", "declared", "folded"),
    [("drain", "inlet", "LV-301"), ("feed", "outlet", "FV-301")],
)
def test_a_liquid_node_declared_one_way_with_a_branch_the_other_way_is_refused(
    dropped, declared, folded,
):
    """A one-direction liquid node writes its net exchange. With the drain
    undeclared it would net off the feed; with the feed undeclared the feed
    would never reach inlet_flow. Either way the gas would not follow it."""
    cfg = separator_config()
    vessel = next(item for item in cfg["equipment"] if item["tag"] == "V-301")
    del vessel["ports"][dropped]

    with pytest.raises(ValueError, match=f"N-302.*{declared}-only.*{folded}"):
        Engine.from_plant(load_plant(cfg))


def test_a_freshly_loaded_drum_sits_at_its_design_point():
    _, engine = running(separator_config())

    row = vessel_row(run(engine, 600))

    assert row["level"] == pytest.approx(0.5, abs=1e-9)
    assert row["pressure"] == pytest.approx(200.0, abs=1e-6)
    assert row["inlet_flow"] == pytest.approx(FEED, abs=1e-6)
    assert row["feed_gas_flow"] == pytest.approx(FEED_GAS, abs=1e-6)
    assert row["gas_outlet_flow"] == pytest.approx(FEED_GAS, abs=1e-6)
    assert row["gas_inlet_flow"] == pytest.approx(0.0)


def test_a_feed_surge_raises_level_and_pressure_together():
    """The one thing a two-phase feed has to teach: opening the feed valve
    adds liquid and gas at once."""
    plant, engine = running(separator_config())
    before = vessel_row(run(engine, 10))

    plant.devices["FV-301"].set_position_target(0.7)
    after = vessel_row(run(engine, 60))

    assert after["level"] > before["level"] + 0.05
    assert after["pressure"] > before["pressure"] + 1.0
    assert after["feed_gas_flow"] == pytest.approx(RATIO * after["inlet_flow"])


def test_one_flow_loop_on_the_one_feed_valve_moves_both_phases():
    """FIC-301 is one loop on FV-301's solved flow. Raising its setpoint
    raises the liquid it holds, and the gas follows at the ratio. LIC-301
    holds the level, so the liquid node and the feed's differential settle."""
    cfg = separator_config()
    cfg["controllers"] = [
        {
            "tag": "FIC-301", "pv": "FV-301.flow", "sp": FEED, "out": "FV-301",
            "mode": "AUTO", "kp": 0.005, "ki": 0.002, "kd": 0.0,
        },
        {
            "tag": "LIC-301", "pv": "V-301.level", "sp": 0.5, "out": "LV-301",
            "mode": "AUTO", "action": "DIRECT", "kp": 1.0, "ki": 0.02, "kd": 0.0,
        },
    ]
    _, engine = running(cfg)
    engine.loops["FIC-301"].loop.pid.setpoint = 55.0

    snapshot = run(engine, 1200)

    assert snapshot.equipment["FV-301"]["flow"] == pytest.approx(55.0, abs=1e-3)
    assert vessel_row(snapshot)["feed_gas_flow"] == pytest.approx(RATIO * 55.0, abs=1e-3)
    assert snapshot.controllers["FIC-301"]["pv_unit"] == "GPM"


def test_a_gas_breakthrough_raises_pressure_and_leaves_level_alone():
    """A malfunction may move the ratio (C8). More gas per gallon raises the
    drum pressure against a fixed vent, and the liquid side - whose boundary
    is the configured pressure plus the head, never the gas pressure - is
    exactly the unfaulted twin's, so those two compare bit for bit."""
    plant, engine = running(separator_config())
    _, twin = running(separator_config())

    equipment = EquipmentRegistry()
    for device in plant.devices.values():
        equipment.register(device)

    malfunctions = MalfunctionRegistry(equipment)
    malfunctions.add(Malfunction("V-301", "feed_gas_ratio", 2 * RATIO))

    snapshot = engine.snapshot()
    twin_snapshot = twin.snapshot()

    for _ in range(60):
        malfunctions.update(snapshot)
        snapshot = engine.step(DT)
        twin_snapshot = twin.step(DT)

    row = vessel_row(snapshot)

    assert row["feed_gas_ratio"] == pytest.approx(2 * RATIO)
    assert row["pressure"] > vessel_row(twin_snapshot)["pressure"] + 5.0
    assert row["level"] == vessel_row(twin_snapshot)["level"]
    assert row["inlet_flow"] == vessel_row(twin_snapshot)["inlet_flow"]


def without(cfg, *ports):
    vessel = next(item for item in cfg["equipment"] if item["tag"] == "V-301")
    vessel["design"]["feed_gas_ratio"] = 0.0

    for port in ports:
        del vessel["ports"][port]

    return cfg


def liquid_only(cfg):
    cfg = without(cfg, "vapor_out")
    cfg["nodes"] = [node for node in cfg["nodes"] if node["id"] not in {"N-312", "N-313"}]
    cfg["equipment"] = [item for item in cfg["equipment"] if item["tag"] != "PV-301"]

    return cfg


@pytest.mark.parametrize(
    ("build", "problem"),
    [
        (lambda cfg: without(cfg, "drain"), "inlet-only"),
        (lambda cfg: without(cfg, "feed"), "outlet-only"),
        (liquid_only, "no vapor port"),
    ],
    ids=["drain undeclared", "feed undeclared", "liquid only"],
)
def test_a_breakthrough_is_refused_where_the_build_would_refuse_the_ratio(build, problem):
    """A plant built with no feed gas loads whatever its liquid nodes
    declare. A scenario raising the ratio on it is refused at arming, by the
    same check the build applies to a configured ratio, rather than letting
    the gas track something other than the feed."""
    engine = Engine.from_plant(load_plant(build(separator_config())))

    with pytest.raises(ValueError, match=problem):
        _check_feed_gas_malfunction(engine, Malfunction("V-301", "feed_gas_ratio", RATIO))


def test_a_breakthrough_on_a_vessel_that_can_release_gas_is_accepted():
    engine = Engine.from_plant(load_plant(without(separator_config())))

    _check_feed_gas_malfunction(engine, Malfunction("V-301", "feed_gas_ratio", RATIO))
    _check_feed_gas_malfunction(engine, Malfunction("V-301", "feed_gas_ratio", 0.0))


def test_arming_a_scenario_refuses_a_breakthrough_the_plant_cannot_release(tmp_path):
    """The refusal is wired where a run is armed, not only callable."""
    cfg = without(separator_config(), "drain")
    library = ScenarioLibrary(
        scenarios=tmp_path / "scenarios",
        plants=tmp_path / "plants",
        conditions=tmp_path / "conditions",
    )

    for folder in (library.scenarios, library.plants, library.conditions):
        folder.mkdir()

    (library.plants / "feed.yaml").write_text(yaml.safe_dump(cfg))
    plant = load_plant(cfg)
    engine = PlantRuntime(Engine.from_plant(plant), plant).engine
    (library.conditions / "fresh.json").write_text(json.dumps(capture_state(engine)))

    scenario = {
        "id": "breakthrough",
        "plant": "feed",
        "initial_condition": {"condition": "fresh"},
        "malfunctions": [{"target_tag": "V-301", "parameter": "feed_gas_ratio", "value": RATIO}],
        "time_limit_s": 60,
        "difficulty": "easy",
        "seed": 1,
    }

    with pytest.raises(ValueError, match="cannot raise the ratio.*inlet-only"):
        ScenarioRunner(library).load_config(scenario)

    scenario["malfunctions"][0]["value"] = 0.0
    ScenarioRunner(library).load_config(scenario)

