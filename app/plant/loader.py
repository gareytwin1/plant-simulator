"""
Plant loader: a C3 config in, a solvable Topology out (T3-3).

The schema validator (T3-1) says whether a config is the right *shape*. This
module says whether it describes a plant the solver can be handed, and it says
so at load time, naming the config path, because the alternative is a
non-convergent solver step whose failure is unreadable.

Every problem found is reported together in one PlantConfigError rather than
one per attempt. A config with a typo'd node id usually has three more, and
fixing them one load at a time is the slow way to find out.

Solvability here means: every reference resolves, every tag and id is unique,
every device port is wired, every flow domain has at least one boundary and each
boundary holds a positive absolute pressure, and each domain is one connected
piece. Whether the solver then *converges* is the solver's business (T4-2).

A node may declare a flow `domain`. `NetworkSolver` solves one domain only, so
the loader partitions the plant into one Topology per domain and rejects a
branch whose two nodes disagree, where the error can name a config path. A node
that declares nothing is in DEFAULT_DOMAIN; a domain is never inherited from a
neighbour.

A device is wired in one of two forms. `node_in` / `node_out` is sugar for a
device with one inlet and one outlet. `ports` (port name -> node id) declares
attachment and nothing else, and `paths` ({from, to} port pairs) declares which
of those become hydraulic branches — one path, one Branch, nothing inferred. A
device with `paths: []` is a coupling device: it lives on `Plant.devices` and in
no Topology. See docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md, Amendment 1.

A `ports` entry is **either** a node-id string **or** a typed object carrying
`node`, `phase`, `purpose` and optionally `control` (T3-7). There is one typed
form and no half-typed one, and absence of `control` means no declared control
role — `control: none` is not how that is written. The alternation is enforced
here rather than in the schema because the C3 validator implements no `oneOf`
and would silently ignore one, and because the error can name the offending
path down to the descriptor. See docs/ADR_0002_TYPED_PORTS.md, Amendment 1.

**Typing comes from configuration, never from the device class.** A device
declares its structural ports by name and direction; the loader is what puts
the semantic metadata on the runtime `Port`. Nothing here may infer a phase or
a purpose from a device type or a port name — that inference is what T3-7
exists to retire. A legacy string entry stays untyped, and `to_config()` emits
back whichever form it read.

**Configured ports (T5-7).** A device class may opt in to receiving its
structural port set from configuration instead of declaring a fixed one —
only `Vessel` does, via a class-level `accepts_configured_ports` marker. In
that mode every `ports` entry is a typed object that also carries
`direction`, and the set the loader builds from them replaces the device's
own, in config order. A config that gives no port a `direction` leaves a
device's own ports untouched, exactly as before; giving only some of them one
is rejected, and so is giving one to a device that has not opted in. The
marker is an internal capability flag, never a design value: `design` may not
set it, on any device. See docs/ADR_0002_TYPED_PORTS.md, Amendment 3.

Plant files may be JSON or YAML (`.json`, `.yaml`, `.yml`). The format is
resolved before validation; after that there is one path.

Only `nodes` and `equipment` build anything. `limits`, `controllers` and
`interlocks` are carried through untouched for the subsystems that will own
them, so a plant round-trips back to the config it came from.
"""

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.equipment.base import (
    INLET,
    OUTLET,
    PORT_CONTROLS,
    PORT_DIRECTIONS,
    PORT_PHASES,
    PORT_PURPOSES,
    RESERVED_PHASES,
    Equipment,
)
from app.equipment.compressor import GasCompressor
from app.equipment.exchanger import HeatExchanger
from app.equipment.pump import CentrifugalPump
from app.equipment.valve import ControlValve
from app.equipment.vessel import Vessel
from app.plant.topology import Branch, Node, Topology
from app.plant.validate import validate


DEFAULT_DOMAIN = "default"

SUGAR_FORM = "sugar"
NAMED_FORM = "named"

CONFIG_SUFFIXES = (".json", ".yaml", ".yml")

PASSTHROUGH_SECTIONS = (
    "limits",
    "controllers",
    "interlocks",
)

# The C3 `type` enum names more kinds than exist yet. A type absent from this
# table is rejected with that reason rather than silently skipped.
DEVICE_TYPES: dict[str, type[Equipment]] = {
    "pump": CentrifugalPump,
    "compressor": GasCompressor,
    "control_valve": ControlValve,
    "vessel": Vessel,
    "heat_exchanger": HeatExchanger,
}


# A typed `ports` entry carries exactly these keys, and the first three are
# required. There is deliberately no half-typed form: a node and a phase with
# no purpose is refused rather than defaulted (ADR 0002, Amendment 1 A.6).
# `direction` (T5-7) is optional per entry — its presence or absence is what
# decides fixed-port versus configured-port mode (ADR 0002, Amendment 3 C.4).
TYPED_PORT_KEYS = (
    "node",
    "phase",
    "purpose",
    "control",
    "direction",
)

