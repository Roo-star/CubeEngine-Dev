"""Rule IR v2 draft model, semantic validation and deterministic hashing.

The JSON Schema next to this module is the portable structural contract.  The
validator below adds cross-reference and semantic checks without requiring a
third-party JSON Schema package in the MVP runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set

from .types import RuleTypeError, RuleTypeRegistry
from .random_service import (
    PCG32_ALGORITHM,
    RECORDED_ALGORITHM,
    SUPPORTED_DISTRIBUTIONS,
    SUPPORTED_SEED_POLICIES,
)


RULE_IR_VERSION = "cubeengine.rule-ir/2.0-alpha.1"
RULE_IR_SCHEMA_PATH = Path(__file__).with_name("rule-ir-v2.schema.json")

_ID = re.compile(r"^(?:rule|scene|asset|input):[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_LOCAL = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_EXTENSION_CAPABILITY = re.compile(
    r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)+/[1-9][0-9]*(?:\.[0-9]+){0,2}$"
)
_BUILTIN_TYPES = {
    "core:any", "core:bool", "core:int", "core:fixed", "core:string",
    "core:coord", "core:entity_id", "core:participant_id", "core:action_id",
}
_EXPRESSION_OPS = {
    "literal", "ref", "param", "var", "call", "list", "vector",
    "not", "and", "or", "eq", "ne", "lt", "lte", "gt", "gte",
    "add", "sub", "mul", "div", "mod", "min", "max", "neg", "abs",
    "if", "coalesce", "contains", "count", "all", "any",
}
_COMMAND_OPS = {
    "state.set", "state.increment", "grid.set", "grid.toggle", "entity.spawn",
    "entity.despawn", "entity.set", "event.emit", "event.schedule",
    "event.cancel", "phase.set", "random.sample", "random.draw", "foreach", "assert",
}


@dataclass(frozen=True)
class RuleIRDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


def new_rule_ir(document_id: str, title: str = "") -> Dict[str, Any]:
    """Return an honest, structurally complete Rule IR draft.

    It is intentionally not compile-ready until topology and mechanics are
    supplied.  Empty containers are not treated as inferred game semantics.
    """

    return {
        "ir_version": RULE_IR_VERSION,
        "document_id": document_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {
            "title": title or document_id,
            "description": "",
            "source_project_hash": "",
            "determinism": "deterministic",
        },
        "dependencies": {
            "scene_ir": None,
            "asset_ir": None,
            "input_ir": None,
            "extensions": [],
        },
        "parameters": [],
        "types": [],
        "participants": [],
        "topologies": [],
        "state": {
            "variables": [],
            "entity_types": [],
            "initial_effects": [],
            "information_model": "perfect",
        },
        "queries": [],
        "events": [],
        "actions": [],
        "systems": [],
        "flow": {
            "model": "event_driven",
            "phases": [
                {"id": "rule:phase.input", "order": 100},
                {"id": "rule:phase.update", "order": 200},
                {"id": "rule:phase.outcome", "order": 300},
            ],
            "initial_phase": "rule:phase.input",
            "scheduler": {
                "clock": "event_queue",
                "tick_hz": None,
                "ordering": "phase_priority_id",
            },
        },
        "random_streams": [],
        "goals": [],
        "outcomes": [],
        "modes": [{"id": "rule:mode.default", "name": "Default", "overrides": []}],
        "invariants": [],
        "extensions": {},
        "provenance": {},
        "unresolved": [
            {
                "path": "/topologies",
                "reason": "No source topology has been supplied.",
                "required": True,
                "owner": "importer_or_llm",
            },
            {
                "path": "/actions",
                "reason": "No player/system action or event system has been supplied.",
                "required": True,
                "owner": "importer_or_llm",
            },
        ],
    }


def load_rule_ir(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError("could not read Rule IR: {0}".format(path)) from error
    except json.JSONDecodeError as error:
        raise ValueError("Rule IR is not valid JSON: {0}".format(error.msg)) from error
    if not isinstance(value, dict):
        raise ValueError("Rule IR root must be an object")
    return value


def canonical_rule_ir_hash(document: Mapping[str, Any]) -> str:
    """Hash semantic content without recursively hashing the hash field."""

    value = deepcopy(dict(document))
    value["content_hash"] = ""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def seal_rule_ir(document: Mapping[str, Any], revision: Optional[int] = None) -> Dict[str, Any]:
    result = deepcopy(dict(document))
    if revision is not None:
        result["revision"] = revision
    result["content_hash"] = canonical_rule_ir_hash(result)
    return result


def is_rule_ir_compile_ready(document: Mapping[str, Any]) -> bool:
    if any(item.severity == "error" for item in validate_rule_ir(document)):
        return False
    unresolved = document.get("unresolved", [])
    if isinstance(unresolved, list) and any(
        isinstance(item, Mapping) and item.get("required") is True for item in unresolved
    ):
        return False
    topologies = document.get("topologies", [])
    actions = document.get("actions", [])
    systems = document.get("systems", [])
    return bool(topologies) and bool(actions or systems)


def validate_rule_ir(document: Mapping[str, Any]) -> List[RuleIRDiagnostic]:
    diagnostics: List[RuleIRDiagnostic] = []
    if not isinstance(document, Mapping):
        return [_error("root.type", "$", "Rule IR root must be an object.")]
    if document.get("ir_version") != RULE_IR_VERSION:
        diagnostics.append(_error("version.unsupported", "/ir_version", "Unsupported Rule IR version."))
    _validate_public_id(document.get("document_id"), "/document_id", "rule", diagnostics)
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = document.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not re.fullmatch(r"[0-9a-f]{64}", content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))

    mapping_keys = ("metadata", "dependencies", "state", "flow", "extensions", "provenance")
    list_keys = (
        "parameters", "types", "participants", "topologies", "queries", "events",
        "actions", "systems", "random_streams", "goals", "outcomes", "modes",
        "invariants", "unresolved",
    )
    for key in mapping_keys:
        if not isinstance(document.get(key), Mapping):
            diagnostics.append(_error("field.object", "/" + key, "Field must be an object."))
    for key in list_keys:
        if not isinstance(document.get(key), list):
            diagnostics.append(_error("field.array", "/" + key, "Field must be an array."))
    dependencies = document.get("dependencies")
    if isinstance(dependencies, Mapping):
        extensions = dependencies.get("extensions")
        if not isinstance(extensions, list) or any(
            not isinstance(item, str) or not _EXTENSION_CAPABILITY.fullmatch(item)
            for item in extensions if isinstance(extensions, list)
        ) or len(extensions) != len(set(extensions)):
            diagnostics.append(_error(
                "dependency.extensions", "/dependencies/extensions",
                "Extensions must be unique versioned capability IDs.",
            ))

    registry: Dict[str, str] = {}
    groups = {
        "parameters": "parameter", "types": "type", "participants": "participant",
        "topologies": "topology", "queries": "query", "events": "event",
        "actions": "action", "systems": "system", "random_streams": "random",
        "goals": "goal", "outcomes": "outcome", "modes": "mode",
        "invariants": "invariant",
    }
    for key, noun in groups.items():
        _register_objects(document.get(key), key, noun, registry, diagnostics)
    flow = document.get("flow") if isinstance(document.get("flow"), Mapping) else {}
    _register_objects(flow.get("phases"), "flow/phases", "phase", registry, diagnostics)
    state = document.get("state") if isinstance(document.get("state"), Mapping) else {}
    _register_objects(state.get("variables"), "state/variables", "state", registry, diagnostics)
    _register_objects(state.get("entity_types"), "state/entity_types", "entity_type", registry, diagnostics)

    declared_types = _BUILTIN_TYPES | {identifier for identifier, kind in registry.items() if kind == "type"}
    _validate_types(document.get("types"), diagnostics)
    parameter_ids = {identifier for identifier, kind in registry.items() if kind == "parameter"}
    _validate_root_parameters(document.get("parameters"), declared_types, diagnostics)
    _validate_modes(document.get("modes"), parameter_ids, diagnostics)
    _validate_topologies(document.get("topologies"), diagnostics)
    topology_ids = {identifier for identifier, kind in registry.items() if kind == "topology"}
    entity_type_ids = {identifier for identifier, kind in registry.items() if kind == "entity_type"}
    _validate_state(state, declared_types, topology_ids, entity_type_ids, diagnostics)
    _validate_queries(document.get("queries"), declared_types, diagnostics)
    phases = {identifier for identifier, kind in registry.items() if kind == "phase"}
    random_streams = {identifier for identifier, kind in registry.items() if kind == "random"}
    _validate_random_streams(document.get("random_streams"), diagnostics)
    events = {identifier for identifier, kind in registry.items() if kind == "event"}
    _validate_events(document.get("events"), declared_types, diagnostics)
    _validate_actions(document.get("actions"), declared_types, phases, random_streams, events, diagnostics)
    _validate_systems(document.get("systems"), phases, random_streams, events, diagnostics)
    _validate_flow(flow, phases, diagnostics)
    _validate_outcomes(document.get("outcomes"), diagnostics)
    _validate_commands(state.get("initial_effects"), "/state/initial_effects", random_streams, events, diagnostics)

    for index, item in enumerate(document.get("invariants", []) if isinstance(document.get("invariants"), list) else []):
        if isinstance(item, Mapping):
            _validate_expression(item.get("condition"), "/invariants/{0}/condition".format(index), diagnostics)
    for index, item in enumerate(document.get("unresolved", []) if isinstance(document.get("unresolved"), list) else []):
        path = "/unresolved/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("unresolved.type", path, "Unresolved item must be an object."))
            continue
        if not isinstance(item.get("path"), str) or not str(item.get("path")).startswith("/"):
            diagnostics.append(_error("unresolved.path", path + "/path", "Unresolved path must be a JSON Pointer."))
        if not isinstance(item.get("reason"), str) or not item.get("reason"):
            diagnostics.append(_error("unresolved.reason", path + "/reason", "Unresolved item needs a reason."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("unresolved.required", path + "/required", "Required must be a boolean."))

    return diagnostics


def _register_objects(
    value: Any, path: str, noun: str, registry: MutableMapping[str, str],
    diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        item_path = "/{0}/{1}".format(path, index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("{0}.type".format(noun), item_path, "Item must be an object."))
            continue
        identifier = item.get("id")
        _validate_public_id(identifier, item_path + "/id", "rule", diagnostics)
        if isinstance(identifier, str):
            if identifier in registry:
                diagnostics.append(_error("id.duplicate", item_path + "/id", "ID is already used at {0}.".format(registry[identifier])))
            else:
                registry[identifier] = noun


def _validate_topologies(value: Any, diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    kinds = {"rect_grid", "hex_grid", "graph", "continuous", "hybrid"}
    anchors = {"cell", "vertex", "edge", "free"}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/topologies/{0}".format(index)
        if item.get("kind") not in kinds:
            diagnostics.append(_error("topology.kind", path + "/kind", "Unsupported topology kind."))
        if item.get("anchor") not in anchors:
            diagnostics.append(_error("topology.anchor", path + "/anchor", "Unsupported topology anchor."))
        axes = item.get("axes")
        if not isinstance(axes, list) or not axes:
            diagnostics.append(_error("topology.axes", path + "/axes", "Topology requires at least one axis."))
            continue
        names = []
        for axis_index, axis in enumerate(axes):
            axis_path = path + "/axes/{0}".format(axis_index)
            if not isinstance(axis, Mapping):
                diagnostics.append(_error("axis.type", axis_path, "Axis must be an object."))
                continue
            name = axis.get("name")
            if not isinstance(name, str) or not _LOCAL.fullmatch(name):
                diagnostics.append(_error("axis.name", axis_path + "/name", "Axis name must be a local identifier."))
            else:
                names.append(name)
            extent = axis.get("extent")
            if isinstance(extent, bool) or not isinstance(extent, int) or extent <= 0:
                diagnostics.append(_error("axis.extent", axis_path + "/extent", "Axis extent must be a positive integer in a compiled target IR."))
        if len(set(names)) != len(names):
            diagnostics.append(_error("axis.duplicate", path + "/axes", "Axis names must be unique."))


def _validate_types(value: Any, diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    try:
        RuleTypeRegistry(value)
    except RuleTypeError as exc:
        diagnostics.append(_error("type.definition", "/types", str(exc)))


def _validate_root_parameters(
    value: Any, declared_types: Set[str], diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    keys = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/parameters/{0}".format(index)
        if item.get("type") not in declared_types:
            diagnostics.append(_error("type.reference", path + "/type", "Unknown parameter type reference."))
        _validate_expression(item.get("default"), path + "/default", diagnostics)
        key = item.get("key")
        if key is not None:
            if not isinstance(key, str) or not _LOCAL.fullmatch(key):
                diagnostics.append(_error("parameter.key", path + "/key", "Parameter key must be a local identifier."))
            elif key in keys:
                diagnostics.append(_error("parameter.key_duplicate", path + "/key", "Parameter key must be unique."))
            else:
                keys.add(key)
        for name in ("minimum", "maximum"):
            if name in item and (isinstance(item[name], bool) or not isinstance(item[name], int)):
                diagnostics.append(_error("parameter.constraint", path + "/" + name, "Rule-critical numeric constraints must be integers."))
        if "choices" in item and not isinstance(item.get("choices"), list):
            diagnostics.append(_error("parameter.choices", path + "/choices", "Parameter choices must be an array."))


def _validate_modes(value: Any, parameter_ids: Set[str], diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/modes/{0}/overrides".format(index)
        overrides = item.get("overrides")
        if not isinstance(overrides, list):
            diagnostics.append(_error("mode.overrides", path, "Mode overrides must be an array."))
            continue
        seen = set()
        for override_index, override in enumerate(overrides):
            override_path = path + "/{0}".format(override_index)
            if not isinstance(override, Mapping):
                diagnostics.append(_error("mode.override", override_path, "Mode override must be an object."))
                continue
            parameter = override.get("parameter")
            if parameter not in parameter_ids:
                diagnostics.append(_error("mode.parameter", override_path + "/parameter", "Mode references an unknown parameter."))
            elif parameter in seen:
                diagnostics.append(_error("mode.parameter_duplicate", override_path + "/parameter", "Mode overrides a parameter more than once."))
            else:
                seen.add(parameter)
            _validate_expression(override.get("value"), override_path + "/value", diagnostics)


def _validate_state(
    state: Mapping[str, Any], declared_types: Set[str], topology_ids: Set[str],
    entity_type_ids: Set[str], diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(state.get("variables"), list):
        diagnostics.append(_error("state.variables", "/state/variables", "State variables must be an array."))
    if not isinstance(state.get("entity_types"), list):
        diagnostics.append(_error("state.entity_types", "/state/entity_types", "Entity types must be an array."))
    for group in ("variables", "entity_types"):
        for index, item in enumerate(state.get(group, []) if isinstance(state.get(group), list) else []):
            if not isinstance(item, Mapping):
                continue
            type_ref = item.get("type")
            if group == "variables" and type_ref not in declared_types:
                diagnostics.append(_error("type.reference", "/state/{0}/{1}/type".format(group, index), "Unknown state type reference."))
            if group == "variables":
                scope = item.get("scope")
                if scope not in ("global", "participant", "topology_site", "entity"):
                    diagnostics.append(_error("state.scope", "/state/variables/{0}/scope".format(index), "Unsupported state scope."))
                if scope == "topology_site" and item.get("topology") not in topology_ids:
                    diagnostics.append(_error("state.topology", "/state/variables/{0}/topology".format(index), "Topology-site state requires a declared topology."))
                if scope == "entity" and item.get("entity_type") is not None and item.get("entity_type") not in entity_type_ids:
                    diagnostics.append(_error("state.entity_type", "/state/variables/{0}/entity_type".format(index), "Entity-scoped state references an unknown entity type."))
                _validate_expression(item.get("initial"), "/state/variables/{0}/initial".format(index), diagnostics)
            else:
                components = item.get("components")
                if not isinstance(components, list):
                    diagnostics.append(_error("entity.components", "/state/entity_types/{0}/components".format(index), "Entity components must be an array."))
                    continue
                names = set()
                for component_index, component in enumerate(components):
                    path = "/state/entity_types/{0}/components/{1}".format(index, component_index)
                    if not isinstance(component, Mapping):
                        diagnostics.append(_error("entity.component", path, "Entity component must be an object."))
                        continue
                    name = component.get("name")
                    if not isinstance(name, str) or not _LOCAL.fullmatch(name):
                        diagnostics.append(_error("entity.component_name", path + "/name", "Component name must be a local identifier."))
                    elif name in names:
                        diagnostics.append(_error("entity.component_duplicate", path + "/name", "Component name must be unique."))
                    else:
                        names.add(name)
                    if component.get("type") not in declared_types:
                        diagnostics.append(_error("type.reference", path + "/type", "Unknown component type reference."))
                    _validate_expression(component.get("default"), path + "/default", diagnostics)


def _validate_queries(value: Any, declared_types: Set[str], diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/queries/{0}".format(index)
        _validate_parameters(item.get("parameters"), path + "/parameters", declared_types, diagnostics)
        if item.get("result_type") not in declared_types:
            diagnostics.append(_error("type.reference", path + "/result_type", "Unknown query result type."))
        _validate_expression(item.get("expression"), path + "/expression", diagnostics)
        if item.get("ordering") not in ("lexicographic", "stable_id", "distance_then_id", "declared"):
            diagnostics.append(_error("query.ordering", path + "/ordering", "Query requires deterministic ordering."))


def _validate_actions(
    value: Any, declared_types: Set[str], phases: Set[str], random_streams: Set[str],
    events: Set[str], diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/actions/{0}".format(index)
        _validate_expression(item.get("actor"), path + "/actor", diagnostics)
        _validate_parameters(item.get("parameters"), path + "/parameters", declared_types, diagnostics)
        _validate_expression(item.get("precondition"), path + "/precondition", diagnostics)
        _validate_commands(item.get("effects"), path + "/effects", random_streams, events, diagnostics)
        timing = item.get("timing")
        if not isinstance(timing, Mapping) or timing.get("phase") not in phases:
            diagnostics.append(_error("action.phase", path + "/timing/phase", "Action phase must reference a declared phase."))
        encoding = item.get("encoding")
        if not isinstance(encoding, Mapping) or encoding.get("kind") not in (
            "none", "finite_catalogue", "parameter_product", "runtime_enumerated",
        ):
            diagnostics.append(_error("action.encoding", path + "/encoding", "Action requires a supported stable encoding strategy."))


def _validate_systems(
    value: Any, phases: Set[str], random_streams: Set[str], events: Set[str],
    diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/systems/{0}".format(index)
        if item.get("phase") not in phases:
            diagnostics.append(_error("system.phase", path + "/phase", "System phase must reference a declared phase."))
        priority = item.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            diagnostics.append(_error("system.priority", path + "/priority", "System priority must be an integer."))
        trigger = item.get("trigger")
        if not isinstance(trigger, Mapping) or trigger.get("kind") not in (
            "event", "tick", "phase_enter", "phase_exit", "state_changed", "manual",
        ):
            diagnostics.append(_error("system.trigger", path + "/trigger", "System requires a supported trigger."))
        elif isinstance(trigger, Mapping):
            kind = trigger.get("kind")
            if kind == "event" and trigger.get("event") not in events | {"rule:event.action_applied", "rule:event.joint_actions_applied"}:
                diagnostics.append(_error("event.reference", path + "/trigger/event", "Event trigger must reference a declared event."))
            if kind == "tick":
                every = trigger.get("every", 1)
                offset = trigger.get("offset", 0)
                if isinstance(every, bool) or not isinstance(every, int) or every <= 0:
                    diagnostics.append(_error("trigger.every", path + "/trigger/every", "Tick trigger every must be a positive integer."))
                if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                    diagnostics.append(_error("trigger.offset", path + "/trigger/offset", "Tick trigger offset must be a non-negative integer."))
            if kind in ("phase_enter", "phase_exit") and trigger.get("phase") not in (None,) and trigger.get("phase") not in phases:
                diagnostics.append(_error("phase.reference", path + "/trigger/phase", "Phase trigger must reference a declared phase."))
            if kind == "manual" and (not isinstance(trigger.get("name"), str) or not trigger.get("name")):
                diagnostics.append(_error("trigger.name", path + "/trigger/name", "Manual trigger requires a non-empty name."))
        _validate_expression(item.get("condition"), path + "/condition", diagnostics)
        _validate_commands(item.get("effects"), path + "/effects", random_streams, events, diagnostics)


def _validate_flow(flow: Mapping[str, Any], phases: Set[str], diagnostics: List[RuleIRDiagnostic]) -> None:
    if flow.get("model") not in ("turn_based", "simultaneous", "event_driven", "fixed_tick", "real_time", "hybrid"):
        diagnostics.append(_error("flow.model", "/flow/model", "Unsupported flow model."))
    if flow.get("initial_phase") not in phases:
        diagnostics.append(_error("flow.initial_phase", "/flow/initial_phase", "Initial phase must reference a declared phase."))
    scheduler = flow.get("scheduler")
    if not isinstance(scheduler, Mapping):
        diagnostics.append(_error("flow.scheduler", "/flow/scheduler", "Flow scheduler must be an object."))
        return
    clock = scheduler.get("clock")
    if clock not in ("turn", "event_queue", "fixed_tick", "real_time"):
        diagnostics.append(_error("flow.clock", "/flow/scheduler/clock", "Unsupported scheduler clock."))
    tick_hz = scheduler.get("tick_hz")
    if clock in ("fixed_tick", "real_time") and (isinstance(tick_hz, bool) or not isinstance(tick_hz, int) or tick_hz <= 0):
        diagnostics.append(_error("flow.tick_hz", "/flow/scheduler/tick_hz", "Fixed-tick and real-time schedulers require a positive integer tick_hz."))
    max_catch_up = scheduler.get("max_catch_up_ticks", 8)
    if isinstance(max_catch_up, bool) or not isinstance(max_catch_up, int) or max_catch_up <= 0:
        diagnostics.append(_error("flow.max_catch_up", "/flow/scheduler/max_catch_up_ticks", "Maximum catch-up ticks must be a positive integer."))
    if scheduler.get("ordering") != "phase_priority_id":
        diagnostics.append(_error("flow.ordering", "/flow/scheduler/ordering", "Rule IR v2 requires deterministic phase_priority_id ordering."))
    if "turn_eligibility" in flow:
        _validate_expression(flow.get("turn_eligibility"), "/flow/turn_eligibility", diagnostics)


def _validate_random_streams(value: Any, diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/random_streams/{0}".format(index)
        policy = item.get("seed_policy")
        algorithm = item.get("algorithm")
        if policy not in SUPPORTED_SEED_POLICIES:
            diagnostics.append(_error("random.seed_policy", path + "/seed_policy", "Unsupported random seed policy."))
            continue
        required_algorithm = RECORDED_ALGORITHM if policy == "recorded" else PCG32_ALGORITHM
        if algorithm != required_algorithm:
            diagnostics.append(_error(
                "random.algorithm", path + "/algorithm",
                "Seed policy {0} requires versioned algorithm {1}.".format(policy, required_algorithm),
            ))
        if policy == "fixed":
            seed = item.get("seed")
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < (1 << 64):
                diagnostics.append(_error("random.seed", path + "/seed", "Fixed seed must be an unsigned 64-bit integer."))
        sequence = item.get("sequence")
        if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence < (1 << 64)):
            diagnostics.append(_error("random.sequence", path + "/sequence", "Stream sequence must be an unsigned 64-bit integer."))
        if "recorded_values" in item and not isinstance(item.get("recorded_values"), list):
            diagnostics.append(_error("random.recorded_values", path + "/recorded_values", "Recorded values must be an array."))


def _validate_outcomes(value: Any, diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/outcomes/{0}".format(index)
        priority = item.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            diagnostics.append(_error("outcome.priority", path + "/priority", "Outcome priority must be an integer."))
        _validate_expression(item.get("condition"), path + "/condition", diagnostics)
        result = item.get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("status"), str) or not isinstance(result.get("terminal"), bool):
            diagnostics.append(_error("outcome.result", path + "/result", "Outcome result requires status and terminal."))


def _validate_parameters(value: Any, path: str, declared_types: Set[str], diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("parameters.type", path, "Parameters must be an array."))
        return
    names = set()
    for index, item in enumerate(value):
        item_path = path + "/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("parameter.type", item_path, "Parameter must be an object."))
            continue
        name = item.get("name")
        if not isinstance(name, str) or not _LOCAL.fullmatch(name):
            diagnostics.append(_error("parameter.name", item_path + "/name", "Parameter name must be a local identifier."))
        elif name in names:
            diagnostics.append(_error("parameter.duplicate", item_path + "/name", "Parameter name must be unique."))
        else:
            names.add(name)
        if item.get("type") not in declared_types:
            diagnostics.append(_error("type.reference", item_path + "/type", "Unknown parameter type reference."))
        if "domain" in item:
            _validate_expression(item.get("domain"), item_path + "/domain", diagnostics)


def _validate_events(
    value: Any, declared_types: Set[str], diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        _validate_parameters(
            item.get("payload"), "/events/{0}/payload".format(index),
            declared_types, diagnostics,
        )


def _validate_expression(value: Any, path: str, diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("expression.type", path, "Expression must be an AST object."))
        return
    operation = value.get("op")
    if operation not in _EXPRESSION_OPS:
        diagnostics.append(_error("expression.op", path + "/op", "Unsupported expression operation."))
        return
    if operation == "literal" and "value" not in value:
        diagnostics.append(_error("expression.literal", path, "Literal expression requires value."))
    if operation == "ref" and not isinstance(value.get("path"), str):
        diagnostics.append(_error("expression.ref", path + "/path", "Reference expression requires a path."))
    if operation in ("param", "var") and (not isinstance(value.get("name"), str) or not _LOCAL.fullmatch(str(value.get("name")))):
        diagnostics.append(_error("expression.name", path + "/name", "Expression requires a local identifier name."))
    if operation == "call":
        function = value.get("function")
        if not isinstance(function, str) or ":" not in function:
            diagnostics.append(_error("expression.function", path + "/function", "Call function must be namespaced."))
    for key in ("args", "items"):
        if key in value:
            children = value.get(key)
            if not isinstance(children, list):
                diagnostics.append(_error("expression.children", path + "/" + key, "Expression children must be an array."))
            else:
                for index, child in enumerate(children):
                    _validate_expression(child, path + "/{0}/{1}".format(key, index), diagnostics)
    for key in ("condition", "then", "else", "value", "fallback"):
        if key in value and operation != "literal":
            _validate_expression(value[key], path + "/" + key, diagnostics)


def _validate_commands(
    value: Any, path: str, random_streams: Set[str], events: Set[str],
    diagnostics: List[RuleIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("effects.type", path, "Effects must be an array."))
        return
    for index, command in enumerate(value):
        item_path = path + "/{0}".format(index)
        if not isinstance(command, Mapping):
            diagnostics.append(_error("effect.type", item_path, "Effect must be a command object."))
            continue
        operation = command.get("op")
        if operation not in _COMMAND_OPS:
            diagnostics.append(_error("effect.op", item_path + "/op", "Unsupported effect operation."))
            continue
        for key in (
            "target", "scope", "value", "coordinate", "entity", "at", "domain",
            "condition", "payload", "delay_ticks", "off", "on", "schedule_id", "phase",
        ):
            if key in command:
                _validate_expression(command[key], item_path + "/" + key, diagnostics)
        if operation == "entity.spawn" and "components" in command:
            components = command.get("components")
            if not isinstance(components, Mapping):
                diagnostics.append(_error("entity.components", item_path + "/components", "Spawn component overrides must be an object."))
            else:
                for name, expression in components.items():
                    _validate_expression(expression, item_path + "/components/" + str(name), diagnostics)
        if operation in ("random.sample", "random.draw") and command.get("stream") not in random_streams:
            diagnostics.append(_error("random.reference", item_path + "/stream", "Random command must reference a declared stream."))
        if operation in ("random.sample", "random.draw") and (not isinstance(command.get("as"), str) or not _LOCAL.fullmatch(str(command.get("as")))):
            diagnostics.append(_error("random.binding", item_path + "/as", "Random command requires a local result binding."))
        if operation == "random.draw":
            distribution = command.get("distribution")
            if not isinstance(distribution, Mapping) or distribution.get("kind") not in SUPPORTED_DISTRIBUTIONS:
                diagnostics.append(_error("random.distribution", item_path + "/distribution", "Random draw requires a supported explicit distribution."))
            else:
                required = {
                    "uniform_int": ("minimum", "maximum"),
                    "choice": ("values",),
                    "bernoulli": ("numerator", "denominator"),
                    "weighted_choice": ("values", "weights"),
                    "shuffle": ("values",),
                    "sample": ("values", "count"),
                }[distribution["kind"]]
                for key in required:
                    if key not in distribution:
                        diagnostics.append(_error("random.distribution_field", item_path + "/distribution/" + key, "Distribution requires this expression."))
                    else:
                        _validate_expression(distribution[key], item_path + "/distribution/" + key, diagnostics)
        if operation in ("event.emit", "event.schedule") and command.get("event") not in events | {"rule:event.action_applied", "rule:event.joint_actions_applied"}:
            diagnostics.append(_error("event.reference", item_path + "/event", "Event command must reference a declared event."))
        if operation == "event.cancel" and "schedule_id" not in command:
            diagnostics.append(_error("event.cancel", item_path + "/schedule_id", "Event cancellation requires a schedule ID expression."))
        if operation == "phase.set" and "phase" not in command:
            diagnostics.append(_error("phase.set", item_path + "/phase", "Phase transition requires a phase expression."))
        if operation == "foreach":
            if not isinstance(command.get("as"), str) or not _LOCAL.fullmatch(str(command.get("as"))):
                diagnostics.append(_error("foreach.binding", item_path + "/as", "Foreach requires a local binding name."))
            _validate_expression(command.get("query"), item_path + "/query", diagnostics)
            _validate_commands(command.get("effects"), item_path + "/effects", random_streams, events, diagnostics)


def _validate_public_id(value: Any, path: str, prefix: str, diagnostics: List[RuleIRDiagnostic]) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value) or not value.startswith(prefix + ":"):
        diagnostics.append(_error("id.format", path, "ID must be a stable {0}: namespaced identifier.".format(prefix)))


def _error(code: str, path: str, message: str) -> RuleIRDiagnostic:
    return RuleIRDiagnostic("error", code, path, message)
