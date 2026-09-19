"""
Validator for contract C3 (config/schema/plant.schema.json).

Walks the schema generically rather than hard-coding plant fields, so a
schema-only change (new equipment type, new limit field) never requires a
validator change. Supports the subset of JSON Schema the plant config
actually uses: type, enum, properties, required, additionalProperties,
items, minItems, minLength, minimum.

Both the schema and the config it checks are decoded JSON of a shape
nothing knows until it is walked — discovering that shape is the whole
job — so they are typed as Any rather than pretending otherwise. The
errors list is the typed part, because that is what callers read.
"""

import json
from pathlib import Path
from typing import Any


Schema = dict[str, Any]

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "schema" / "plant.schema.json"


def load_schema() -> Schema:
    with open(SCHEMA_PATH) as f:
        schema: Schema = json.load(f)

    return schema


def validate(config: Any, schema: Schema | None = None) -> list[str]:
    """Return a list of error strings, each naming the offending path. Empty means valid."""
    if schema is None:
        schema = load_schema()

    errors: list[str] = []
    _check(config, schema, "$", errors)
    return errors


def _check(value: Any, schema: Schema, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")

    if expected_type and not _type_matches(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {_type_name(value)}")
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
        return

    if expected_type == "object":
        _check_object(value, schema, path, errors)
    elif expected_type == "array":
        _check_array(value, schema, path, errors)
    elif expected_type == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: length {len(value)} is below the minimum of {schema['minLength']}")
    elif expected_type == "number":
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below the minimum of {schema['minimum']}")


def _check_object(value: Any, schema: Schema, path: str, errors: list[str]) -> None:
    for key in schema.get("required", []):
        if key not in value:
            errors.append(f"{path}: missing required property {key!r}")

    properties = schema.get("properties", {})

    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                errors.append(f"{path}: unexpected property {key!r}")

    for key, subschema in properties.items():
        if key in value:
            _check(value[key], subschema, f"{path}.{key}", errors)


def _check_array(value: Any, schema: Schema, path: str, errors: list[str]) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{path}: has {len(value)} items, fewer than the minimum of {schema['minItems']}")

    items_schema = schema.get("items")
    if items_schema:
        for i, item in enumerate(value):
            _check(item, items_schema, f"{path}[{i}]", errors)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return True


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
