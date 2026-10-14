import copy

import pytest

from app.controls.loader import LoopBinding, LoopConfigError, load_loops
from app.controls.modes import Mode
from app.controls.pid import Action
from app.engine.coupling import build_couplings
from app.engine.engine import Engine
from app.plant.loader import PlantConfigError, load_plant
from tests.test_loop_execution import vessel_config


def valid_config():
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 50.0},
            {"id": "N-02", "boundary": False, "pressure": 60.0},
            {"id": "N-03", "boundary": True, "pressure": 875.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {"shutoff_pressure_rise": 90.0, "max_flow": 1000.0},
            },
            {
                "tag": "FV-101",
                "type": "control_valve",
                "node_in": "N-02",
                "node_out": "N-03",
                "design": {},
            },
        ],
        "controllers": [
            {
                "tag": "PIC-101",
                "pv": "N-02",
                "sp": 65.0,
                "out": "FV-101",
                "mode": "MANUAL",
                "kp": 0.01,
                "ki": 0.005,
                "kd": 0.0,
            },
        ],
    }


def bind(plant):
    """Bind `plant`'s loops against the plant as an engine with no loops
    publishes it."""
    devices = plant.devices.values()
    engine = Engine(devices, topologies=plant.topologies, couplings=build_couplings(devices, plant.topologies))

    return load_loops(plant, engine.snapshot().as_dict(), engine.couplings)


def loaded(config):
    return bind(load_plant(config))


def rejected(config):
    plant = load_plant(config)

    with pytest.raises(LoopConfigError) as raised:
        bind(plant)

    return raised.value.errors


def test_loads_and_binds_a_loop_from_the_controllers_key():
    plant = load_plant(valid_config())

    bindings = bind(plant)

    assert set(bindings) == {"PIC-101"}

    binding = bindings["PIC-101"]
    assert isinstance(binding, LoopBinding)
    assert binding.out_tag == "FV-101"
    assert binding.loop.mode is Mode.MANUAL
    assert binding.loop.pid.kp == 0.01
    assert binding.loop.pid.ki == 0.005
    assert binding.loop.pid.setpoint == 65.0
    assert binding.loop.pid.output_min == 0.0
    assert binding.loop.pid.output_max == 1.0


def test_output_setter_is_bound_to_the_real_device():
    plant = load_plant(valid_config())
    binding = bind(plant)["PIC-101"]

    binding.output_setter(0.7)

    assert plant.devices["FV-101"].position_target == 0.7


@pytest.mark.parametrize("mode", ["MANUAL", "AUTO"])
def test_a_loop_starts_at_the_command_its_output_already_holds(mode):
    config = valid_config()
    config["controllers"][0]["mode"] = mode
    config["equipment"][1]["design"] = {"position": 0.4, "position_target": 0.4}

    loop = loaded(config)["PIC-101"].loop

    assert loop.output == pytest.approx(0.4)
    assert loop.manual_output == pytest.approx(0.4)


def test_the_pv_point_is_the_pv_node_pressure():
    binding = loaded(valid_config())["PIC-101"]

    assert binding.pv_point == ("nodes", "N-02", "pressure")


def test_no_controllers_key_binds_nothing():
    config = valid_config()
    del config["controllers"]

    plant = load_plant(config)

    assert bind(plant) == {}


def test_auto_mode_loads_as_auto():
    config = valid_config()
    config["controllers"][0]["mode"] = "AUTO"

    binding = loaded(config)["PIC-101"]

    assert binding.loop.mode is Mode.AUTO


def test_an_entry_with_no_action_loads_reverse_acting():
    binding = loaded(valid_config())["PIC-101"]

    assert binding.loop.pid.action is Action.REVERSE


@pytest.mark.parametrize("action, expected", [("DIRECT", Action.DIRECT), ("REVERSE", Action.REVERSE)])
def test_an_entry_loads_the_action_it_names(action, expected):
    config = valid_config()
    config["controllers"][0]["action"] = action

    binding = loaded(config)["PIC-101"]

    assert binding.loop.pid.action is expected


def test_unknown_pv_tag_is_rejected_at_load():
    config = valid_config()
    config["controllers"][0]["pv"] = "N-999"

    errors = rejected(config)

    assert any(
        "$.controllers[0].pv" in e and "N-999" in e and "N-01" in e for e in errors
    )


