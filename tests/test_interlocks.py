from pathlib import Path

import pytest

from app.plant.loader import load_plant_file
from app.plant.validate import validate
from app.safety.interlocks import (
    Condition,
    Interlock,
    InterlockDefinition,
    InterlockState,
    get_interlock,
    load_interlocks,
)

PLANTS = Path(__file__).resolve().parent.parent / "config" / "plants"


def _definition(delay_s: float = 0.0, reset: str = "manual") -> InterlockDefinition:
    return InterlockDefinition(
        tag="PSHH-101",
        condition=Condition.parse("K-101.discharge_pressure >= 350.0"),
        delay_s=delay_s,
        actions=("K-101.stop",),
        reset=reset,  # type: ignore[arg-type]
    )


# --- Condition parsing --------------------------------------------------


def test_condition_parses_tag_variable_operator_and_threshold():
    condition = Condition.parse("P-101.suction_pressure < 20.0")

    assert condition.tag == "P-101"
    assert condition.variable == "suction_pressure"
    assert condition.operator == "<"
    assert condition.threshold == 20.0


@pytest.mark.parametrize(
    ("text", "value", "expected"),
    [
        ("V-101.level >= 0.9", 0.9, True),
        ("V-101.level >= 0.9", 0.899, False),
        ("V-101.level <= 0.1", 0.1, True),
        ("K-101.discharge_pressure == 350.0", 350.0, True),
        ("K-101.discharge_pressure != 350.0", 350.0, False),
        ("P-101.flow < 20.0", 19.9, True),
        ("P-101.flow > 20.0", 20.1, True),
    ],
)
def test_condition_is_met_for_every_operator(text: str, value: float, expected: bool):
    assert Condition.parse(text).is_met(value) is expected


def test_condition_handles_a_negative_threshold_and_no_surrounding_spaces():
    condition = Condition.parse("P-101.flow<=-20.0")

    assert condition.threshold == -20.0
    assert condition.is_met(-20.0) is True
    assert condition.is_met(-19.9) is False


def test_malformed_condition_is_rejected():
    with pytest.raises(ValueError, match="malformed interlock condition"):
        Condition.parse("not a condition")


def test_unknown_operator_is_rejected():
    with pytest.raises(ValueError, match="unknown operator"):
        Condition(tag="P-101", variable="flow", operator="<>", threshold=1.0)


# --- InterlockDefinition validation --------------------------------------


def test_negative_delay_is_rejected():
    with pytest.raises(ValueError, match="delay_s"):
        _definition(delay_s=-1.0)


def test_empty_actions_is_rejected():
    with pytest.raises(ValueError, match="actions"):
        InterlockDefinition(
            tag="PSHH-101",
            condition=Condition.parse("K-101.discharge_pressure >= 350.0"),
            delay_s=1.0,
            actions=(),
            reset="manual",
        )


def test_invalid_reset_mode_is_rejected():
    with pytest.raises(ValueError, match="reset"):
        _definition(reset="sometimes")


# --- Delay timer -----------------------------------------------------------


def test_condition_not_met_stays_normal():
    interlock = Interlock(_definition(delay_s=5.0))

    assert interlock.evaluate(300.0, dt=1.0) is InterlockState.NORMAL
    assert not interlock.tripped


def test_delay_timer_accrues_exactly_by_dt_each_step():
    interlock = Interlock(_definition(delay_s=5.0))

    interlock.evaluate(360.0, dt=1.0)
    assert interlock.pending_elapsed == pytest.approx(1.0)
    interlock.evaluate(360.0, dt=2.0)
    assert interlock.pending_elapsed == pytest.approx(3.0)


def test_trip_does_not_fire_before_the_delay_elapses():
    interlock = Interlock(_definition(delay_s=5.0))

    interlock.evaluate(360.0, dt=1.0)
    interlock.evaluate(360.0, dt=1.0)
    interlock.evaluate(360.0, dt=2.9)

    assert interlock.state is InterlockState.PENDING
    assert not interlock.tripped


