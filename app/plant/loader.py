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

from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.plant.topology import Branch, Node, Topology
from app.plant.validate import validate


DEFAULT_DOMAIN = "default"

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
    loader promises.

    `nodes` is every node in config order, across all domains; `topologies`
    holds one Topology per flow domain in the order each first appears.
    """

    def __init__(
        self,
        topologies: Mapping[str, Topology],
        nodes: Mapping[str, Node],
        declared_domains: Mapping[str, str],
        branches: Mapping[str, Branch],
        design_keys: Mapping[str, list[str]],
        equipment_types: Mapping[str, str],
        passthrough: Mapping[str, Any],
    ) -> None:
        self.topologies: dict[str, Topology] = dict(topologies)
        self.nodes: dict[str, Node] = dict(nodes)

        self._declared_domains = dict(declared_domains)
        self._branches = dict(branches)
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
                {
                    "tag": tag,
                    "type": self._equipment_types[tag],
                    "node_in": branch.from_node.id,
                    "node_out": branch.to_node.id,
                    "design": {
                        key: getattr(branch.device, key)
                        for key in self._design_keys[tag]
                    },
                }
                for tag, branch in self._branches.items()
            ],
        }

        for section in PASSTHROUGH_SECTIONS:
            if section in self._passthrough:
                config[section] = copy.deepcopy(self._passthrough[section])

        return config

    def _node_config(self, node: Node) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": node.id,
            "boundary": node.is_boundary,
            "pressure": node.pressure,
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

    topologies, nodes, branches, design_keys, errors = _build(config, types)

    errors += _solvability_errors(topologies)

    if errors:
        raise PlantConfigError(errors)

    return Plant(
        topologies=topologies,
        nodes=nodes,
        declared_domains={
            node["id"]: node["domain"] for node in config["nodes"] if "domain" in node
        },
        branches=branches,
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

        for end in ("node_in", "node_out"):
            if item[end] not in node_paths:
                errors.append(
                    f"{path}.{end}: unknown node {item[end]!r}, "
                    f"only {sorted(node_paths)}",
                )

        if item["node_in"] == item["node_out"]:
            errors.append(
                f"{path}: {item['tag']!r} starts and ends at node "
                f"{item['node_in']!r}",
            )

        if item["node_in"] in node_paths and item["node_out"] in node_paths:
            errors += _domain_agreement_errors(item, path, domains)

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
    item: Mapping[str, Any],
    path: str,
    nodes: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    ends = [nodes[item["node_in"]], nodes[item["node_out"]]]

    if _domain_of(ends[0]) == _domain_of(ends[1]):
        return []

    described = []

    for node in ends:
        text = f"{node['id']!r} is in domain {_domain_of(node)!r}"

        if "domain" not in node:
            text += f" (the default, no domain was declared for {node['id']!r})"

        described.append(text)

    return [
        f"{path}: {item['tag']!r} joins two flow domains, "
        f"{described[0]} but {described[1]} — a branch cannot cross domains",
    ]


def _build(
    config: Mapping[str, Any],
    types: Mapping[str, type[Equipment]],
) -> tuple[
    dict[str, Topology],
    dict[str, Node],
    dict[str, Branch],
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

    branches: dict[str, Branch] = {}
    design_keys: dict[str, list[str]] = {}

    for i, item in enumerate(config["equipment"]):
        path = f"$.equipment[{i}]"

        device = types[item["type"]](item["tag"])

        design_errors = _apply_design(device, item["design"], f"{path}.design")

        errors += design_errors
        design_keys[item["tag"]] = list(item["design"])

        # Domain agreement was checked with the references, so both ends are
        # in one topology here.
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
            errors.append(f"{path}: {error}")
        else:
            branches[item["tag"]] = branch

    return topologies, nodes, branches, design_keys, errors


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

    return errors


def _same_kind(current: Any, value: Any) -> bool:
    # Decoded JSON meets arbitrary device attributes, so this compares kinds
    # rather than exact types: an int in the file may set a float attribute.
    if isinstance(current, bool) or isinstance(value, bool):
        return isinstance(current, bool) and isinstance(value, bool)

    if isinstance(current, (int, float)):
        return isinstance(value, (int, float))

    return isinstance(value, type(current))


def _solvability_errors(topologies: Mapping[str, Topology]) -> list[str]:
    errors: list[str] = []

    for name, topology in topologies.items():
        for tag, port in topology.unconnected_ports():
            errors.append(
                f"device {tag}: port {port.name!r} ({port.direction}) is not "
                f"wired to any node",
            )

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