REQUIRED_TYPED_PORT_KEYS = (
    "node",
    "phase",
    "purpose",
)

# Internal capability markers a device class may declare (T5-7): a statement
# the class makes about itself, never a design parameter. `design` may not
# set one, on any device, because the loader reads it from the class and
# never from the instance — a design key of this name would be accepted,
# round-tripped, and silently ignored (ADR 0002, Amendment 3 C6a).
CAPABILITY_MARKERS = ("accepts_configured_ports",)

# Structural device state, named here rather than left to the generic
# hasattr()/setattr() path below (D4): `tag` and `ports` are plain instance
# attributes with no property guard, so before this denylist a design block
# could silently rename a device or rewrite its port table after
# construction — `Plant.devices`/`Engine.equipment` key a device by the tag
# it was built with, so a design-set tag would leave that key and
# `device.tag` disagreeing. Every `_`-prefixed name is refused for the same
# reason without being named individually: it is a device's own private
# state (e.g. `_construction_state`, `reset()`'s snapshot), never a
# published design parameter. A per-class allowlist that reopens some of
# this is deferred to the C1 design/configure hook (the open
# `Equipment.reset()` decision in project_state.md).
STRUCTURAL_ATTRIBUTES = ("tag", "ports")


@dataclass(frozen=True)
class PortDeclaration:
    """One C3 `ports` entry, in whichever of the two forms it was written.

    `typed` records the *form*, not the content. A legacy string entry
    declares attachment and nothing more, and `to_config()` has to emit it
    back as the bare string it arrived as — a config is never silently
    upgraded, and reading typedness off the live Port instead would make that
    depend on no device class ever declaring a phase of its own.

    `direction` (T5-7) is set only for a typed entry that declared one, and
    only such an entry ever reaches `add_port` with it. A legacy string, a
    typed entry with no `direction`, and sugar all leave it `None`.
    """

    node: str
    phase: str | None = None
    purpose: str | None = None
    control: str | None = None
    direction: str | None = None
    typed: bool = False


# What `to_config()` needs to reproduce one equipment item: the wiring form,
# the port declarations in config order, and the paths as port-name pairs.
Wiring = tuple[str, dict[str, PortDeclaration], list[tuple[str, str]]]


class PlantConfigError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors

        super().__init__(
            f"plant config rejected, {len(errors)} problem(s):\n"
            + "\n".join(f"  {error}" for error in errors),
        )