def test_trip_fires_exactly_at_the_configured_delay():
    interlock = Interlock(_definition(delay_s=5.0))

    interlock.evaluate(360.0, dt=1.0)
    interlock.evaluate(360.0, dt=1.0)
    interlock.evaluate(360.0, dt=1.0)
    interlock.evaluate(360.0, dt=1.0)
    state = interlock.evaluate(360.0, dt=1.0)  # total 5.0 == delay_s

    assert state is InterlockState.TRIPPED
    assert interlock.tripped


def test_trip_fires_immediately_when_a_single_step_reaches_the_delay():
    interlock = Interlock(_definition(delay_s=5.0))

    state = interlock.evaluate(360.0, dt=5.0)

    assert state is InterlockState.TRIPPED


def test_zero_delay_trips_on_the_very_first_evaluation():
    interlock = Interlock(_definition(delay_s=0.0))

    state = interlock.evaluate(360.0, dt=0.0)

    assert state is InterlockState.TRIPPED


def test_recovery_inside_the_window_prevents_the_trip():
    interlock = Interlock(_definition(delay_s=5.0))

    interlock.evaluate(360.0, dt=4.0)
    state = interlock.evaluate(340.0, dt=0.0)  # condition clears before 5.0 elapses

    assert state is InterlockState.NORMAL
    assert not interlock.tripped


def test_timer_resets_when_the_condition_clears_then_restarts_from_zero():
    interlock = Interlock(_definition(delay_s=5.0))

    interlock.evaluate(360.0, dt=4.0)
    interlock.evaluate(340.0, dt=1.0)  # clears - timer resets
    assert interlock.pending_elapsed == pytest.approx(0.0)

    # The same near-miss again should take the full delay, not just the 1.0 left over.
    interlock.evaluate(360.0, dt=4.0)
    assert interlock.state is InterlockState.PENDING
    state = interlock.evaluate(360.0, dt=1.0)

    assert state is InterlockState.TRIPPED


def test_negative_dt_is_rejected():
    interlock = Interlock(_definition(delay_s=5.0))

    with pytest.raises(ValueError, match="dt"):
        interlock.evaluate(360.0, dt=-1.0)


# --- Reset semantics ---------------------------------------------------


def test_auto_reset_clears_itself_once_the_condition_is_no_longer_met():
    interlock = Interlock(_definition(delay_s=0.0, reset="auto"))
    interlock.evaluate(360.0, dt=0.0)
    assert interlock.tripped

    state = interlock.evaluate(300.0, dt=1.0)

    assert state is InterlockState.NORMAL
    assert not interlock.tripped


def test_auto_reset_stays_tripped_while_the_condition_is_still_met():
    interlock = Interlock(_definition(delay_s=0.0, reset="auto"))
    interlock.evaluate(360.0, dt=0.0)

    state = interlock.evaluate(360.0, dt=1.0)

    assert state is InterlockState.TRIPPED


def test_manual_reset_does_not_clear_on_its_own():
    interlock = Interlock(_definition(delay_s=0.0, reset="manual"))
    interlock.evaluate(360.0, dt=0.0)

    interlock.evaluate(300.0, dt=1.0)  # condition long gone

    assert interlock.tripped


def test_manual_reset_succeeds_once_the_condition_has_cleared():
    interlock = Interlock(_definition(delay_s=0.0, reset="manual"))
    interlock.evaluate(360.0, dt=0.0)
    interlock.evaluate(300.0, dt=1.0)  # condition clears, still tripped

    took_effect = interlock.reset()

    assert took_effect is True
    assert interlock.state is InterlockState.NORMAL
    assert interlock.pending_elapsed == pytest.approx(0.0)


def test_manual_reset_refuses_while_the_condition_is_still_met():
    interlock = Interlock(_definition(delay_s=0.0, reset="manual"))
    interlock.evaluate(360.0, dt=0.0)

    took_effect = interlock.reset()

    assert took_effect is False
    assert interlock.tripped