def test_unknown_out_tag_is_rejected_at_load():
    config = valid_config()
    config["controllers"][0]["out"] = "FV-999"

    errors = rejected(config)

    assert any(
        "$.controllers[0].out" in e and "FV-999" in e and "FV-101" in e
        for e in errors
    )


def test_out_tag_naming_a_device_that_cannot_be_driven_is_rejected():
    config = valid_config()
    config["controllers"][0]["out"] = "P-101"

    errors = rejected(config)

    assert any(
        "$.controllers[0].out" in e and "CentrifugalPump" in e for e in errors
    )


def test_duplicate_loop_tag_is_rejected():
    config = valid_config()
    config["controllers"].append(copy.deepcopy(config["controllers"][0]))

    errors = rejected(config)

    assert any(
        "$.controllers[1].tag" in e and "duplicate" in e and "PIC-101" in e
        for e in errors
    )


def test_two_loops_on_one_output_is_rejected():
    config = valid_config()
    second = copy.deepcopy(config["controllers"][0])
    second["tag"] = "PIC-102"
    config["controllers"].append(second)

    errors = rejected(config)

    assert any(
        "$.controllers[1].out" in e
        and "FV-101" in e
        and "PIC-101" in e
        for e in errors
    )


def test_an_entry_with_a_bad_pv_does_not_claim_its_out_tag():
    """A first entry with an unresolvable pv never produces a binding, so it
    must not block — or be blamed as the prior claimant for — a later, fully
    valid entry that reuses its out tag."""
    config = valid_config()
    config["controllers"][0]["pv"] = "N-999"
    second = copy.deepcopy(config["controllers"][0])
    second["tag"] = "PIC-102"
    second["pv"] = "N-02"
    config["controllers"].append(second)

    errors = rejected(config)

    assert len(errors) == 1
    assert "$.controllers[0].pv" in errors[0]
    assert not any("already driven" in e for e in errors)


def test_an_entry_bad_on_both_pv_and_out_reports_both_problems():
    """A later entry that reuses an already-claimed out tag AND has its own
    unresolvable pv is wrong in two independent ways — unlike the entry
    reusing that out tag being wrong (test above), an entry that has already
    genuinely lost its out tag to an earlier, real loop gains nothing from
    only being told about its pv. Every problem is reported, matching
    app.plant.loader's convention of never picking one error over another
    true one on the same item."""
    config = valid_config()
    second = copy.deepcopy(config["controllers"][0])
    second["tag"] = "PIC-102"
    second["pv"] = "N-999"
    config["controllers"].append(second)

    errors = rejected(config)

    assert len(errors) == 2
    assert any("$.controllers[1].pv" in e for e in errors)
    assert any("$.controllers[1].out" in e and "already driven" in e for e in errors)


def test_negative_ki_is_rejected_with_a_clear_message():
    config = valid_config()
    config["controllers"][0]["ki"] = -1.0

    errors = rejected(config)

    assert any("$.controllers[0]" in e and "ki" in e for e in errors)


def test_every_bad_entry_is_reported_together():
    config = valid_config()
    config["controllers"][0]["pv"] = "N-999"
    config["controllers"].append(
        {
            "tag": "PIC-102",
            "pv": "N-01",
            "sp": 50.0,
            "out": "FV-888",
            "mode": "MANUAL",
            "kp": 1.0,
            "ki": 0.0,
            "kd": 0.0,
        },
    )

    errors = rejected(config)

    assert len(errors) == 2


def test_a_loop_is_not_tunable_unless_its_entry_says_so():
    binding = loaded(valid_config())["PIC-101"]

    assert binding.tunable is False


def test_an_entry_marked_tunable_loads_tunable():
    config = valid_config()
    config["controllers"][0]["tunable"] = True

    binding = loaded(config)["PIC-101"]

    assert binding.tunable is True


def test_tunable_must_be_a_boolean():
    config = valid_config()
    config["controllers"][0]["tunable"] = "yes"

    with pytest.raises(PlantConfigError, match=r"controllers\[0\]\.tunable"):
        load_plant(config)


def test_the_pv_unit_is_psia():
    binding = loaded(valid_config())["PIC-101"]

    assert binding.pv_unit == "psia"


# ---- A pv may name any published point (T20-4) ----


