"""Typed ports (T3-7): phase, purpose and control.

The contract under test is docs/ADR_0002_TYPED_PORTS.md as amended by
Amendment 1. A connection carries a name that means nothing to the code, a
direction, a phase, a purpose, and optionally a control. `purpose` and
`control` are orthogonal — a level-controlled drain declares both, a manual
drain declares only the purpose — and neither may ever reach a mass balance.

T3-7 builds the vocabulary. T5-6 aggregates over it, so nothing here asserts
anything about a flow.

The final section, T5-7, covers Amendment 3's configured-port grammar:
whether a `ports` entry carries `direction` decides fixed-port versus
configured-port mode, only an opted-in class (`Vessel`) may use the latter,
and the two modes may not mix within one item. `SeparatorDouble` above stays
a **fixed**-port double throughout — it never opts in — so it is exactly the
device those tests need to prove a direction is refused on ordinary
equipment.
"""

import copy

import pytest

from app.equipment.base import (
    INLET,
    OUTLET,
    PORT_CONTROLS,
    PORT_PHASES,
    PORT_PURPOSES,
    Equipment,
    Port,
)
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.equipment.vessel import Vessel
from app.plant.loader import PlantConfigError, load_plant
from app.plant.validate import validate


class SeparatorDouble(Equipment):
    """One inlet and two outlets, declared by name and direction only.

    Deliberately says nothing about phase or purpose: T3-7's whole point is
    that the semantics arrive from configuration, so a device that hard-coded
    them would not exercise the contract.
    """

    def __init__(self, tag="V-101"):
        super().__init__(
            tag,
            ports={
                "liquid_in": INLET,
                "liquid_out": OUTLET,
                "vapor_out": OUTLET,
            },
        )

    def integrate(self, dt):
        pass

    def characteristic(self, flow):
        return 0.0

    def get_state(self):
        return {}


DEVICE_TYPES = {
    "pump": CentrifugalPump,
    "compressor": GasCompressor,
    "vessel": SeparatorDouble,
}


def base_config():
    return {
        "nodes": [
            {"id": "L-01", "boundary": True, "pressure": 50.0, "domain": "liquid"},
            {"id": "L-02", "boundary": False, "pressure": 60.0, "domain": "liquid"},
            {"id": "G-01", "boundary": True, "pressure": 100.0, "domain": "gas"},
            {"id": "G-02", "boundary": True, "pressure": 900.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "L-01",
                "node_out": "L-02",
                "design": {"max_flow": 1000.0},
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "G-01",
                "node_out": "G-02",
                "design": {},
            },
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": {
                    "liquid_in": "L-02",
                    "liquid_out": "L-01",
                    "vapor_out": "G-01",
                },
                "paths": [],
                "design": {},
            },
        ],
    }


VESSEL = 2

NODE_OF = {"liquid_in": "L-02", "liquid_out": "L-01", "vapor_out": "G-01"}


def typed(port_name, **descriptors):
    """A config whose one vessel port is declared in the typed form."""
    config = base_config()
    config["equipment"][VESSEL]["ports"][port_name] = {
        "node": NODE_OF[port_name],
        **descriptors,
    }

    return config


def load(config):
    return load_plant(config, device_types=DEVICE_TYPES)


def rejected(config):
    with pytest.raises(PlantConfigError) as raised:
        load(config)

    return raised.value.errors


def vessel_port(config, port_name):
    return load(config).devices["V-101"].port(port_name)


# The Port contract itself


def test_a_port_is_undeclared_until_configuration_says_otherwise():
    port = Port("suction", INLET)

    assert port.phase is None
    assert port.purpose is None
    assert port.control is None


def test_a_port_carries_every_descriptor_it_was_given():
    port = Port("draw", OUTLET, phase="liquid", purpose="drain", control="level")

    assert port.direction == OUTLET
    assert port.phase == "liquid"
    assert port.purpose == "drain"
    assert port.control == "level"


@pytest.mark.parametrize("phase", PORT_PHASES)
def test_port_accepts_every_phase(phase):
    assert Port("n", INLET, phase=phase).phase == phase


@pytest.mark.parametrize("purpose", PORT_PURPOSES)
def test_port_accepts_every_purpose(purpose):
    assert Port("n", INLET, purpose=purpose).purpose == purpose


@pytest.mark.parametrize("control", PORT_CONTROLS)
def test_port_accepts_every_control(control):
    assert Port("n", INLET, control=control).control == control