class Plant:
    """A loaded plant: its topologies, plus the config that produced it.

    `to_config()` reads design values back off the live devices, so what it
    returns is the plant as it stands, not a stale copy of the file. For a
    freshly loaded plant that is the same config, which is the round-trip the
    loader promises. Node pressures come from `Node.configured_pressure`
    rather than the live one, so a plant that has been stepped round-trips to
    its as-built boundaries.

    `nodes` is every node in config order, across all domains; `topologies`
    holds one Topology per flow domain in the order each first appears.
    `devices` is every device in config order, including coupling devices that
    sit in no Topology — build an Engine from it, never from Topology.devices.

    `to_config()` is form-preserving: a device loaded with node_in/node_out is
    emitted with them, a device loaded with ports/paths is emitted with those,
    and each port entry comes back in the form it was read — a bare node-id
    string stays a string, a typed object comes back typed with its phase,
    purpose and any control intact.
    """

    def __init__(
        self,
        topologies: Mapping[str, Topology],
        nodes: Mapping[str, Node],
        declared_domains: Mapping[str, str],
        devices: Mapping[str, Equipment],
        forms: Mapping[str, str],
        port_declarations: Mapping[str, Mapping[str, PortDeclaration]],
        paths: Mapping[str, list[tuple[str, str]]],
        design_keys: Mapping[str, list[str]],
        equipment_types: Mapping[str, str],
        passthrough: Mapping[str, Any],
    ) -> None:
        self.topologies: dict[str, Topology] = dict(topologies)
        self.nodes: dict[str, Node] = dict(nodes)

        self._declared_domains = dict(declared_domains)
        self.devices: dict[str, Equipment] = dict(devices)

        self._forms = dict(forms)
        self._port_declarations = {
            tag: dict(declarations) for tag, declarations in port_declarations.items()
        }
        self._paths = {tag: list(pairs) for tag, pairs in paths.items()}
        self._design_keys = {tag: list(keys) for tag, keys in design_keys.items()}
        self._equipment_types = dict(equipment_types)
        # Decoded JSON of sections no subsystem interprets yet.
        self._passthrough: dict[str, Any] = copy.deepcopy(dict(passthrough))

    @property
    def topology(self) -> Topology:
        if len(self.topologies) != 1:
            raise ValueError(
                f"plant spans {len(self.topologies)} flow domains "
                f"{list(self.topologies)}, use Plant.topologies",
            )

        return next(iter(self.topologies.values()))

    def to_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "nodes": [self._node_config(node) for node in self.nodes.values()],
            "equipment": [
                self._equipment_config(tag, device)
                for tag, device in self.devices.items()
            ],
        }

        for section in PASSTHROUGH_SECTIONS:
            if section in self._passthrough:
                config[section] = copy.deepcopy(self._passthrough[section])

        return config

    def _equipment_config(self, tag: str, device: Equipment) -> dict[str, Any]:
        item: dict[str, Any] = {
            "tag": tag,
            "type": self._equipment_types[tag],
        }

        if self._forms[tag] == SUGAR_FORM:
            from_port, to_port = self._paths[tag][0]

            item["node_in"] = _attached_node_id(device, from_port)
            item["node_out"] = _attached_node_id(device, to_port)
        else:
            item["ports"] = {
                name: self._port_config(device, name, declaration)
                for name, declaration in self._port_declarations[tag].items()
            }
            item["paths"] = [
                {"from": from_port, "to": to_port}
                for from_port, to_port in self._paths[tag]
            ]

        item["design"] = {key: getattr(device, key) for key in self._design_keys[tag]}

        return item

    def _port_config(
        self,
        device: Equipment,
        name: str,
        declaration: PortDeclaration,
    ) -> str | dict[str, Any]:
        # Form from the declaration, values off the live Port — the same split
        # as design values, and the reason a legacy string is never silently
        # upgraded into a typed object it was not written as.
        node_id = _attached_node_id(device, name)

        if not declaration.typed:
            return node_id

        port = device.port(name)

        entry: dict[str, Any] = {"node": node_id}

        # Emitted only where the declaration carried one (T5-7): a fixed-port
        # config must round-trip without gaining a direction it never had.
        if declaration.direction is not None:
            entry["direction"] = port.direction

        entry["phase"] = port.phase
        entry["purpose"] = port.purpose

        if port.control is not None:
            entry["control"] = port.control

        return entry

    def _node_config(self, node: Node) -> dict[str, Any]:
        # configured_pressure, not pressure: a boundary fed by a coupling
        # device (T5-2) carries an inventory head on top of its as-built
        # value, and emitting that would make a stepped plant round-trip to
        # a config it was never loaded from.
        item: dict[str, Any] = {
            "id": node.id,
            "boundary": node.is_boundary,
            "pressure": node.configured_pressure,
        }

        if node.id in self._declared_domains:
            item["domain"] = self._declared_domains[node.id]

        return item


def load_plant(
    config: Any,
    device_types: Mapping[str, type[Equipment]] | None = None,
) -> Plant:
    types = DEVICE_TYPES if device_types is None else device_types

    errors = validate(config)

    if errors:
        raise PlantConfigError(errors)

    errors = _reference_errors(config, types)

    if errors:
        raise PlantConfigError(errors)

    topologies, nodes, devices, wiring, design_keys, errors = _build(config, types)

    errors += _dangling_port_errors(devices)
    errors += _solvability_errors(topologies)

    if errors:
        raise PlantConfigError(errors)

    return Plant(
        topologies=topologies,
        nodes=nodes,
        declared_domains={
            node["id"]: node["domain"] for node in config["nodes"] if "domain" in node
        },
        devices=devices,
        forms={tag: form for tag, (form, _, _) in wiring.items()},
        port_declarations={
            tag: declarations for tag, (_, declarations, _) in wiring.items()
        },
        paths={tag: pairs for tag, (_, _, pairs) in wiring.items()},
        design_keys=design_keys,
        equipment_types={item["tag"]: item["type"] for item in config["equipment"]},
        passthrough={
            section: config[section]
            for section in PASSTHROUGH_SECTIONS
            if section in config
        },
    )


def load_plant_file(
    path: str | Path,
    device_types: Mapping[str, type[Equipment]] | None = None,
) -> Plant:
    return load_plant(_read_config(Path(path)), device_types)


def _read_config(path: Path) -> Any:
    # Decoded JSON or YAML of a shape only the C3 validator knows. Both formats
    # feed the same load_plant(), so there is one set of semantics.
    suffix = path.suffix.lower()

    if suffix not in CONFIG_SUFFIXES:
        raise PlantConfigError(
            [
                f"{path}: unsupported plant file type {path.suffix!r}, "
                f"only {sorted(CONFIG_SUFFIXES)}",
            ],
        )

    with open(path) as f:
        try:
            if suffix == ".json":
                return json.load(f)

            document = yaml.safe_load(f)
        except (json.JSONDecodeError, yaml.YAMLError) as error:
            raise PlantConfigError(
                [f"{path}: not parseable as {suffix[1:].upper()}: {error}"],
            ) from error

    if document is None:
        raise PlantConfigError([f"{path}: plant file is empty"])

    return document


