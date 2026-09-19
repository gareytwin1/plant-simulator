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
every device port is wired, there is at least one boundary and each boundary
holds a positive absolute pressure, and the graph is one connected piece.
Whether the solver then *converges* is the solver's business (T4-2).

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
    """A loaded plant: its topology, plus the config that produced it.

    `to_config()` reads design values back off the live devices, so what it
    returns is the plant as it stands, not a stale copy of the file. For a
    freshly loaded plant that is the same config, which is the round-trip the
    loader promises.
    """

    def __init__(
        self,
        topology: Topology,
        design_keys: Mapping[str, list[str]],
        equipment_types: Mapping[str, str],
        passthrough: Mapping[str, Any],
    ) -> None:
        self.topology = topology

        self._design_keys = {tag: list(keys) for tag, keys in design_keys.items()}
        self._equipment_types = dict(equipment_types)
        # Decoded JSON of sections no subsystem interprets yet.
        self._passthrough: dict[str, Any] = copy.deepcopy(dict(passthrough))

    def to_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "nodes": [
                {
                    "id": node.id,
                    "boundary": node.is_boundary,
                    "pressure": node.pressure,
                }
                for node in self.topology.nodes.values()
            ],
            "equipment": [
                {
                    "tag": branch.device.tag,
                    "type": self._equipment_types[branch.device.tag],
                    "node_in": branch.from_node.id,
                    "node_out": branch.to_node.id,
                    "design": {
                        key: getattr(branch.device, key)
                        for key in self._design_keys[branch.device.tag]
                    },
                }
                for branch in self.topology.branches.values()
            ],
        }

        for section in PASSTHROUGH_SECTIONS:
            if section in self._passthrough:
                config[section] = copy.deepcopy(self._passthrough[section])

        return config


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

    topology, design_keys, errors = _build(config, types)

    errors += _solvability_errors(topology)

    if errors:
        raise PlantConfigError(errors)

    return Plant(
        topology=topology,
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

            return yaml.safe_load(f)
        except (json.JSONDecodeError, yaml.YAMLError) as error:
            raise PlantConfigError(
                [f"{path}: not parseable as {suffix[1:].upper()}: {error}"],
            ) from error


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

    if not any(node["boundary"] for node in config["nodes"]):
        errors.append(
            "$.nodes: no boundary node — nothing anchors the pressure field",
        )

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

    return errors


def _build(
    config: Mapping[str, Any],
    types: Mapping[str, type[Equipment]],
) -> tuple[Topology, dict[str, list[str]], list[str]]:
    errors: list[str] = []

    topology = Topology(
        nodes=[
            Node(
                id=node["id"],
                pressure=node["pressure"],
                is_boundary=node["boundary"],
            )
            for node in config["nodes"]
        ],
    )

    design_keys: dict[str, list[str]] = {}

    for i, item in enumerate(config["equipment"]):
        path = f"$.equipment[{i}]"

        device = types[item["type"]](item["tag"])

        design_errors = _apply_design(device, item["design"], f"{path}.design")

        errors += design_errors
        design_keys[item["tag"]] = list(item["design"])

        try:
            topology.add_branch(
                Branch(
                    id=f"B-{item['tag']}",
                    from_node=topology.node(item["node_in"]),
                    to_node=topology.node(item["node_out"]),
                    device=device,
                ),
            )
        except ValueError as error:
            errors.append(f"{path}: {error}")

    return topology, design_keys, errors


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


def _solvability_errors(topology: Topology) -> list[str]:
    errors: list[str] = []

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

        errors.append(
            f"$.nodes: the graph is not one connected piece, "
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
