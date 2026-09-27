import copy

import pytest

from app.controls.loader import LoopBinding, LoopConfigError, load_loops
from app.controls.modes import Mode
from app.controls.pid import Action
from app.plant.loader import load_plant


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


def rejected(config):
    plant = load_plant(config)

    with pytest.raises(LoopConfigError) as raised:
        load_loops(plant)

    return raised.value.errors


def test_loads_and_binds_a_loop_from_the_controllers_key():
    plant = load_plant(valid_config())

    bindings = load_loops(plant)

    assert set(bindings) == {"PIC-101"}

    binding = bindings["PIC-101"]
    assert isinstance(binding, LoopBinding)
    assert binding.pv_tag == "N-02"
    assert binding.pv_node is plant.nodes["N-02"]
    assert binding.out_tag == "FV-101"
    assert binding.loop.mode is Mode.MANUAL
    assert binding.loop.pid.kp == 0.01
    assert binding.loop.pid.ki == 0.005
    assert binding.loop.pid.setpoint == 65.0
    assert binding.loop.pid.output_min == 0.0
    assert binding.loop.pid.output_max == 1.0


def test_output_setter_is_bound_to_the_real_device():
    plant = load_plant(valid_config())
    binding = load_loops(plant)["PIC-101"]

    binding.output_setter(0.7)

    assert plant.devices["FV-101"].position_target == 0.7


@pytest.mark.parametrize("mode", ["MANUAL", "AUTO"])
def test_a_loop_starts_at_the_command_its_output_already_holds(mode):
    config = valid_config()
    config["controllers"][0]["mode"] = mode
    config["equipment"][1]["design"] = {"position": 0.4, "position_target": 0.4}

    loop = load_loops(load_plant(config))["PIC-101"].loop

    assert loop.output == pytest.approx(0.4)
    assert loop.manual_output == pytest.approx(0.4)


def test_the_pv_point_is_the_pv_node_pressure():
    binding = load_loops(load_plant(valid_config()))["PIC-101"]

    assert binding.pv_point == ("nodes", "N-02", "pressure")


def test_no_controllers_key_binds_nothing():
    config = valid_config()
    del config["controllers"]

    plant = load_plant(config)

    assert load_loops(plant) == {}


def test_auto_mode_loads_as_auto():
    config = valid_config()
    config["controllers"][0]["mode"] = "AUTO"

    binding = load_loops(load_plant(config))["PIC-101"]

    assert binding.loop.mode is Mode.AUTO


def test_an_entry_with_no_action_loads_reverse_acting():
    binding = load_loops(load_plant(valid_config()))["PIC-101"]

    assert binding.loop.pid.action is Action.REVERSE


@pytest.mark.parametrize("action, expected", [("DIRECT", Action.DIRECT), ("REVERSE", Action.REVERSE)])
def test_an_entry_loads_the_action_it_names(action, expected):
    config = valid_config()
    config["controllers"][0]["action"] = action

    binding = load_loops(load_plant(config))["PIC-101"]

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