def _reference_errors(
    config: Mapping[str, Any],
    types: Mapping[str, type[Equipment]],
) -> list[str]:
    errors: list[str] = []

    node_paths: dict[str, str] = {}

    for i, node in enumerate(config["nodes"]):
        path = f"$.nodes[{i}]"

        if node["id"] in node_paths:
            errors.append(
                f"{path}.id: duplicate node id {node['id']!r}, "
                f"first used at {node_paths[node['id']]}",
            )
        else:
            node_paths[node["id"]] = path

        pressure = node["pressure"]

        if not math.isfinite(pressure):
            # Layered with validate.py's own non-finite check on every
            # schema-typed number (D1): this one is the loader's, so a
            # config built and passed to load_plant() without going
            # through the schema validator — as tests do throughout this
            # module — still gets it, and boundary/internal pressure get
            # a domain-worded message instead of the validator's generic
            # one.
            kind = "boundary" if node["boundary"] else "internal"
            errors.append(
                f"{path}.pressure: {kind} node {node['id']!r} holds "
                f"{pressure} psia — a node pressure must be a finite number",
            )
        elif node["boundary"] and pressure <= 0.0:
            errors.append(
                f"{path}.pressure: boundary node {node['id']!r} holds "
                f"{pressure} psia — a boundary needs a positive "
                f"absolute pressure to anchor the network",
            )

    errors += _boundary_errors(config)

    domains = {node["id"]: node for node in config["nodes"]}

    tag_paths: dict[str, str] = {}

    for i, item in enumerate(config["equipment"]):
        path = f"$.equipment[{i}]"

        if item["tag"] in tag_paths:
            errors.append(
                f"{path}.tag: duplicate tag {item['tag']!r}, "
                f"first used at {tag_paths[item['tag']]}",
            )
        else:
            tag_paths[item["tag"]] = path

        device_type = types.get(item["type"])

        if device_type is None:
            errors.append(
                f"{path}.type: {item['type']!r} has no device model yet, "
                f"only {sorted(types)}",
            )

        form = _wiring_form(item)

        if form == SUGAR_FORM:
            errors += _pair_errors(
                item["tag"],
                (item["node_in"], item["node_out"]),
                path,
                (f"{path}.node_in", f"{path}.node_out"),
                node_paths,
                domains,
            )
        elif form == NAMED_FORM:
            errors += _named_reference_errors(
                item,
                path,
                node_paths,
                domains,
                device_type,
            )
        else:
            errors.append(f"{path}: {_wiring_form_problem(item)}")

    return errors


def _wiring_form(item: Mapping[str, Any]) -> str | None:
    has_sugar = "node_in" in item and "node_out" in item
    has_named = "ports" in item

    if has_sugar and not has_named and "paths" not in item:
        return SUGAR_FORM

    if has_named and "node_in" not in item and "node_out" not in item:
        return NAMED_FORM

    return None


def _wiring_form_problem(item: Mapping[str, Any]) -> str:
    tag = item["tag"]

    if "ports" in item:
        return (
            f"{tag!r} mixes wiring forms — 'ports' cannot be combined with "
            f"'node_in' / 'node_out'"
        )

    if "paths" in item:
        return (
            f"{tag!r} declares 'paths' without 'ports' — paths name ports, "
            f"and node_in / node_out takes no paths"
        )

    present = [end for end in ("node_in", "node_out") if end in item]

    if present:
        return f"{tag!r} has only {present[0]!r} — a wired device needs both node_in and node_out"

    return f"{tag!r} declares no wiring — give node_in and node_out, or ports and paths"


def _named_reference_errors(
    item: Mapping[str, Any],
    path: str,
    node_paths: Mapping[str, str],
    domains: Mapping[str, Mapping[str, Any]],
    device_type: type[Equipment] | None,
) -> list[str]:
    ports = item["ports"]
    ports_path = f"{path}.ports"

    declarations, errors = _port_declarations(ports, ports_path)

    # Skipped for an unrecognised type: that is already reported, and there
    # is no class to check an opt-in against (ADR 0002, Amendment 3 C.7).
    if device_type is not None:
        errors += _configured_port_mode_errors(
            ports,
            ports_path,
            item["tag"],
            device_type,
        )

    for name, declaration in declarations.items():
        if declaration.node not in node_paths:
            errors.append(
                f"{path}.ports.{name}: unknown node {declaration.node!r}, "
                f"only {sorted(node_paths)}",
            )

    if "paths" not in item:
        errors.append(
            f"{path}: {item['tag']!r} declares 'ports' without 'paths' — "
            f"'paths' is required with 'ports', and [] says the device is in "
            f"no hydraulic solve",
        )

        return errors

    paths = item["paths"]

    if len(paths) > 1:
        errors.append(
            f"{path}.paths: {item['tag']!r} declares {len(paths)} paths, only "
            f"one is supported — characteristic(flow) is device-wide, so a "
            f"second path would publish the same curve, in the same flow "
            f"unit, into a second branch",
        )

        return errors

    if paths:
        pair = paths[0]
        where = f"{path}.paths[0]"

        for end in ("from", "to"):
            if pair[end] not in ports:
                errors.append(
                    f"{where}.{end}: {pair[end]!r} is not a key of "
                    f"{path}.ports, only {sorted(ports)}",
                )

        if pair["from"] in declarations and pair["to"] in declarations:
            errors += _pair_errors(
                item["tag"],
                (
                    declarations[pair["from"]].node,
                    declarations[pair["to"]].node,
                ),
                where,
                (f"{path}.ports.{pair['from']}", f"{path}.ports.{pair['to']}"),
                node_paths,
                domains,
                report_unknown=False,
            )

    return errors


