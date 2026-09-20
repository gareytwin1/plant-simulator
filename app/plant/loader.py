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

Plant files may be JSON or YAML (`.json`, `.yaml`, `.yml`). The format is
resolved before validation; after that there is one path.

Only `nodes` and `equipment` build anything. `limits`, `controllers` and
`interlocks` are carried through untouched for the subsystems that will own
them, so a plant round-trips back to the config it came from.
"""

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from app.equipment.base import INLET, OUTLET, Equipment
from app.equipment.compressor import GasCompressor
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
}


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
    emitted with them, a device loaded with ports/paths is emitted with those.
    """

    def __init__(
        self,
        topologies: Mapping[str, Topology],
        nodes: Mapping[str, Node],
        declared_domains: Mapping[str, str],
        devices: Mapping[str, Equipment],
        forms: Mapping[str, str],
        port_order: Mapping[str, list[str]],
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
        self._port_order = {tag: list(names) for tag, names in port_order.items()}
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
                name: _attached_node_id(device, name) for name in self._port_order[tag]
            }
            item["paths"] = [
                {"from": from_port, "to": to_port}
                for from_port, to_port in self._paths[tag]
            ]

        item["design"] = {key: getattr(device, key) for key in self._design_keys[tag]}

        return item

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
        port_order={tag: order for tag, (_, order, _) in wiring.items()},
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

        if node["boundary"] and node["pressure"] <= 0.0:
            errors.append(
                f"{path}.pressure: boundary node {node['id']!r} holds "
                f"{node['pressure']} psia — a boundary needs a positive "
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

        if item["type"] not in types:
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
            errors += _named_reference_errors(item, path, node_paths, domains)
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
) -> list[str]:
    errors: list[str] = []

    ports = item["ports"]

    for name, node_id in ports.items():
        if node_id not in node_paths:
            errors.append(
                f"{path}.ports.{name}: unknown node {node_id!r}, "
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

        if pair["from"] in ports and pair["to"] in ports:
            errors += _pair_errors(
                item["tag"],
                (ports[pair["from"]], ports[pair["to"]]),
                where,
                (f"{path}.ports.{pair['from']}", f"{path}.ports.{pair['to']}"),
                node_paths,
                domains,
                report_unknown=False,
            )

    return errors


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
    dict[str, tuple[str, list[str], list[tuple[str, str]]]],
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
    # Per tag: the wiring form, the port names in config order, and the paths
    # as (from, to) port-name pairs — what to_config() needs to reproduce it.
    wiring: dict[str, tuple[str, list[str], list[tuple[str, str]]]] = {}
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
) -> list[str] | tuple[str, list[str], list[tuple[str, str]]]:
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

    return (SUGAR_FORM, names, [(names[0], names[1])])


def _build_named(
    item: Mapping[str, Any],
    device: Equipment,
    topologies: Mapping[str, Topology],
    nodes: Mapping[str, Node],
    node_domains: Mapping[str, str],
    path: str,
) -> list[str] | tuple[str, list[str], list[tuple[str, str]]]:
    ports: Mapping[str, str] = item["ports"]
    paths: list[Mapping[str, str]] = item["paths"]

    errors: list[str] = []

    for name in ports:
        if name not in device.ports:
            errors.append(
                f"{path}.ports.{name}: {device.tag} has no port {name!r}, "
                f"only {sorted(device.ports)}",
            )

    for name, port in device.ports.items():
        if name not in ports:
            errors.append(
                f"{path}.ports: {device.tag} port {name!r} ({port.direction}) "
                f"is not wired to any node",
            )

    if errors:
        return errors

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

        topology = topologies[node_domains[ports[pair["from"]]]]

        try:
            topology.add_branch(
                Branch(
                    id=f"B-{item['tag']}",
                    from_node=topology.node(ports[pair["from"]]),
                    to_node=topology.node(ports[pair["to"]]),
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

    for name, node_id in ports.items():
        if name not in claimed:
            device.port(name).connect(nodes[node_id])

    return (
        NAMED_FORM,
        list(ports),
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
