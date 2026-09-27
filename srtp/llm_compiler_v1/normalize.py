"""Lossless, schema-guided repairs of model-authored IR data.

Only changes with exactly one meaning are made, and each is logged:
integer/number/boolean text to its value, an integral float to an integer, a
single value where an array is required, an enum value that differs only in
case or surrounding spaces (unique match), and a bare number, boolean, null or
``rule:`` identifier where an expression is required (a literal expression).
Anything ambiguous (alternatives without a discriminator, other strings in
expression positions) is left for the validators to report.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

_INTEGER = re.compile(r"^-?\d+$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_RULE_ID = re.compile(r"^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


@lru_cache(maxsize=4)
def authoring_schema_for(slot: str) -> Dict[str, Any]:
    from srtp.ir_contracts import IR_SCHEMA_FILES
    from .program_builder import authoring_schema
    path = Path(__file__).resolve().parents[1] / IR_SCHEMA_FILES[slot]
    return authoring_schema(json.loads(path.read_text(encoding="utf-8")))


def normalize_definition(slot: str, definition: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Normalize root fields of a staged definition."""
    schema = authoring_schema_for(slot)
    log: List[str] = []
    result = {}
    for key, value in definition.items():
        child = schema.get("properties", {}).get(key)
        result[key] = normalize(value, child, schema, "/" + key, log) if child else deepcopy(value)
    return result, log


def normalize_operation_value(slot: str, path: str, value: Any, log: List[str], pointer: str = "") -> Any:
    """Normalize an RFC 6902 value at /field, /field/N or /field/- of one IR."""
    schema = authoring_schema_for(slot)
    parts = [part for part in path.split("/") if part]
    child = schema.get("properties", {}).get(parts[0]) if parts else None
    if child is None or len(parts) > 2:
        return value
    if len(parts) == 2:
        resolved, _ = _resolve(child, schema)
        if resolved.get("type") != "array" or not (parts[1] == "-" or parts[1].isdigit()):
            return value
        child = resolved.get("items") or {}
    return normalize(value, child, schema, pointer or path, log)


def normalize(value: Any, schema: Optional[Mapping[str, Any]], root: Mapping[str, Any], path: str, log: List[str]) -> Any:
    if not isinstance(schema, Mapping):
        return deepcopy(value)
    schema, ref = _resolve(schema, root)
    if ref == "expression" and not isinstance(value, (dict, list)) and (
            value is None or isinstance(value, (bool, int, float)) or (isinstance(value, str) and _RULE_ID.match(value))):
        log.append("{0}: {1!r} -> literal expression".format(path, value))
        return {"op": "literal", "value": value}
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            choice = _discriminated(value, schema[keyword], root)
            return normalize(value, choice, root, path, log) if choice is not None else deepcopy(value)
    kind = schema.get("type")
    if isinstance(kind, list):
        return deepcopy(value)
    if kind == "integer":
        if isinstance(value, str) and _INTEGER.match(value.strip()):
            log.append("{0}: {1!r} -> {2}".format(path, value, int(value.strip())))
            return int(value.strip())
        if isinstance(value, float) and value.is_integer():
            log.append("{0}: {1!r} -> {2}".format(path, value, int(value)))
            return int(value)
    elif kind == "number":
        if isinstance(value, str) and _NUMBER.match(value.strip()):
            number = float(value.strip()) if "." in value else int(value.strip())
            log.append("{0}: {1!r} -> {2}".format(path, value, number))
            return number
    elif kind == "boolean":
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            log.append("{0}: {1!r} -> {2}".format(path, value, value.strip().lower() == "true"))
            return value.strip().lower() == "true"
    elif kind == "array":
        if not isinstance(value, list) and value is not None:
            log.append("{0}: single value -> [value]".format(path))
            value = [value]
        if isinstance(value, list):
            items = schema.get("items")
            return [normalize(item, items, root, "{0}/{1}".format(path, index), log) for index, item in enumerate(value)]
    elif (kind == "object" or (kind is None and "properties" in schema)) and isinstance(value, dict):
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        result = {}
        for key, item in value.items():
            child = properties.get(key, extra if isinstance(extra, Mapping) else None)
            result[key] = normalize(item, child, root, "{0}/{1}".format(path, key), log) if child else deepcopy(item)
        return result
    if "enum" in schema and isinstance(value, str) and value not in schema["enum"]:
        matches = [item for item in schema["enum"] if isinstance(item, str) and item.lower() == value.strip().lower()]
        if len(matches) == 1:
            log.append("{0}: {1!r} -> {2!r}".format(path, value, matches[0]))
            return matches[0]
    return deepcopy(value)


def _resolve(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Tuple[Mapping[str, Any], Optional[str]]:
    ref_name = None
    seen = 0
    while isinstance(schema, Mapping) and "$ref" in schema and seen < 16:
        ref = str(schema["$ref"])
        ref_name = ref.rsplit("/", 1)[-1]
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, Mapping) else {}
        schema, seen = target, seen + 1
    return schema, ref_name


def _discriminated(value: Any, choices: List[Mapping[str, Any]], root: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """The one alternative whose const property matches the value (e.g. op)."""
    if not isinstance(value, Mapping):
        return None
    matched = []
    for choice in choices:
        resolved, _ = _resolve(choice, root)
        properties = resolved.get("properties", {}) if isinstance(resolved, Mapping) else {}
        if any(isinstance(p, Mapping) and "const" in p and value.get(key) == p["const"] for key, p in properties.items()):
            matched.append(resolved)
    return matched[0] if len(matched) == 1 else None


def _decode(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def prune_satisfied_removes(document: Mapping[str, Any], operations: Any) -> Tuple[Any, List[str]]:
    """Drop ``remove`` operations whose OBJECT KEY is already absent when they run.

    The requested end state already holds (e.g. the engine migrated a renamed
    field the reply also removes). Array indexes are never pruned: a missing
    index is a real mistake. Operations are simulated in order on a copy; if
    the simulation cannot follow an operation, pruning stops there and the
    rest is left to the real patch applier.
    """
    if not isinstance(operations, list):
        return operations, []
    working: Any = deepcopy(dict(document))
    kept: List[Any] = []
    notes: List[str] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping) or not isinstance(operation.get("path"), str):
            return kept + operations[index:], notes
        tokens = [_decode(part) for part in operation["path"].split("/")[1:]]
        parent: Any = working
        try:
            for token in tokens[:-1]:
                parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        except (KeyError, IndexError, ValueError, TypeError):
            return kept + operations[index:], notes
        key = tokens[-1] if tokens else None
        op = operation.get("op")
        if op == "remove" and isinstance(parent, dict) and key not in parent:
            notes.append("dropped remove of absent key {0}".format(operation["path"]))
            continue
        kept.append(operation)
        try:
            if key is None:
                return kept + operations[index + 1:], notes
            if op == "remove":
                del parent[int(key) if isinstance(parent, list) else key]
            elif op in ("add", "replace"):
                if isinstance(parent, list):
                    position = len(parent) if key == "-" else int(key)
                    if op == "add":
                        parent.insert(position, deepcopy(operation.get("value")))
                    else:
                        parent[position] = deepcopy(operation.get("value"))
                else:
                    parent[key] = deepcopy(operation.get("value"))
            elif op != "test":
                return kept + operations[index + 1:], notes  # move/copy: stop simulating
        except (KeyError, IndexError, ValueError, TypeError):
            return kept + operations[index + 1:], notes
    return kept, notes