def _configured_port_mode_errors(
    ports: Mapping[str, Any],
    ports_path: str,
    tag: str,
    device_type: type[Equipment],
) -> list[str]:
    """Whether a `ports` map is fixed or configured is decided from whether
    any entry carries `direction` (ADR 0002, Amendment 3 C.4). No entry
    carrying one is fixed-port mode, unchanged from before T5-7, and needs no
    error here. Once one does, every entry must, and the device's class must
    have opted in — a bare node-id string can never carry a direction, so it
    is always a "missing" entry in that case (C.5).
    """
    carries_direction = {
        name: isinstance(entry, dict) and "direction" in entry
        for name, entry in ports.items()
    }

    if not any(carries_direction.values()):
        return []

    if getattr(device_type, "accepts_configured_ports", False) is not True:
        return [
            f"{ports_path}.{name}.direction: {tag!r} has a fixed port set — "
            f"configuration cannot give it a direction"
            for name, carries in carries_direction.items()
            if carries
        ]

    errors = []

    for name, carries in carries_direction.items():
        if carries:
            continue

        if isinstance(ports[name], str):
            errors.append(
                f"{ports_path}.{name}: a node id string cannot declare a "
                f"direction — {tag!r} is in configured-port mode here, and "
                f"every port needs a typed entry with one",
            )
        else:
            errors.append(
                f"{ports_path}.{name}: missing direction — {tag!r} is in "
                f"configured-port mode here, and every port must declare one",
            )

    return errors


def _port_declarations(
    ports: Mapping[str, Any],
    path: str,
) -> tuple[dict[str, PortDeclaration], list[str]]:
    """Read a `ports` map into declarations, reporting every problem found.

    This is the string-or-object alternation, and it lives here rather than in
    the schema because the C3 validator implements no `oneOf`. Only entries
    that parsed appear in the result, so a caller must check membership before
    resolving a node reference.
    """
    declarations: dict[str, PortDeclaration] = {}
    errors: list[str] = []

    for name, entry in ports.items():
        where = f"{path}.{name}"

        if isinstance(entry, str):
            if not entry:
                errors.append(f"{where}: node id is empty")
                continue

            declarations[name] = PortDeclaration(node=entry)
            continue

        if not isinstance(entry, dict):
            errors.append(
                f"{where}: expected a node id string or a typed object "
                f"{{node, phase, purpose}}, got {_type_name(entry)}",
            )
            continue

        entry_errors = _typed_port_errors(entry, where)

        if entry_errors:
            errors += entry_errors
            continue

        declarations[name] = PortDeclaration(
            node=entry["node"],
            phase=entry["phase"],
            purpose=entry["purpose"],
            control=entry.get("control"),
            direction=entry.get("direction"),
            typed=True,
        )

    return declarations, errors