def test_port_rejects_an_unknown_phase():
    with pytest.raises(ValueError):
        Port("n", INLET, phase="plasma")


def test_port_rejects_mixed_phase_as_reserved_rather_than_unknown():
    with pytest.raises(ValueError) as raised:
        Port("n", INLET, phase="mixed")

    assert "reserved" in str(raised.value)


def test_port_rejects_an_unknown_purpose():
    with pytest.raises(ValueError):
        Port("n", INLET, purpose="recycle")


def test_port_rejects_an_unknown_control():
    with pytest.raises(ValueError):
        Port("n", INLET, control="composition")


def test_declare_replaces_every_descriptor_together():
    port = Port("draw", OUTLET, phase="liquid", purpose="drain", control="level")

    port.declare(phase="vapor", purpose="vent")

    assert port.phase == "vapor"
    assert port.purpose == "vent"
    assert port.control is None


def test_a_port_still_refuses_to_carry_process_state():
    port = Port("draw", OUTLET, phase="liquid", purpose="drain")

    for name in ("pressure", "flow", "temperature", "level"):
        with pytest.raises(AttributeError):
            setattr(port, name, 42.0)


def test_add_port_carries_the_descriptors_through():
    device = SeparatorDouble()

    port = device.add_port("relief", OUTLET, phase="vapor", purpose="relief")

    assert device.port("relief") is port
    assert (port.phase, port.purpose, port.control) == ("vapor", "relief", None)


def test_descriptors_survive_a_reset():
    device = SeparatorDouble()
    device.port("liquid_out").declare(phase="liquid", purpose="drain", control="level")

    device.reset()

    port = device.port("liquid_out")

    assert (port.phase, port.purpose, port.control) == ("liquid", "drain", "level")


# purpose and control are orthogonal


ORTHOGONAL = [
    ("vapor_out", "vapor", "process", None),
    ("vapor_out", "vapor", "vent", None),
    ("vapor_out", "vapor", "vent", "pressure"),
    ("vapor_out", "vapor", "relief", None),
    ("liquid_out", "liquid", "drain", None),
    ("liquid_out", "liquid", "drain", "level"),
    ("liquid_in", "liquid", "process", "flow"),
    ("liquid_in", "liquid", "process", "temperature"),
]


@pytest.mark.parametrize("port_name,phase,purpose,control", ORTHOGONAL)
def test_every_orthogonal_combination_loads(port_name, phase, purpose, control):
    descriptors = {"phase": phase, "purpose": purpose}

    if control is not None:
        descriptors["control"] = control

    port = vessel_port(typed(port_name, **descriptors), port_name)

    assert (port.phase, port.purpose, port.control) == (phase, purpose, control)


def test_a_manual_drain_and_a_level_controlled_drain_are_both_drains():
    manual = vessel_port(
        typed("liquid_out", phase="liquid", purpose="drain"),
        "liquid_out",
    )
    controlled = vessel_port(
        typed("liquid_out", phase="liquid", purpose="drain", control="level"),
        "liquid_out",
    )

    assert manual.purpose == controlled.purpose == "drain"
    assert manual.control is None
    assert controlled.control == "level"


def test_no_engineering_restriction_is_imposed_between_descriptors():
    # These combinations are unusual in the V1 fixture and perfectly real in a
    # plant. They are a property of a configuration, never of the vocabulary,
    # so Port and the loader must not have an opinion (Amendment 1 A.5).
    liquid_relief = vessel_port(
        typed("liquid_out", phase="liquid", purpose="relief"),
        "liquid_out",
    )
    vapor_under_level_control = vessel_port(
        typed("vapor_out", phase="vapor", purpose="process", control="level"),
        "vapor_out",
    )

    assert (liquid_relief.phase, liquid_relief.purpose) == ("liquid", "relief")
    assert vapor_under_level_control.control == "level"


def test_direction_is_untouched_by_typing():
    config = typed("liquid_in", phase="liquid", purpose="process")
    device = load(config).devices["V-101"]

    assert device.port("liquid_in").direction == INLET
    assert device.port("liquid_out").direction == OUTLET
    assert device.port("vapor_out").direction == OUTLET


# Typed-entry validation, and the config path it names


def test_a_typed_entry_missing_node_is_rejected():
    config = base_config()
    config["equipment"][VESSEL]["ports"]["vapor_out"] = {
        "phase": "vapor",
        "purpose": "process",
    }

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out" in error for error in errors)
    assert any("'node'" in error for error in errors)


