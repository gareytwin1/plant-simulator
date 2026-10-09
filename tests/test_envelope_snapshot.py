"""
Envelope status in the snapshot (T9-4).

A single Vessel with no topology - level is driven directly through
inlet_flow/outlet_flow or by setting `level` outright, the same way
tests/test_vessel.py already does - so these tests exercise envelope
classification through a real Engine.step() without needing a plant that
also converges a network. `V-101.level` is deliberately the same tag and
variable the reference plant config/plants/olefins_lite.yaml configures a
real limit against. The reference plant's other two limits name solved
points the engine composes onto a device's row (T9-5) and are covered at the
end of this file.
"""

from pathlib import Path

import pytest

from app.engine.engine import Engine
from app.engine.instruments import Instrument
from app.envelope.evaluator import Evaluator, Limits, Severity, isa_band
from app.envelope.loader import load_limits
from app.equipment.vessel import Vessel
from app.plant.loader import load_plant_file

PLANTS = Path(__file__).resolve().parent.parent / "config" / "plants"


LIMITS = {
    "limits": [
        {"tag": "V-101", "variable": "level", "lo": 0.2, "hi": 0.8, "hi_hi": 0.95, "trip": True},
    ],
}


def make_engine(level=0.5):
    vessel = Vessel()
    vessel.level = level

    return Engine([vessel], limits=load_limits(LIMITS))


def set_level(engine, level):
    engine.equipment["V-101"].level = level


# --------------------------------------------------------------------------
# The envelope section itself
# --------------------------------------------------------------------------

def test_a_plant_with_no_limits_publishes_an_empty_envelope():
    engine = Engine([Vessel()])

    assert engine.step(1.0).envelope == {}


def test_a_value_inside_its_band_reports_no_envelope_entry():
    engine = make_engine(level=0.5)

    assert engine.step(1.0).envelope == {}


def test_a_value_outside_its_band_reports_the_isa_band_label():
    engine = make_engine(level=0.5)
    set_level(engine, 0.9)

    snapshot = engine.step(1.0)

    assert snapshot.envelope == {
        "V-101.level": {"band": "hi", "since": pytest.approx(1.0)},
    }


def test_a_deeper_excursion_reports_the_worse_isa_band_label():
    engine = make_engine(level=0.5)
    set_level(engine, 0.97)

    snapshot = engine.step(1.0)

    assert snapshot.envelope["V-101.level"]["band"] == "hihihi"


def test_the_lo_side_reports_its_own_label():
    engine = make_engine(level=0.5)
    set_level(engine, 0.1)

    snapshot = engine.step(1.0)

    assert snapshot.envelope["V-101.level"]["band"] == "lo"


def test_clearing_the_band_drops_the_envelope_entry_entirely():
    engine = make_engine(level=0.5)
    set_level(engine, 0.9)
    engine.step(1.0)

    set_level(engine, 0.5)
    snapshot = engine.step(1.0)

    assert snapshot.envelope == {}


# --------------------------------------------------------------------------
# since - the sim_time the current band was entered
# --------------------------------------------------------------------------

def test_since_is_the_sim_time_the_band_was_first_entered():
    engine = make_engine(level=0.5)
    engine.step(1.0)  # still NORMAL

    set_level(engine, 0.9)
    first = engine.step(1.0)

    assert first.envelope["V-101.level"]["since"] == pytest.approx(2.0)


def test_since_holds_steady_while_the_band_does_not_change():
    engine = make_engine(level=0.5)
    set_level(engine, 0.9)
    engine.step(1.0)

    snapshot = engine.step(1.0)
    snapshot = engine.step(1.0)

    assert snapshot.envelope["V-101.level"]["since"] == pytest.approx(1.0)


def test_since_updates_on_a_side_flip_at_the_same_severity():
    engine = make_engine(level=0.5)
    set_level(engine, 0.9)  # WARNING/hi
    engine.step(1.0)

    set_level(engine, 0.1)  # WARNING/lo - a side flip, not an escalation
    snapshot = engine.step(1.0)

    assert snapshot.envelope["V-101.level"]["band"] == "lo"
    assert snapshot.envelope["V-101.level"]["since"] == pytest.approx(2.0)


def test_since_is_seeded_at_construction_for_a_design_point_already_out_of_band():
    engine = make_engine(level=0.9)

    assert engine.snapshot().envelope["V-101.level"] == {
        "band": "hi",
        "since": pytest.approx(0.0),
    }


