"""Minimal JSON-Schema subset validator for structured LLM outputs.

Covers exactly what our prompts emit: object/array/string/number/integer/
boolean, required, properties, items, enum. Anything outside the subset is
ignored (never rejected) — but we never CLAIM full JSON-Schema compliance:
violations inside the covered subset raise, so a passing result is genuinely
checked, not merely parsed.
"""

from typing import Any


def validate_against_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    """Return a list of violation descriptions (empty = valid)."""
    return _check(data, schema, path="$")


def _check(data: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not isinstance(schema, dict):
        return []
    violations: list[str] = []
    expected = schema.get("type")
    if expected is not None and not _type_matches(data, expected):
        return [f"{path}: expected type {expected!r}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        violations.append(f"{path}: value not in enum {schema['enum']!r}")
    if expected == "object" and isinstance(data, dict):
        for key in schema.get("required", []) or []:
            if key not in data:
                violations.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties", {}) or {}
        for key, subschema in props.items():
            if key in data and isinstance(subschema, dict):
                violations.extend(_check(data[key], subschema, f"{path}.{key}"))
    if expected == "array" and isinstance(data, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(data):
                violations.extend(_check(item, items, f"{path}[{i}]"))
    return violations


def _type_matches(data: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(data, dict)
    if expected == "array":
        return isinstance(data, list)
    if expected == "string":
        return isinstance(data, str)
    if expected == "boolean":
        return isinstance(data, bool)
    if expected == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if expected == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if expected == "null":
        return data is None
    # Unknown type keyword: do not reject (outside the covered subset).
    return True
