"""Deterministic Rule IR authoring configuration.

Modes are intentionally limited to typed parameter overrides. Structural IR
changes use revision-safe JSON Patch proposals instead of an unvalidated mode
escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Sequence

from .expression import EvaluationContext, ExpressionError, ExpressionEvaluator
from .types import RuleTypeError, RuleTypeRegistry


class RuleConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedRuleConfiguration:
    mode_id: Optional[str]
    values_by_id: Mapping[str, Any]
    values_by_key: Mapping[str, Any]
    types_by_key: Mapping[str, str]


def resolve_rule_configuration(
    document: Mapping[str, Any],
    *,
    mode_id: Optional[str] = None,
    overrides: Optional[Mapping[str, Any]] = None,
) -> ResolvedRuleConfiguration:
    """Resolve defaults < selected mode < explicit designer/session values."""

    definitions = document.get("parameters", [])
    if not isinstance(definitions, list):
        raise RuleConfigurationError("Rule IR parameters must be an array")
    registry = RuleTypeRegistry(document.get("types", []))
    evaluator = ExpressionEvaluator()
    parameter_by_id: Dict[str, Mapping[str, Any]] = {}
    for definition in definitions:
        if not isinstance(definition, Mapping):
            raise RuleConfigurationError("parameter definition must be an object")
        identifier = str(definition.get("id", ""))
        if not identifier or identifier in parameter_by_id:
            raise RuleConfigurationError("parameter IDs must be non-empty and unique")
        parameter_by_id[identifier] = definition

    selected_mode = _select_mode(document.get("modes", []), mode_id)
    mode_overrides = _mode_override_map(
        selected_mode.get("overrides", []) if selected_mode is not None else [], parameter_by_id,
    )
    explicit = dict(overrides or {})
    unknown_explicit = set(explicit) - set(parameter_by_id)
    if unknown_explicit:
        raise RuleConfigurationError("unknown parameter override: {0}".format(sorted(unknown_explicit)[0]))

    by_id: Dict[str, Any] = {}
    by_key: Dict[str, Any] = {}
    types_by_key: Dict[str, str] = {}
    for definition in definitions:
        identifier = str(definition["id"])
        key = _parameter_key(definition)
        if key in by_key:
            raise RuleConfigurationError("parameter keys must be unique: {0}".format(key))
        if identifier in explicit:
            value = explicit[identifier]
        else:
            expression = mode_overrides.get(identifier, definition.get("default"))
            if not isinstance(expression, Mapping):
                raise RuleConfigurationError("parameter {0} requires a default expression".format(identifier))
            try:
                value = evaluator.evaluate(expression, EvaluationContext(parameters=dict(by_key)))
            except ExpressionError as exc:
                raise RuleConfigurationError(str(exc))
        try:
            registry.validate(value, str(definition["type"]), "parameter " + identifier)
            _validate_constraints(value, definition, identifier)
        except (RuleTypeError, KeyError) as exc:
            raise RuleConfigurationError(str(exc))
        by_id[identifier] = value
        by_key[key] = value
        types_by_key[key] = str(definition["type"])

    return ResolvedRuleConfiguration(
        str(selected_mode["id"]) if selected_mode is not None else None,
        MappingProxyType(dict(by_id)),
        MappingProxyType(dict(by_key)),
        MappingProxyType(dict(types_by_key)),
    )


def _select_mode(value: Any, requested: Optional[str]) -> Optional[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise RuleConfigurationError("Rule IR modes must be an array")
    modes = {str(item["id"]): item for item in value if isinstance(item, Mapping) and item.get("id")}
    selected = requested
    if selected is None and "rule:mode.default" in modes:
        selected = "rule:mode.default"
    if selected is None:
        return None
    if selected not in modes:
        raise RuleConfigurationError("unknown Rule IR mode: {0}".format(selected))
    return modes[selected]


def _mode_override_map(
    overrides: Any, definitions: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(overrides, list):
        raise RuleConfigurationError("mode overrides must be an array")
    result: Dict[str, Mapping[str, Any]] = {}
    for override in overrides:
        if not isinstance(override, Mapping):
            raise RuleConfigurationError("mode override must be an object")
        identifier = str(override.get("parameter", ""))
        if identifier not in definitions:
            raise RuleConfigurationError("mode references unknown parameter: {0}".format(identifier))
        if identifier in result:
            raise RuleConfigurationError("mode overrides a parameter more than once: {0}".format(identifier))
        expression = override.get("value")
        if not isinstance(expression, Mapping):
            raise RuleConfigurationError("mode override requires a value expression")
        result[identifier] = expression
    return result


def _parameter_key(definition: Mapping[str, Any]) -> str:
    explicit = definition.get("key")
    if isinstance(explicit, str) and explicit:
        return explicit
    identifier = str(definition.get("id", ""))
    prefix = "rule:parameter."
    return identifier[len(prefix):] if identifier.startswith(prefix) else identifier.rsplit(".", 1)[-1]


def _validate_constraints(value: Any, definition: Mapping[str, Any], identifier: str) -> None:
    try:
        if "minimum" in definition and value < definition["minimum"]:
            raise RuleConfigurationError("parameter {0} is below minimum".format(identifier))
        if "maximum" in definition and value > definition["maximum"]:
            raise RuleConfigurationError("parameter {0} is above maximum".format(identifier))
    except TypeError:
        raise RuleConfigurationError("parameter {0} has incompatible numeric constraints".format(identifier))
    if "choices" in definition:
        choices = definition["choices"]
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or value not in choices:
            raise RuleConfigurationError("parameter {0} is outside declared choices".format(identifier))