def test_reset_is_a_no_op_when_not_tripped():
    interlock = Interlock(_definition(delay_s=5.0))

    assert interlock.reset() is False


def test_a_reset_interlock_can_trip_again_if_the_condition_returns():
    interlock = Interlock(_definition(delay_s=0.0, reset="manual"))
    interlock.evaluate(360.0, dt=0.0)
    interlock.evaluate(300.0, dt=1.0)
    interlock.reset()

    state = interlock.evaluate(360.0, dt=0.0)

    assert state is InterlockState.TRIPPED


# --- Loader --------------------------------------------------------------


def test_interlocks_load_from_the_interlocks_key():
    config = {
        "interlocks": [
            {
                "tag": "PSLL-101",
                "condition": "P-101.suction_pressure < 20.0",
                "delay_s": 2.0,
                "actions": ["P-101.stop"],
                "reset": "manual",
            },
        ],
    }

    interlocks = load_interlocks(config)

    assert set(interlocks) == {"PSLL-101"}
    assert interlocks["PSLL-101"].definition.delay_s == 2.0
    assert interlocks["PSLL-101"].definition.actions == ("P-101.stop",)


def test_missing_interlocks_key_yields_an_empty_mapping():
    assert load_interlocks({}) == {}


def test_a_repeated_tag_is_rejected_rather_than_silently_overwritten():
    config = {
        "interlocks": [
            {
                "tag": "PSLL-101",
                "condition": "P-101.suction_pressure < 20.0",
                "delay_s": 2.0,
                "actions": ["P-101.stop"],
                "reset": "manual",
            },
            {
                "tag": "PSLL-101",
                "condition": "P-101.suction_pressure < 10.0",
                "delay_s": 1.0,
                "actions": ["P-101.stop"],
                "reset": "manual",
            },
        ],
    }

    with pytest.raises(ValueError, match="PSLL-101"):
        load_interlocks(config)


def test_a_malformed_condition_names_the_offending_tag():
    config = {
        "interlocks": [
            {
                "tag": "PSLL-101",
                "condition": "nonsense",
                "delay_s": 2.0,
                "actions": ["P-101.stop"],
                "reset": "manual",
            },
        ],
    }

    with pytest.raises(ValueError, match="PSLL-101"):
        load_interlocks(config)


def test_get_interlock_returns_the_matching_interlock():
    interlocks = load_interlocks(
        {
            "interlocks": [
                {
                    "tag": "PSLL-101",
                    "condition": "P-101.suction_pressure < 20.0",
                    "delay_s": 2.0,
                    "actions": ["P-101.stop"],
                    "reset": "manual",
                },
            ],
        }
    )

    assert get_interlock(interlocks, "PSLL-101") is interlocks["PSLL-101"]


def test_get_interlock_warns_rather_than_crashes_when_a_tag_is_unconfigured():
    interlocks = load_interlocks({"interlocks": []})

    with pytest.warns(UserWarning, match="PSLL-101"):
        result = get_interlock(interlocks, "PSLL-101")

    assert result is None


def test_the_reference_plant_config_defines_loadable_interlocks():
    plant = load_plant_file(PLANTS / "olefins_lite.yaml")
    config = plant.to_config()

    assert validate(config) == []

    interlocks = load_interlocks(config)

    assert {"LSHH-101", "PSHH-101", "FSLL-101"} <= set(interlocks)

    # The design point sits on the safe side of every trip threshold (T5-5),
    # so every configured interlock reads NORMAL the moment the plant loads.
    assert interlocks["LSHH-101"].evaluate(0.5, dt=1.0) is InterlockState.NORMAL
    assert interlocks["PSHH-101"].evaluate(304.0, dt=1.0) is InterlockState.NORMAL
    assert interlocks["FSLL-101"].evaluate(50.0, dt=1.0) is InterlockState.NORMAL