def test_a_typed_entry_missing_phase_is_rejected():
    config = typed("vapor_out", purpose="process")

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out" in error for error in errors)
    assert any("'phase'" in error for error in errors)


def test_a_typed_entry_missing_purpose_is_rejected():
    # No half-typed form: a node and a phase are not enough, and purpose is
    # never defaulted to process.
    config = typed("vapor_out", phase="vapor")

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out" in error for error in errors)
    assert any("'purpose'" in error for error in errors)


def test_an_unknown_phase_is_rejected_naming_the_descriptor():
    config = typed("vapor_out", phase="plasma", purpose="process")

    assert any(
        "$.equipment[2].ports.vapor_out.phase" in error for error in rejected(config)
    )


def test_mixed_phase_is_rejected_at_load_as_reserved():
    config = typed("vapor_out", phase="mixed", purpose="process")

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out.phase" in error for error in errors)
    assert any("reserved" in error for error in errors)


def test_an_unknown_purpose_is_rejected_naming_the_descriptor():
    config = typed("vapor_out", phase="vapor", purpose="recycle")

    assert any(
        "$.equipment[2].ports.vapor_out.purpose" in error for error in rejected(config)
    )


def test_an_unknown_control_is_rejected_naming_the_descriptor():
    config = typed("vapor_out", phase="vapor", purpose="vent", control="composition")

    assert any(
        "$.equipment[2].ports.vapor_out.control" in error for error in rejected(config)
    )


def test_an_explicit_null_control_is_rejected_rather_than_read_as_uncontrolled():
    config = typed("vapor_out", phase="vapor", purpose="vent", control=None)

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out.control" in error for error in errors)
    assert any("omit control" in error for error in errors)


def test_an_unexpected_property_is_rejected():
    config = typed("vapor_out", phase="vapor", purpose="process", service="relief")

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out" in error for error in errors)
    assert any("'service'" in error for error in errors)


def test_a_malformed_entry_is_rejected_naming_what_it_was():
    config = base_config()
    config["equipment"][VESSEL]["ports"]["vapor_out"] = 7

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out" in error for error in errors)
    assert any("got number" in error for error in errors)


def test_a_typed_entry_with_an_unknown_node_is_rejected():
    config = typed("vapor_out", phase="vapor", purpose="process")
    config["equipment"][VESSEL]["ports"]["vapor_out"]["node"] = "G-99"

    errors = rejected(config)

    assert any("$.equipment[2].ports.vapor_out" in error for error in errors)
    assert any("unknown node 'G-99'" in error for error in errors)


def test_a_non_string_node_is_rejected():
    config = typed("vapor_out", phase="vapor", purpose="process")
    config["equipment"][VESSEL]["ports"]["vapor_out"]["node"] = 7

    assert any(
        "$.equipment[2].ports.vapor_out.node" in error for error in rejected(config)
    )


def test_every_problem_in_one_entry_is_reported_together():
    config = typed("vapor_out", phase="steam", purpose="recycle", control="composition")

    errors = rejected(config)

    for descriptor in ("phase", "purpose", "control"):
        assert any(
            f"$.equipment[2].ports.vapor_out.{descriptor}" in error
            for error in errors
        )


def test_the_schema_stays_out_of_the_alternation():
    # The C3 validator implements no oneOf, so the schema must not pretend to
    # enforce this. Both forms, and an invalid one, pass validate() untouched
    # and are the loader's business (ADR 0002 section 2.6).
    for entry in (
        "G-01",
        {"node": "G-01", "phase": "vapor", "purpose": "process"},
        {"node": "G-01", "phase": "mixed", "purpose": "process"},
    ):
        config = base_config()
        config["equipment"][VESSEL]["ports"]["vapor_out"] = entry

        assert validate(config) == []


# Round trip


def test_a_typed_entry_without_control_round_trips_as_a_typed_object():
    config = typed("vapor_out", phase="vapor", purpose="process")

    emitted = load(config).to_config()["equipment"][VESSEL]["ports"]

    assert emitted["vapor_out"] == {
        "node": "G-01",
        "phase": "vapor",
        "purpose": "process",
    }


def test_a_typed_entry_with_control_round_trips_with_its_control():
    config = typed("vapor_out", phase="vapor", purpose="vent", control="pressure")

    emitted = load(config).to_config()["equipment"][VESSEL]["ports"]

    assert emitted["vapor_out"] == {
        "node": "G-01",
        "phase": "vapor",
        "purpose": "vent",
        "control": "pressure",
    }