@pytest.mark.parametrize(
    "pv, point, unit",
    [
        ("V-301.level", ("equipment", "V-301", "level"), "fraction"),
        ("B-FV-301.flow", ("streams", "B-FV-301", "flow"), "GPM"),
        ("FV-301.flow", ("equipment", "FV-301", "flow"), "GPM"),
        ("N-302.pressure", ("nodes", "N-302", "pressure"), "psia"),
        ("N-302", ("nodes", "N-302", "pressure"), "psia"),
        ("N-302.temperature", ("nodes", "N-302", "temperature"), "°F"),
        ("B-FV-301.temperature", ("streams", "B-FV-301", "temperature"), "°F"),
        ("B-FV-301.pressure", ("streams", "B-FV-301", "pressure"), "psia"),
    ],
    ids=[
        "equipment-field", "stream", "solved-point", "node-point", "bare-node",
        "node-temperature", "stream-temperature", "stream-pressure",
    ],
)
def test_a_pv_resolves_to_the_point_it_names_and_takes_its_unit(pv, point, unit):
    config = vessel_config()
    config["controllers"][0]["pv"] = pv
    config["controllers"][0]["sp"] = 0.5

    binding = loaded(config)["FIC-301"]

    assert binding.pv_point == point
    assert binding.pv_unit == unit


@pytest.mark.parametrize(
    "config, pv, words",
    [
        (vessel_config, "X-999.level", ["'X-999'", "no device, node or stream"]),
        (vessel_config, "V-301.colour", ["'colour'", "'level'"]),
        (vessel_config, "V-301.", ["publishes no ''"]),
        (valid_config, "P-101.running", ["'P-101.running'", "not a number"]),
    ],
    ids=["unknown-id", "unpublished-field", "empty-field", "not-a-number"],
)
def test_a_pv_that_names_no_number_the_plant_publishes_is_a_load_error(config, pv, words):
    config = config()
    config["controllers"][0]["pv"] = pv

    errors = rejected(config)

    assert len(errors) == 1
    assert errors[0].startswith("$.controllers[0].pv: ")
    assert all(word in errors[0] for word in words)


def test_a_bad_pv_is_refused_when_the_engine_is_built_from_the_plant():
    config = vessel_config()
    config["controllers"][1]["pv"] = "V-301.colour"

    with pytest.raises(LoopConfigError, match=r"\$\.controllers\[1\]\.pv"):
        Engine.from_plant(load_plant(config))


@pytest.mark.parametrize("sp", [1.01, -0.01], ids=["above-1", "below-0"])
def test_a_fraction_setpoint_outside_0_to_1_is_a_load_error(sp):
    config = vessel_config()
    config["controllers"][1]["sp"] = sp

    errors = rejected(config)

    assert errors == [f"$.controllers[1].sp: must be a fraction within [0, 1], got {sp!r}"]


def test_a_negative_setpoint_is_a_load_error():
    config = valid_config()
    config["controllers"][0]["sp"] = -1.0

    assert rejected(config) == ["$.controllers[0].sp: must be non-negative psia, got -1.0"]


def test_a_temperature_loop_takes_a_setpoint_below_zero():
    """A °F point is signed: a chilled or cryogenic stream sits below zero."""
    config = vessel_config()
    config["controllers"][0]["pv"] = "B-FV-301.temperature"
    config["controllers"][0]["sp"] = -40.0

    binding = loaded(config)["FIC-301"]

    assert binding.pv_unit == "°F"
    assert binding.loop.pid.setpoint == -40.0


def test_a_temperature_setpoint_at_or_below_absolute_zero_is_a_load_error():
    config = vessel_config()
    config["controllers"][0]["pv"] = "B-FV-301.temperature"
    config["controllers"][0]["sp"] = -459.67

    assert rejected(config) == [
        "$.controllers[0].sp: must be above -459.67 °F, got -459.67",
    ]


@pytest.mark.parametrize("pv", ["V-301", "B-FV-301"], ids=["device", "stream"])
def test_a_bare_device_or_stream_id_says_to_name_a_field(pv):
    config = vessel_config()
    config["controllers"][0]["pv"] = pv

    errors = rejected(config)

    assert len(errors) == 1
    assert errors[0].startswith(f"$.controllers[0].pv: {pv!r} is not a node; a point names one of its fields")