def test_a_pre_biased_instrument_seeds_the_envelope_from_the_indicated_value():
    """Regression: instruments must be wired before limits are resolved and
    seeded at construction, or the design-point band would be classified
    from the true value instead of the indicated one - then jump on the
    very first step once the already-registered bias started being read,
    even though nothing in the plant actually changed."""
    vessel = Vessel()
    vessel.level = 0.3  # true 0.3; a +0.5 bias indicates 0.8, at the hi limit
    transmitter = Instrument("LT-101", "equipment", "V-101", "level", bias=0.5)

    engine = Engine([vessel], limits=load_limits(LIMITS), instruments=[transmitter])

    assert engine.snapshot().envelope["V-101.level"] == {
        "band": "hi",
        "since": pytest.approx(0.0),
    }

    # Nothing physically changes on the next step - the band must not move.
    snapshot = engine.step(1.0)

    assert snapshot.envelope["V-101.level"]["since"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Status matches what an independent Evaluator would say
# --------------------------------------------------------------------------

def test_the_envelope_matches_an_independent_evaluator_fed_the_same_values():
    """The same Limits (T9-1), driven by the same value/dt sequence outside
    the engine entirely, must classify identically to what the engine's own
    Evaluator - advanced once per step inside _update_envelope - reports in
    the snapshot. This is the acceptance criterion in the build plan:
    "status matches the evaluator for every tag"."""
    reference = Evaluator(Limits(warning_lo=0.2, warning_hi=0.8, trip_hi=0.95))
    engine = make_engine(level=0.5)
    reference.evaluate(0.5, dt=0.0)

    for level, dt in [(0.5, 1.0), (0.9, 1.0), (0.9, 1.0), (0.97, 1.0), (0.5, 1.0)]:
        set_level(engine, level)
        snapshot = engine.step(dt)
        severity = reference.evaluate(level, dt)

        if severity is Severity.NORMAL:
            assert "V-101.level" not in snapshot.envelope
        else:
            assert snapshot.envelope["V-101.level"]["band"] == isa_band(
                severity, reference.side,
            )


# --------------------------------------------------------------------------
# Trackers - reachable directly, one per resolved limit (T9-3)
# --------------------------------------------------------------------------

def test_a_tracker_is_built_for_every_resolved_limit():
    engine = make_engine(level=0.5)

    assert set(engine.trackers) == {("V-101", "level")}


def test_the_tracker_accumulates_time_in_band_across_steps():
    engine = make_engine(level=0.5)
    set_level(engine, 0.9)

    engine.step(1.0)
    engine.step(2.0)

    tracker = engine.trackers[("V-101", "level")]

    assert tracker.time_in(Severity.WARNING) == pytest.approx(3.0)


def test_the_tracker_captures_the_peak_excursion():
    engine = make_engine(level=0.5)
    set_level(engine, 0.85)
    engine.step(1.0)
    set_level(engine, 0.9)
    engine.step(1.0)
    set_level(engine, 0.82)
    engine.step(1.0)

    peak = engine.trackers[("V-101", "level")].peak

    assert peak is not None
    assert peak.magnitude == pytest.approx(0.1)


# --------------------------------------------------------------------------
# A limit that does not resolve against the equipment section
# --------------------------------------------------------------------------

def test_an_unresolvable_limit_is_refused_at_construction():
    config = {
        "limits": [
            {"tag": "V-101", "variable": "not_a_real_field", "hi": 1.0},
            {"tag": "V-101", "variable": "level", "hi": 0.9},
            {"tag": "X-999", "variable": "level", "hi": 1.0},
        ],
    }

    with pytest.raises(ValueError, match=r"V-101\.not_a_real_field.*X-999\.level"):
        Engine([Vessel()], limits=load_limits(config))


def test_the_reference_plant_evaluates_every_configured_limit():
    """K-101.outlet_pressure and P-101.flow are solved values: the engine
    publishes them on K-101's and P-101's rows (T9-5), so all three limits
    classify live. The plant file builds P-101 stopped, and a stopped P-101
    backflows at -100 GPM (olefins_lite.yaml), past its -20 GPM trip bound;
    started, it runs forward and the band clears."""
    engine = Engine.from_plant(load_plant_file(PLANTS / "olefins_lite.yaml"))

    assert set(engine.limits) == {
        ("V-101", "level"),
        ("K-101", "outlet_pressure"),
        ("P-101", "flow"),
    }
    assert dict(engine.snapshot().envelope["P-101.flow"]) == {"band": "lololo", "since": 0.0}

    engine.equipment["P-101"].start()

    for _ in range(120):
        snapshot = engine.step(1.0)

    assert snapshot.equipment["P-101"]["flow"] > 0.0
    assert "P-101.flow" not in snapshot.envelope