def test_a_legacy_string_entry_is_never_upgraded():
    emitted = load(base_config()).to_config()["equipment"][VESSEL]["ports"]

    assert emitted == {
        "liquid_in": "L-02",
        "liquid_out": "L-01",
        "vapor_out": "G-01",
    }


def test_sugar_is_still_emitted_as_sugar():
    config = typed("vapor_out", phase="vapor", purpose="process")

    emitted = load(config).to_config()["equipment"][0]

    assert emitted["node_in"] == "L-01"
    assert emitted["node_out"] == "L-02"
    assert "ports" not in emitted
    assert "paths" not in emitted


def test_typed_and_legacy_entries_coexist_and_each_keeps_its_form():
    config = typed("liquid_out", phase="liquid", purpose="drain", control="level")

    emitted = load(config).to_config()["equipment"][VESSEL]["ports"]

    assert emitted["liquid_in"] == "L-02"
    assert emitted["vapor_out"] == "G-01"
    assert emitted["liquid_out"] == {
        "node": "L-01",
        "phase": "liquid",
        "purpose": "drain",
        "control": "level",
    }


def test_the_round_trip_is_idempotent():
    config = typed("liquid_out", phase="liquid", purpose="drain", control="level")

    once = load(config).to_config()
    twice = load(copy.deepcopy(once)).to_config()

    assert twice == once


def test_a_legacy_config_still_loads_and_leaves_its_ports_untyped():
    device = load(base_config()).devices["V-101"]

    for port in device.ports.values():
        assert port.phase is None
        assert port.purpose is None
        assert port.control is None


# --- T5-7: configured-port mode (ADR 0002, Amendment 3 C.4-C.7) -----------
#
# These use the production Vessel, never SeparatorDouble: SeparatorDouble is
# what a FIXED-port device looks like, which is exactly what several of these
# tests need it to keep being.


CONFIGURED_TYPES = {"vessel": Vessel}


def configured_vessel_config(ports):
    """A minimal plant whose vessel takes its ports from configuration.

    One liquid node and one gas node, each carrying no branch of its own, so
    each is trivially "one connected piece" and needs nothing else wired.
    """
    return {
        "nodes": [
            {"id": "N-101", "boundary": True, "pressure": 100.0, "domain": "liquid"},
            {"id": "N-201", "boundary": True, "pressure": 80.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": ports,
                "paths": [],
                "design": {},
            },
        ],
    }


def fixed_vessel_config(ports=None):
    return {
        "nodes": [{"id": "N-101", "boundary": True, "pressure": 100.0}],
        "equipment": [
            {
                "tag": "V-101",
                "type": "vessel",
                "ports": ports or {"inlet": "N-101", "outlet": "N-101"},
                "paths": [],
                "design": {},
            },
        ],
    }


THREE_CONFIGURED_PORTS = {
    "feed": {
        "node": "N-101",
        "phase": "liquid",
        "purpose": "process",
        "direction": INLET,
    },
    "drain": {
        "node": "N-101",
        "phase": "liquid",
        "purpose": "drain",
        "direction": OUTLET,
    },
    "vapor_out": {
        "node": "N-201",
        "phase": "vapor",
        "purpose": "process",
        "direction": OUTLET,
    },
}


def load_configured(config):
    return load_plant(config, device_types=CONFIGURED_TYPES)


def rejected_configured(config):
    with pytest.raises(PlantConfigError) as raised:
        load_configured(config)

    return raised.value.errors


def test_vessel_opts_in_to_configured_ports():
    assert Vessel.accepts_configured_ports is True


def test_no_direction_anywhere_keeps_the_fixed_ports():
    device = load_configured(fixed_vessel_config()).devices["V-101"]

    assert set(device.ports) == {"inlet", "outlet"}
    assert device.port("inlet").direction == INLET
    assert device.port("outlet").direction == OUTLET


def test_a_configured_vessel_builds_exactly_the_declared_ports():
    device = load_configured(
        configured_vessel_config(THREE_CONFIGURED_PORTS),
    ).devices["V-101"]

    assert list(device.ports) == ["feed", "drain", "vapor_out"]
    assert device.port("feed").direction == INLET
    assert device.port("drain").direction == OUTLET
    assert device.port("vapor_out").direction == OUTLET
    assert device.port("feed").phase == "liquid"
    assert device.port("vapor_out").phase == "vapor"