def _typed_port_errors(entry: Mapping[str, Any], where: str) -> list[str]:
    errors: list[str] = []

    for key in entry:
        if key not in TYPED_PORT_KEYS:
            errors.append(
                f"{where}: unexpected property {key!r}, "
                f"a typed port carries {list(TYPED_PORT_KEYS)}",
            )

    for key in REQUIRED_TYPED_PORT_KEYS:
        if key not in entry:
            errors.append(
                f"{where}: missing required property {key!r} — a typed port "
                f"declares {list(REQUIRED_TYPED_PORT_KEYS)}, and control only "
                f"where the connection has a control role",
            )

    if "node" in entry and not (isinstance(entry["node"], str) and entry["node"]):
        errors.append(
            f"{where}.node: expected a non-empty node id string, "
            f"got {_type_name(entry['node'])}",
        )

    if entry.get("phase") in RESERVED_PHASES:
        errors.append(
            f"{where}.phase: {entry['phase']!r} is reserved for a future "
            f"version — a stream carrying both phases has to split, and "
            f"splitting it is a flash calculation, which V1 does not model. "
            f"Declare one of {list(PORT_PHASES)}",
        )
    elif "phase" in entry and entry["phase"] not in PORT_PHASES:
        errors.append(
            f"{where}.phase: {entry['phase']!r} is not one of {list(PORT_PHASES)}",
        )

    if "purpose" in entry and entry["purpose"] not in PORT_PURPOSES:
        errors.append(
            f"{where}.purpose: {entry['purpose']!r} is not one of "
            f"{list(PORT_PURPOSES)}",
        )

    # Whether a direction here is permitted at all — the device must opt in,
    # and every sibling entry must carry one too — is a mode question, not an
    # entry-shape one, and is checked separately (ADR 0002, Amendment 3 C.4).
    if "direction" in entry and entry["direction"] not in PORT_DIRECTIONS:
        errors.append(
            f"{where}.direction: {entry['direction']!r} is not one of "
            f"{list(PORT_DIRECTIONS)}",
        )

    # Membership, not `.get()`: `control: none` must be refused rather than
    # read as an uncontrolled connection. Absence is how that is written.
    if "control" in entry and entry["control"] not in PORT_CONTROLS:
        errors.append(
            f"{where}.control: {entry['control']!r} is not one of "
            f"{list(PORT_CONTROLS)} — omit control entirely for a connection "
            f"under no control",
        )

    return errors


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, dict):
        return "object"

    if isinstance(value, list):
        return "array"

    if isinstance(value, str):
        return "string"

    if isinstance(value, (int, float)):
        return "number"

    if value is None:
        return "null"

    return type(value).__name__


def _pair_errors(
    tag: str,
    ends: tuple[str, str],
    path: str,
    end_paths: tuple[str, str],
    node_paths: Mapping[str, str],
    domains: Mapping[str, Mapping[str, Any]],
    report_unknown: bool = True,
) -> list[str]:
    errors: list[str] = []

    if report_unknown:
        for node_id, end_path in zip(ends, end_paths):
            if node_id not in node_paths:
                errors.append(
                    f"{end_path}: unknown node {node_id!r}, "
                    f"only {sorted(node_paths)}",
                )

    if ends[0] == ends[1]:
        errors.append(f"{path}: {tag!r} starts and ends at node {ends[0]!r}")

    if ends[0] in node_paths and ends[1] in node_paths:
        errors += _domain_agreement_errors(tag, ends, path, domains)

    return errors


def _domain_of(node: Mapping[str, Any]) -> str:
    return str(node.get("domain", DEFAULT_DOMAIN))


def _domain_names(config: Mapping[str, Any]) -> list[str]:
    # Order of first appearance in config["nodes"], which fixes the order of
    # Plant.topologies.
    return list(dict.fromkeys(_domain_of(node) for node in config["nodes"]))


def _boundary_errors(config: Mapping[str, Any]) -> list[str]:
    names = _domain_names(config)

    errors: list[str] = []

    for name in names:
        if any(
            node["boundary"] for node in config["nodes"] if _domain_of(node) == name
        ):
            continue

        if len(names) == 1:
            errors.append(
                "$.nodes: no boundary node — nothing anchors the pressure field",
            )
        else:
            errors.append(
                f"$.nodes: domain {name!r} has no boundary node — nothing "
                f"anchors its pressure field",
            )

    return errors


