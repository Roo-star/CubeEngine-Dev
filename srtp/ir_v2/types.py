"""Runtime value types for CubeEngine Rule IR v2.

Rule-critical values remain JSON-compatible and deterministic.  Fixed-point
values are represented by integer scaled units; the custom fixed type declares
the scale that gives those units meaning.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple


BUILTIN_TYPES = {
    "core:any", "core:bool", "core:int", "core:fixed", "core:string",
    "core:coord", "core:entity_id", "core:participant_id", "core:action_id",
}


class RuleTypeError(ValueError):
    pass


class RuleTypeRegistry:
    """Validate declared types and every value crossing the runtime boundary."""

    def __init__(self, definitions: Sequence[Mapping[str, Any]] = ()) -> None:
        self.definitions: Dict[str, Mapping[str, Any]] = {}
        for definition in definitions:
            if not isinstance(definition, Mapping) or not isinstance(definition.get("id"), str):
                raise RuleTypeError("type definition requires an ID")
            identifier = str(definition["id"])
            if identifier in self.definitions or identifier in BUILTIN_TYPES:
                raise RuleTypeError("duplicate type definition: {0}".format(identifier))
            self.definitions[identifier] = definition
        self._validate_definitions()
        self._ensure_acyclic_definitions()

    def validate(self, value: Any, type_ref: str, path: str = "$") -> None:
        self._validate(value, str(type_ref), path, set())

    def _validate(self, value: Any, type_ref: str, path: str, stack: Set[str]) -> None:
        if type_ref in BUILTIN_TYPES:
            self._validate_builtin(value, type_ref, path)
            return
        if type_ref not in self.definitions:
            raise RuleTypeError("{0}: unknown type {1}".format(path, type_ref))
        if type_ref in stack:
            raise RuleTypeError("{0}: recursive value type {1} is not supported in alpha 1".format(path, type_ref))
        definition = self.definitions[type_ref]
        kind = definition.get("kind")
        nested = set(stack)
        nested.add(type_ref)

        if kind == "enum":
            values = definition.get("values", {})
            if not any(_same_scalar(value, candidate) for candidate in values.values()):
                raise RuleTypeError("{0}: value is outside enum {1}".format(path, type_ref))
            return
        if kind == "record":
            if not isinstance(value, Mapping):
                raise RuleTypeError("{0}: expected record {1}".format(path, type_ref))
            fields = definition.get("fields", [])
            known = {str(field["name"]) for field in fields}
            if definition.get("additional_fields", False) is not True:
                extra = set(value) - known
                if extra:
                    raise RuleTypeError("{0}: unknown record field {1}".format(path, sorted(extra)[0]))
            for field in fields:
                name = str(field["name"])
                if name not in value:
                    if field.get("required", True):
                        raise RuleTypeError("{0}: missing record field {1}".format(path, name))
                    continue
                self._validate(value[name], str(field["type"]), path + "." + name, nested)
            return
        if kind in ("list", "set"):
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise RuleTypeError("{0}: expected {1}".format(path, kind))
            minimum = int(definition.get("min_items", 0))
            maximum = definition.get("max_items")
            if len(value) < minimum or (maximum is not None and len(value) > int(maximum)):
                raise RuleTypeError("{0}: {1} length is outside declared bounds".format(path, kind))
            if kind == "set":
                keys = [_canonical_key(item) for item in value]
                if len(keys) != len(set(keys)):
                    raise RuleTypeError("{0}: set values must be unique".format(path))
            for index, item in enumerate(value):
                self._validate(item, str(definition["element_type"]), "{0}[{1}]".format(path, index), nested)
            return
        if kind == "map":
            if not isinstance(value, Mapping):
                raise RuleTypeError("{0}: expected map".format(path))
            minimum = int(definition.get("min_items", 0))
            maximum = definition.get("max_items")
            if len(value) < minimum or (maximum is not None and len(value) > int(maximum)):
                raise RuleTypeError("{0}: map size is outside declared bounds".format(path))
            for key, item in value.items():
                self._validate(key, str(definition["key_type"]), path + ".<key>", nested)
                self._validate(item, str(definition["value_type"]), path + "." + str(key), nested)
            return
        if kind == "optional":
            if value is not None:
                self._validate(value, str(definition["item_type"]), path, nested)
            return
        if kind == "fixed":
            _require_integer(value, path, "fixed-point scaled units")
            minimum = definition.get("min_scaled")
            maximum = definition.get("max_scaled")
            if minimum is not None and value < minimum:
                raise RuleTypeError("{0}: fixed-point value is below min_scaled".format(path))
            if maximum is not None and value > maximum:
                raise RuleTypeError("{0}: fixed-point value is above max_scaled".format(path))
            return
        raise RuleTypeError("{0}: unsupported type kind {1}".format(path, kind))

    def _validate_builtin(self, value: Any, type_ref: str, path: str) -> None:
        if type_ref == "core:any":
            _validate_json_value(value, path)
        elif type_ref == "core:bool":
            if not isinstance(value, bool):
                raise RuleTypeError("{0}: expected bool".format(path))
        elif type_ref in ("core:int", "core:fixed"):
            _require_integer(value, path, type_ref)
        elif type_ref == "core:string":
            if not isinstance(value, str):
                raise RuleTypeError("{0}: expected string".format(path))
        elif type_ref == "core:coord":
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
                raise RuleTypeError("{0}: expected a non-empty coordinate".format(path))
            for index, item in enumerate(value):
                _require_integer(item, "{0}[{1}]".format(path, index), "coordinate component")
        elif type_ref == "core:participant_id":
            _require_id(value, path, "rule:participant.")
        elif type_ref == "core:entity_id":
            _require_id(value, path, "entity:")
        elif type_ref == "core:action_id":
            _require_id(value, path, "rule:action.")

    def _validate_definitions(self) -> None:
        declared = BUILTIN_TYPES | set(self.definitions)
        for identifier, definition in self.definitions.items():
            kind = definition.get("kind")
            if kind == "enum":
                values = definition.get("values")
                if not isinstance(values, Mapping) or not values:
                    raise RuleTypeError("enum {0} requires named values".format(identifier))
                for name, value in values.items():
                    if not isinstance(name, str) or not name:
                        raise RuleTypeError("enum {0} has an invalid value name".format(identifier))
                    _validate_json_scalar(value, "enum {0}.{1}".format(identifier, name))
                keys = [_canonical_key(value) for value in values.values()]
                if len(keys) != len(set(keys)):
                    raise RuleTypeError("enum {0} values must be unique".format(identifier))
            elif kind == "record":
                fields = definition.get("fields")
                if not isinstance(fields, list):
                    raise RuleTypeError("record {0} requires fields".format(identifier))
                names = []
                for field in fields:
                    if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
                        raise RuleTypeError("record {0} has an invalid field".format(identifier))
                    names.append(str(field["name"]))
                    _require_type_ref(field.get("type"), declared, "record {0}".format(identifier))
                if len(names) != len(set(names)):
                    raise RuleTypeError("record {0} field names must be unique".format(identifier))
            elif kind in ("list", "set"):
                _require_type_ref(definition.get("element_type"), declared, kind + " " + identifier)
                _validate_size_bounds(definition, kind + " " + identifier)
            elif kind == "map":
                _require_type_ref(definition.get("key_type"), declared, "map " + identifier)
                _require_type_ref(definition.get("value_type"), declared, "map " + identifier)
                _validate_size_bounds(definition, "map " + identifier)
            elif kind == "optional":
                _require_type_ref(definition.get("item_type"), declared, "optional " + identifier)
            elif kind == "fixed":
                scale = definition.get("scale")
                if isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0:
                    raise RuleTypeError("fixed {0} requires a positive integer scale".format(identifier))
                for key in ("min_scaled", "max_scaled"):
                    if key in definition:
                        _require_integer(definition[key], "fixed {0}.{1}".format(identifier, key), key)
                if definition.get("min_scaled") is not None and definition.get("max_scaled") is not None:
                    if definition["min_scaled"] > definition["max_scaled"]:
                        raise RuleTypeError("fixed {0} has inverted bounds".format(identifier))
            else:
                raise RuleTypeError("type {0} has unsupported kind {1}".format(identifier, kind))

    def _ensure_acyclic_definitions(self) -> None:
        dependencies: Dict[str, Set[str]] = {identifier: set() for identifier in self.definitions}
        for identifier, definition in self.definitions.items():
            kind = definition.get("kind")
            references = []
            if kind == "record":
                references.extend(field.get("type") for field in definition.get("fields", []))
            elif kind in ("list", "set"):
                references.append(definition.get("element_type"))
            elif kind == "map":
                references.extend((definition.get("key_type"), definition.get("value_type")))
            elif kind == "optional":
                references.append(definition.get("item_type"))
            dependencies[identifier].update(ref for ref in references if ref in self.definitions)

        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise RuleTypeError("recursive value type definitions are not supported: {0}".format(identifier))
            if identifier in visited:
                return
            visiting.add(identifier)
            for dependency in dependencies[identifier]:
                visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in dependencies:
            visit(identifier)


def _validate_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        raise RuleTypeError("{0}: binary floating-point values are not allowed".format(path))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _validate_json_value(item, "{0}[{1}]".format(path, index))
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RuleTypeError("{0}: JSON object keys must be strings".format(path))
            _validate_json_value(item, path + "." + key)
        return
    raise RuleTypeError("{0}: unsupported runtime value {1}".format(path, type(value).__name__))


def _validate_json_scalar(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    raise RuleTypeError("{0}: enum values must be JSON scalars without floats".format(path))


def _same_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _canonical_key(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RuleTypeError("value is not canonically serializable: {0}".format(exc))


def _require_integer(value: Any, path: str, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuleTypeError("{0}: expected integer {1}".format(path, label))


def _require_id(value: Any, path: str, prefix: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise RuleTypeError("{0}: expected {1} ID".format(path, prefix))


def _require_type_ref(value: Any, declared: Set[str], label: str) -> None:
    if not isinstance(value, str) or value not in declared:
        raise RuleTypeError("{0} references unknown type {1}".format(label, value))


def _validate_size_bounds(definition: Mapping[str, Any], label: str) -> None:
    minimum = definition.get("min_items", 0)
    maximum: Optional[int] = definition.get("max_items")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        raise RuleTypeError("{0} has invalid min_items".format(label))
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < minimum):
        raise RuleTypeError("{0} has invalid max_items".format(label))