def test_configured_port_order_follows_config_order():
    ports = {
        "vapor_out": THREE_CONFIGURED_PORTS["vapor_out"],
        "feed": THREE_CONFIGURED_PORTS["feed"],
        "drain": THREE_CONFIGURED_PORTS["drain"],
    }
    device = load_configured(configured_vessel_config(ports)).devices["V-101"]

    assert list(device.ports) == ["vapor_out", "feed", "drain"]


def test_a_configured_vessel_may_have_a_single_port():
    ports = {"drain": THREE_CONFIGURED_PORTS["drain"]}
    device = load_configured(configured_vessel_config(ports)).devices["V-101"]

    assert list(device.ports) == ["drain"]


def test_fixed_port_equipment_given_direction_is_rejected():
    config = base_config()
    config["equipment"][VESSEL]["ports"]["vapor_out"] = {
        "node": "G-01",
        "phase": "vapor",
        "purpose": "process",
        "direction": OUTLET,
    }

    errors = rejected(config)

    assert any(
        "$.equipment[2].ports.vapor_out.direction" in error for error in errors
    )
    assert any("fixed port set" in error for error in errors)


def test_partial_direction_with_a_legacy_string_sibling_is_rejected():
    ports = {
        "feed": THREE_CONFIGURED_PORTS["feed"],
        "outlet": "N-101",
    }
    errors = rejected_configured(configured_vessel_config(ports))

    assert any("$.equipment[0].ports.outlet" in error for error in errors)
    assert any("a node id string cannot declare a direction" in error for error in errors)


def test_partial_direction_with_a_typed_sibling_missing_one_is_rejected():
    ports = {
        "feed": THREE_CONFIGURED_PORTS["feed"],
        "drain": {"node": "N-101", "phase": "liquid", "purpose": "drain"},
    }
    errors = rejected_configured(configured_vessel_config(ports))

    assert any("$.equipment[0].ports.drain" in error for error in errors)
    assert any("missing direction" in error for error in errors)


def test_an_unknown_direction_value_is_rejected():
    ports = dict(THREE_CONFIGURED_PORTS)
    ports["feed"] = {**ports["feed"], "direction": "sideways"}

    errors = rejected_configured(configured_vessel_config(ports))

    assert any(
        "$.equipment[0].ports.feed.direction" in error for error in errors
    )
    assert any("'sideways'" in error for error in errors)


def test_accepts_configured_ports_cannot_be_set_through_design_on_a_vessel():
    config = fixed_vessel_config()
    config["equipment"][0]["design"] = {"accepts_configured_ports": False}

    errors = rejected_configured(config)

    assert any(
        "$.equipment[0].design.accepts_configured_ports" in error
        for error in errors
    )
    assert any("capability marker" in error for error in errors)


def test_accepts_configured_ports_cannot_be_set_through_design_on_a_fixed_device():
    config = base_config()
    config["equipment"][VESSEL]["design"]["accepts_configured_ports"] = True

    errors = rejected(config)

    assert any(
        "$.equipment[2].design.accepts_configured_ports" in error
        for error in errors
    )
    assert any("capability marker" in error for error in errors)
    # The generic "has no such attribute" refusal never fires for it.
    assert not any("has no such attribute" in error for error in errors)


def test_a_configured_entry_round_trips_with_direction_and_order():
    ports = {
        "vapor_out": THREE_CONFIGURED_PORTS["vapor_out"],
        "feed": THREE_CONFIGURED_PORTS["feed"],
        "drain": THREE_CONFIGURED_PORTS["drain"],
    }
    plant = load_configured(configured_vessel_config(ports))

    emitted = plant.to_config()["equipment"][0]["ports"]

    assert list(emitted) == ["vapor_out", "feed", "drain"]
    assert emitted["feed"] == {
        "node": "N-101",
        "direction": "inlet",
        "phase": "liquid",
        "purpose": "process",
    }


def test_a_fixed_port_config_round_trips_without_gaining_direction():
    plant = load_configured(fixed_vessel_config())

    emitted = plant.to_config()["equipment"][0]["ports"]

    assert emitted == {"inlet": "N-101", "outlet": "N-101"}


def test_the_round_trip_of_a_configured_vessel_is_idempotent():
    config = configured_vessel_config(THREE_CONFIGURED_PORTS)

    once = load_configured(config).to_config()
    twice = load_configured(copy.deepcopy(once)).to_config()

    assert twice == once
