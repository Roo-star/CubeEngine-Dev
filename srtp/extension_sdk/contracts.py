"""Small deterministic JSON contract subset used at the Extension RPC boundary."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence, Set


class ContractSchemaError(ValueError):
    pass


class ContractValidationError(ValueError):
    pass


_KEYS = {
    "type", "required", "properties", "additionalProperties", "items",
    "enum", "const", "minimum", "maximum", "minItems", "maxItems",
    "minLength", "maxLength", "pattern", "oneOf", "anyOf",
}
_TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}


def validate_contract_schema(schema: Mapping[str, Any], path: str = "$schema") -> None:
    if not isinstance(schema, Mapping):
        raise ContractSchemaError("{0} must be an object".format(path))
    unknown = set(schema) - _KEYS
    if unknown:
        raise ContractSchemaError("{0} uses unsupported schema keyword: {1}".format(path, sorted(unknown)[0]))
    type_value = schema.get("type")
    if type_value is not None:
        types = [type_value] if isinstance(type_value, str) else type_value
        if not isinstance(types, list) or not types or any(item not in _TYPES for item in types):
            raise ContractSchemaError("{0}.type is unsupported".format(path))
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or len(required) != len(set(required)) or any(not isinstance(item, str) for item in required):
            raise ContractSchemaError("{0}.required must contain unique strings".format(path))
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping) or any(not isinstance(key, str) for key in properties):
            raise ContractSchemaError("{0}.properties must be an object".format(path))
        for key, child in properties.items():
            validate_contract_schema(child, "{0}.properties.{1}".format(path, key))
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        raise ContractSchemaError("{0}.additionalProperties must be boolean".format(path))
    if "items" in schema:
        validate_contract_schema(schema["items"], path + ".items")
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            options = schema[keyword]
            if not isinstance(options, list) or not options:
                raise ContractSchemaError("{0}.{1} must be a non-empty array".format(path, keyword))
            for index, child in enumerate(options):
                validate_contract_schema(child, "{0}.{1}[{2}]".format(path, keyword, index))
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ContractSchemaError("{0}.enum must be a non-empty array".format(path))
    for keyword in ("minimum", "maximum"):
        if keyword in schema and (isinstance(schema[keyword], bool) or not isinstance(schema[keyword], (int, float)) or not math.isfinite(schema[keyword])):
            raise ContractSchemaError("{0}.{1} must be finite numeric".format(path, keyword))
    for keyword in ("minItems", "maxItems", "minLength", "maxLength"):
        if keyword in schema and (isinstance(schema[keyword], bool) or not isinstance(schema[keyword], int) or schema[keyword] < 0):
            raise ContractSchemaError("{0}.{1} must be a non-negative integer".format(path, keyword))
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise ContractSchemaError("{0}.pattern must be a string".format(path))
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise ContractSchemaError("{0}.pattern is invalid".format(path)) from exc


def validate_json_contract(value: Any, schema: Mapping[str, Any], path: str = "$value") -> None:
    validate_contract_schema(schema)
    _validate(value, schema, path)


def _validate(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if "oneOf" in schema:
        matches = _matching_options(value, schema["oneOf"], path)
        if matches != 1:
            raise ContractValidationError("{0} must match exactly one schema option".format(path))
    if "anyOf" in schema and _matching_options(value, schema["anyOf"], path) == 0:
        raise ContractValidationError("{0} must match at least one schema option".format(path))
    if "type" in schema:
        types = (schema["type"],) if isinstance(schema["type"], str) else tuple(schema["type"])
        if not any(_is_type(value, item) for item in types):
            raise ContractValidationError("{0} has the wrong JSON type".format(path))
    if "const" in schema and value != schema["const"]:
        raise ContractValidationError("{0} does not equal the required constant".format(path))
    if "enum" in schema and not any(value == option for option in schema["enum"]):
        raise ContractValidationError("{0} is outside the declared enum".format(path))
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractValidationError("{0} must be finite".format(path))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractValidationError("{0} is below minimum".format(path))
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractValidationError("{0} is above maximum".format(path))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ContractValidationError("{0} is shorter than minLength".format(path))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractValidationError("{0} is longer than maxLength".format(path))
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ContractValidationError("{0} does not match pattern".format(path))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ContractValidationError("{0} has too few items".format(path))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ContractValidationError("{0} has too many items".format(path))
        if "items" in schema:
            for index, child in enumerate(value):
                _validate(child, schema["items"], "{0}[{1}]".format(path, index))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractValidationError("{0} contains a non-string object key".format(path))
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ContractValidationError("{0}.{1} is required".format(path, key))
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ContractValidationError("{0} contains unsupported field {1}".format(path, sorted(extras)[0]))
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], "{0}.{1}".format(path, key))


def _matching_options(value: Any, options: Sequence[Mapping[str, Any]], path: str) -> int:
    matches = 0
    for option in options:
        try:
            _validate(value, option, path)
        except ContractValidationError:
            continue
        matches += 1
    return matches


def _is_type(value: Any, kind: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, Mapping),
    }[kind]