def _domain_agreement_errors(
    tag: str,
    node_ids: tuple[str, str],
    path: str,
    nodes: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    ends = [nodes[node_ids[0]], nodes[node_ids[1]]]

    if _domain_of(ends[0]) == _domain_of(ends[1]):
        return []

    described = []

    for node in ends:
        text = f"{node['id']!r} is in domain {_domain_of(node)!r}"

        if "domain" not in node:
            text += f" (the default, no domain was declared for {node['id']!r})"

        described.append(text)

    return [
        f"{path}: {tag!r} joins two flow domains, "
        f"{described[0]} but {described[1]} — a branch cannot cross domains",
    ]


def _build(
    config: Mapping[str, Any],
    types: Mapping[str, type[Equipment]],
) -> tuple[
    dict[str, Topology],
    dict[str, Node],
    dict[str, Equipment],
    dict[str, Wiring],
    dict[str, list[str]],
    list[str],
]:
    errors: list[str] = []

    nodes = {
        node["id"]: Node(
            id=node["id"],
            pressure=node["pressure"],
            is_boundary=node["boundary"],
        )
        for node in config["nodes"]
    }
    node_domains = {node["id"]: _domain_of(node) for node in config["nodes"]}

    topologies = {
        name: Topology(
            nodes=[nodes[node_id] for node_id, d in node_domains.items() if d == name],
        )
        for name in _domain_names(config)
    }

    devices: dict[str, Equipment] = {}
    # Per tag: the wiring form, the port declarations in config order, and the
    # paths as (from, to) port-name pairs — what to_config() needs to reproduce
    # the item it read, in the form it read it.
    wiring: dict[str, Wiring] = {}
    design_keys: dict[str, list[str]] = {}

    for i, item in enumerate(config["equipment"]):
        path = f"$.equipment[{i}]"

        device = types[item["type"]](item["tag"])

        design_errors = _apply_design(device, item["design"], f"{path}.design")

        errors += design_errors
        design_keys[item["tag"]] = list(item["design"])

        if _wiring_form(item) == SUGAR_FORM:
            built = _build_sugar(item, device, topologies, node_domains, path)
        else:
            built = _build_named(item, device, topologies, nodes, node_domains, path)

        if isinstance(built, list):
            errors += built
            continue

        devices[item["tag"]] = device
        wiring[item["tag"]] = built

    return topologies, nodes, devices, wiring, design_keys, errors


def _build_sugar(
    item: Mapping[str, Any],
    device: Equipment,
    topologies: Mapping[str, Topology],
    node_domains: Mapping[str, str],
    path: str,
) -> list[str] | Wiring:
    # Domain agreement was checked with the references, so both ends are in one
    # topology here.
    topology = topologies[node_domains[item["node_in"]]]

    try:
        branch = Branch(
            id=f"B-{item['tag']}",
            from_node=topology.node(item["node_in"]),
            to_node=topology.node(item["node_out"]),
            device=device,
        )

        topology.add_branch(branch)
    except ValueError as error:
        return [f"{path}: {error}"]

    names = [branch.from_port.name, branch.to_port.name]

    # Sugar is the untyped form by definition, so these carry attachment only.
    declarations = {
        names[0]: PortDeclaration(node=item["node_in"]),
        names[1]: PortDeclaration(node=item["node_out"]),
    }

    return (SUGAR_FORM, declarations, [(names[0], names[1])])


def _build_named(
    item: Mapping[str, Any],
    device: Equipment,
    topologies: Mapping[str, Topology],
    nodes: Mapping[str, Node],
    node_domains: Mapping[str, str],
    path: str,
) -> list[str] | Wiring:
    ports: Mapping[str, Any] = item["ports"]
    paths: list[Mapping[str, str]] = item["paths"]

    # Already validated in the reference pass, which raised before reaching
    # here, so the errors this returns are empty by construction.
    declarations, _ = _port_declarations(ports, f"{path}.ports")

    errors: list[str] = []

    # The reference pass already confirmed either every declaration carries a
    # direction and the device's class opted in, or none does (ADR 0002,
    # Amendment 3 C.4, C.7) — so this is exactly the mode, not a guess.
    configured = any(d.direction is not None for d in declarations.values())

    if configured:
        # The configured set replaces the device's own outright, in config
        # order, rather than being checked against it: a configured device
        # has no fixed shape for "has no port" or "not wired" to test.
        device.ports.clear()

        for name, declaration in declarations.items():
            assert declaration.direction is not None
            device.add_port(name, declaration.direction)
    else:
        for name in ports:
            if name not in device.ports:
                errors.append(
                    f"{path}.ports.{name}: {device.tag} has no port {name!r}, "
                    f"only {sorted(device.ports)}",
                )

        for name, port in device.ports.items():
            if name not in ports:
                errors.append(
                    f"{path}.ports: {device.tag} port {name!r} "
                    f"({port.direction}) is not wired to any node",
                )

        if errors:
            return errors

    # Semantic metadata reaches the runtime Port from configuration and from
    # nowhere else. A legacy string entry leaves the port untyped rather than
    # having a phase guessed for it.
    for name, declaration in declarations.items():
        if declaration.typed:
            device.port(name).declare(
                phase=declaration.phase,
                purpose=declaration.purpose,
                control=declaration.control,
            )

    if paths:
        pair = paths[0]
        where = f"{path}.paths[0]"

        if device.ports[pair["from"]].direction != INLET:
            errors.append(
                f"{where}.from: port {pair['from']!r} is an "
                f"{device.ports[pair['from']].direction}, a path must start at an inlet",
            )

        if device.ports[pair["to"]].direction != OUTLET:
            errors.append(
                f"{where}.to: port {pair['to']!r} is an "
                f"{device.ports[pair['to']].direction}, a path must end at an outlet",
            )

        if errors:
            return errors

        topology = topologies[node_domains[declarations[pair["from"]].node]]

        try:
            topology.add_branch(
                Branch(
                    id=f"B-{item['tag']}",
                    from_node=topology.node(declarations[pair["from"]].node),
                    to_node=topology.node(declarations[pair["to"]].node),
                    device=device,
                    from_port=pair["from"],
                    to_port=pair["to"],
                ),
            )
        except ValueError as error:
            return [f"{where}: {error}"]

    # A port no path claims is attached directly. With `paths: []` that is
    # every port, and this is the only place a coupling device is wired.
    claimed = {pair[end] for pair in paths for end in ("from", "to")}

    for name, declaration in declarations.items():
        if name not in claimed:
            device.port(name).connect(nodes[declaration.node])

    return (
        NAMED_FORM,
        declarations,
        [(pair["from"], pair["to"]) for pair in paths],
    )


def _attached_node_id(device: Equipment, port_name: str) -> str:
    node = device.port(port_name).node

    if node is None:
        raise ValueError(f"{device.tag} port {port_name!r} is not wired to any node")

    return node.id


def _apply_design(
    device: Equipment,
    design: Mapping[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []

    for key, value in design.items():
        if key in CAPABILITY_MARKERS:
            # Checked before hasattr(): the marker is read from the class,
            # never the instance, so a design value here would be accepted,
            # round-tripped and silently ignored rather than doing anything —
            # worse than a refusal (ADR 0002, Amendment 3 C6a). Every device
            # gets this specific reason, including one with no such
            # attribute at all, which would otherwise get the generic one
            # below.
            errors.append(
                f"{path}.{key}: {key!r} is an internal capability marker, "
                f"not a design parameter — {device.tag}'s port structure "
                f"cannot be set through design",
            )
            continue

        if key in STRUCTURAL_ATTRIBUTES:
            errors.append(
                f"{path}.{key}: {key!r} is structural device state, not a "
                f"design parameter — {device.tag}'s identity and wiring "
                f"are set by the plant config, not by design",
            )
            continue

        if key.startswith("_"):
            errors.append(
                f"{path}.{key}: {key!r} is a private attribute, not a "
                f"design parameter — {device.tag}'s internal state cannot "
                f"be set through design",
            )
            continue

        if not hasattr(device, key):
            errors.append(
                f"{path}.{key}: {device.tag} has no such attribute",
            )
            continue

        current = getattr(device, key)

        if not _same_kind(current, value):
            errors.append(
                f"{path}.{key}: expected {type(current).__name__}, "
                f"got {type(value).__name__} {value!r}",
            )
            continue

        # design has no per-key schema (C3 declares it only as {"type":
        # "object"}), so the validator cannot see a design number at all —
        # this is the only layer that can reject a non-finite one (D1).
        # isinstance rather than the isinstance-plus-bool-exclusion helper
        # above: _same_kind already confirmed value is numeric and not a
        # bool by this point.
        if isinstance(value, (int, float)) and not math.isfinite(value):
            errors.append(f"{path}.{key}: {value} is not a finite number")
            continue

        try:
            setattr(device, key, value)
        except AttributeError:
            errors.append(
                f"{path}.{key}: {device.tag}.{key} is derived and cannot be set",
            )
        except ValueError as error:
            # A device guarding its own range (Vessel.capacity, .level) says
            # why in its own words; the loader only adds the config path.
            errors.append(f"{path}.{key}: {error}")

    return errors


def _same_kind(current: Any, value: Any) -> bool:
    # Decoded JSON meets arbitrary device attributes, so this compares kinds
    # rather than exact types: an int in the file may set a float attribute.
    if isinstance(current, bool) or isinstance(value, bool):
        return isinstance(current, bool) and isinstance(value, bool)

    if isinstance(current, (int, float)):
        return isinstance(value, (int, float))

    return isinstance(value, type(current))


def _dangling_port_errors(devices: Mapping[str, Equipment]) -> list[str]:
    # Walks Plant.devices, not Topology.unconnected_ports(), which cannot see a
    # coupling device that sits in no topology.
    return [
        f"device {tag}: port {port.name!r} ({port.direction}) is not "
        f"wired to any node"
        for tag, device in devices.items()
        for port in device.ports.values()
        if not port.connected
    ]


def _solvability_errors(topologies: Mapping[str, Topology]) -> list[str]:
    errors: list[str] = []

    for name, topology in topologies.items():
        components = _components(topology)

        if len(components) > 1:
            described = "; ".join(
                "{" + ", ".join(component) + "}" for component in components
            )
            where = "the graph" if len(topologies) == 1 else f"domain {name!r}"

            errors.append(
                f"$.nodes: {where} is not one connected piece, "
                f"{len(components)} disconnected subgraphs: {described}",
            )

    return errors


def _components(topology: Topology) -> list[list[str]]:
    seen: set[str] = set()
    components: list[list[str]] = []

    for start in topology.nodes:
        if start in seen:
            continue

        component: list[str] = []
        pending = [start]
        seen.add(start)

        while pending:
            node_id = pending.pop()
            component.append(node_id)

            for branch in topology.branches_at(node_id):
                for neighbour in branch.nodes:
                    if neighbour.id not in seen:
                        seen.add(neighbour.id)
                        pending.append(neighbour.id)

        components.append(sorted(component))

    return components
