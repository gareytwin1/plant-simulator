"""
Operator view (T16-13) - what a plant's control system would show a browser.

A device's `get_state()` row is everything the model knows: fault flags,
malfunction-writable parameters, design constants and model internals as well
as the readings an operator can see. Sending it unchanged would put a fault
the operator is meant to diagnose on the console. `operator_view` cuts every
equipment row of a snapshot down to the fields its device class lists in
`VISIBLE`, and leaves every other section as the snapshot has it.

`VISIBLE` is an explicit allowlist in the same shape, and for the same reason,
as `ACTIONS`, `WRITABLE`, `OUTPUTS` and `TRIP_ACTIONS`: a new class or a new
state field shows nothing until someone decides it should. The rule is to show
measurements, run status and the operator's own commands, never a fault flag,
a malfunction-writable parameter, a design constant or a model internal. A
field moving onto the list is a reviewed change with its reason in the PR. A
class with no entry, or a tag the plant does not know, shows an empty row.

Server-side consumers - trips, alarms, scenarios, scoring, replay - read the
full snapshot; only what leaves for a browser goes through here. `nodes` and
`streams` still show every solved point, because instruments are not in C3
(recorded technical debt).
"""

from collections.abc import Mapping

from app.engine.snapshot import Snapshot
from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.exchanger import HeatExchanger
from app.equipment.furnace import Furnace
from app.equipment.pump import CentrifugalPump
from app.equipment.relief import ReliefValve
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.statetypes import JSONValue


# A machine's flow and its suction and discharge pressures are the solved
# points the engine composes onto its row (T9-5); a valve's are already its
# stream's flow and its two nodes' pressures, so it does not repeat them.
MACHINE_POINTS = frozenset({"flow", "inlet_pressure", "outlet_pressure"})

VISIBLE: dict[type[Equipment], frozenset[str]] = {
    CentrifugalPump: frozenset({"running", "speed", "speed_target"}) | MACHINE_POINTS,
    GasCompressor: frozenset({"running", "load", "load_target"}) | MACHINE_POINTS,
    ControlValve: frozenset({"position", "position_target"}),
    Vessel: frozenset({"level", "pressure"}),
    HeatExchanger: frozenset({"inlet_temperature"}),
    Furnace: frozenset({"firing_rate", "duty_setpoint"}),
    ReliefValve: frozenset({"lifted"}),
}


# What a device is, in the operator's words, for a faceplate's heading (T20-2).
# Same allowlist rule as VISIBLE: a class with no entry has no kind ('').
KIND: dict[type[Equipment], str] = {
    CentrifugalPump: "Centrifugal pump",
    GasCompressor: "Gas compressor",
    ControlValve: "Control valve",
    Vessel: "Vessel",
    HeatExchanger: "Heat exchanger",
    Furnace: "Furnace",
    ReliefValve: "Relief valve",
}


# The operator's word for a published field, where it differs from the field
# with its underscores as spaces. Keyed on the field, never a port name: the
# generic inlet and outlet are what a machine's suction and discharge are
# called on a console. An alarm message and a trend pen both read it.
DESCRIPTORS: dict[type[Equipment], dict[str, str]] = {
    CentrifugalPump: {"inlet_pressure": "suction pressure", "outlet_pressure": "discharge pressure"},
    GasCompressor: {"inlet_pressure": "suction pressure", "outlet_pressure": "discharge pressure"},
}


def descriptor(device: Equipment | None, field: str) -> str:
    """The operator's word for `field` on `device`: its class's `DESCRIPTORS`
    entry, else the field with its underscores as spaces. A tag the plant does
    not know (`device` is None) takes the fallback too."""
    if device is not None:
        words = DESCRIPTORS.get(type(device), {}).get(field)

        if words is not None:
            return words

    return field.replace("_", " ")


def operator_view(
    snapshot: Snapshot,
    equipment: Mapping[str, Equipment],
) -> dict[str, JSONValue]:
    """`snapshot.as_dict()` with each equipment row cut to its class's
    `VISIBLE` fields. `equipment` maps tag to device, from the plant that
    published `snapshot`."""
    view = snapshot.as_dict()
    rows = snapshot.equipment

    view["equipment"] = {tag: _visible_row(row, equipment.get(tag)) for tag, row in rows.items()}

    return view


def _visible_row(row: Mapping[str, JSONValue], device: Equipment | None) -> dict[str, JSONValue]:
    if device is None:
        return {}

    fields = VISIBLE.get(type(device), frozenset())

    return {name: value for name, value in row.items() if name in fields}
