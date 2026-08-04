"""Canonical CubeEngine Rule Schema v1 and semantic validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .report import Diagnostic


RULE_SCHEMA_VERSION = "cubeengine.srtp/rule-schema-v1"
JSON_SCHEMA_PATH = Path(__file__).with_name("rule-schema-v1.schema.json")

FLOW_MODELS = {
    "turn_based", "simultaneous", "event_driven", "real_time",
    "tick_based", "hybrid", "unknown",
}
ANCHORS = {"cell_center", "grid_intersection", "edge", "free", "unknown"}
TOPOLOGIES = {"rectangular_grid", "hex_grid", "graph", "continuous", "unknown"}
ACTOR_KINDS = {"human", "ai", "human_or_ai", "system", "chance"}
ENTITY_KINDS = {
    "piece", "avatar", "token", "terrain", "cell_marker", "projectile",
    "collectible", "obstacle", "region", "system_object", "unknown",
}
ACTION_VERBS = {
    "place", "move", "select", "reveal", "toggle", "rotate", "transform",
    "spawn", "remove", "capture", "swap", "push", "shoot", "control",
    "pass", "resolve", "tick", "unknown",
}


def new_rule_schema(game_id: str, name: str = "") -> Dict[str, Any]:
    """Create an honest partial schema; unknown does not mean a guessed default."""

    clean_id = str(game_id or "unidentified_game").strip() or "unidentified_game"
    return {
        "schema_version": RULE_SCHEMA_VERSION,
        "source": {
            "path": "",
            "format": "unknown",
            "sha256": "",
            "provenance": {},
        },
        "game": {
            "id": clean_id,
            "name": str(name or clean_id),
            "description": "",
            "classification": {
                "mechanic_families": [],
                "agency": "unknown",
                "state_scope": "unknown",
            },
        },
        "space": {
            "topology": "rectangular_grid",
            "dimensions": {"x": None, "y": None, "z": 1},
            "coordinate_anchor": "unknown",
            "coordinate_system": {
                "origin": [0, 0, 0],
                "axis_order": ["x", "y", "z"],
                "index_base": 0,
            },
            "adjacency": {"kind": "orthogonal", "diagonals": False},
            "boundaries": {"x": "bounded", "y": "bounded", "z": "bounded"},
        },
        "participants": [],
        "entities": [],
        "state": {
            "board": {"empty_value": 0, "cell_states": {"0": "empty"}},
            "variables": [],
            "information": "unknown",
        },
        "setup": {"placements": [], "generators": []},
        "flow": {
            "model": "unknown",
            "turn_order": [],
            "phases": [],
            "tick_rate": None,
        },
        "actions": [],
        "randomness": {
            "model": "deterministic",
            "events": [],
        },
        "goals": [],
        "outcomes": [],
        "modes": [{"id": "default", "name": "Default", "overrides": {}}],
        "ui_hints": {},
        "extensions": {
            "unmapped_source_fields": {},
            "parser_notes": [],
        },
    }


def normalize_rule_schema(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Fill structural containers without inventing missing game semantics."""

    if not isinstance(value, Mapping):
        return new_rule_schema("invalid_input")
    source = deepcopy(dict(value))
    game_value = source.get("game", {})
    if isinstance(game_value, Mapping):
        game_id = game_value.get("id", source.get("game_id", "unidentified_game"))
        game_name = game_value.get("name", game_id)
    else:
        game_id = source.get("game_id", "unidentified_game")
        game_name = game_id
    result = new_rule_schema(str(game_id), str(game_name))
    _deep_merge(result, source)
    result["schema_version"] = RULE_SCHEMA_VERSION
    space = result.get("space")
    dimensions = space.get("dimensions", {}) if isinstance(space, Mapping) else {}
    if isinstance(dimensions, MutableMapping) and dimensions.get("z") is None:
        dimensions["z"] = 1
    return result


def validate_rule_schema(schema: Mapping[str, Any]) -> List[Diagnostic]:
    """Validate structure plus cross-field references without third-party packages."""

    diagnostics: List[Diagnostic] = []
    if not isinstance(schema, Mapping):
        return [Diagnostic("error", "schema.type", "$", "Rule Schema must be an object.")]
    if schema.get("schema_version") != RULE_SCHEMA_VERSION:
        diagnostics.append(Diagnostic(
            "error", "schema.version", "schema_version",
            "Unsupported or missing Rule Schema version.",
            suggestion="Use {0}.".format(RULE_SCHEMA_VERSION),
        ))
    for key in ("source", "game", "space", "state", "setup", "flow", "randomness", "ui_hints", "extensions"):
        if not isinstance(schema.get(key), Mapping):
            diagnostics.append(Diagnostic("error", "field.object", key, "Field must be an object."))
    for key in ("participants", "entities", "actions", "goals", "outcomes", "modes"):
        if not isinstance(schema.get(key), list):
            diagnostics.append(Diagnostic("error", "field.list", key, "Field must be a list."))

    game = _mapping(schema.get("game"))
    _require_nonempty_string(game.get("id"), "game.id", diagnostics)
    _require_nonempty_string(game.get("name"), "game.name", diagnostics)

    space = _mapping(schema.get("space"))
    if space.get("topology") not in TOPOLOGIES:
        diagnostics.append(Diagnostic("error", "space.topology", "space.topology", "Unsupported topology value."))
    dimensions = _mapping(space.get("dimensions"))
    for axis in ("x", "y", "z"):
        value = dimensions.get(axis)
        if value is None:
            diagnostics.append(Diagnostic(
                "error", "space.dimension_missing", "space.dimensions.{0}".format(axis),
                "Grid extent could not be established.",
                suggestion="Provide an explicit cell count or use Function 2 to resolve the source semantics.",
                requires_llm=True,
            ))
        elif isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            diagnostics.append(Diagnostic(
                "error", "space.dimension_type", "space.dimensions.{0}".format(axis),
                "Dimension must be a positive integer cell/site count.",
            ))
    if space.get("coordinate_anchor") not in ANCHORS:
        diagnostics.append(Diagnostic("error", "space.anchor", "space.coordinate_anchor", "Unsupported coordinate anchor."))
    elif space.get("coordinate_anchor") == "unknown":
        diagnostics.append(Diagnostic(
            "warning", "space.anchor_unresolved", "space.coordinate_anchor",
            "The source does not prove whether actions target cells, intersections, edges or free space.",
            suggestion="Choose an anchor in Workbench or resolve it through Function 2.",
            requires_llm=True,
        ))

    participants = schema.get("participants")
    participant_ids = _validate_unique_objects(participants, "participants", diagnostics)
    for index, participant in enumerate(participants if isinstance(participants, list) else []):
        if not isinstance(participant, Mapping):
            continue
        if participant.get("kind") not in ACTOR_KINDS:
            diagnostics.append(Diagnostic(
                "error", "participant.kind", "participants[{0}].kind".format(index),
                "Participant kind must identify human, AI, system or chance agency.",
            ))

    entities = schema.get("entities")
    entity_ids = _validate_unique_objects(entities, "entities", diagnostics)
    for index, entity in enumerate(entities if isinstance(entities, list) else []):
        if not isinstance(entity, Mapping):
            continue
        if entity.get("kind", "unknown") not in ENTITY_KINDS:
            diagnostics.append(Diagnostic("error", "entity.kind", "entities[{0}].kind".format(index), "Unsupported entity kind."))
        owner = entity.get("owner")
        if owner not in (None, "none", "system", "shared", "current_role") and owner not in participant_ids:
            diagnostics.append(Diagnostic(
                "error", "entity.owner_reference", "entities[{0}].owner".format(index),
                "Entity owner does not reference a declared participant.",
            ))

    flow = _mapping(schema.get("flow"))
    if flow.get("model") not in FLOW_MODELS:
        diagnostics.append(Diagnostic("error", "flow.model", "flow.model", "Unsupported flow model."))
    if flow.get("model") == "unknown":
        diagnostics.append(Diagnostic(
            "warning", "flow.unresolved", "flow.model",
            "The source does not establish turn-based, tick-based or real-time progression.",
            requires_llm=True,
        ))
    for role in flow.get("turn_order", []) if isinstance(flow.get("turn_order"), list) else []:
        if role not in participant_ids:
            diagnostics.append(Diagnostic("error", "flow.role_reference", "flow.turn_order", "Turn order references an undeclared participant."))

    actions = schema.get("actions")
    _validate_unique_objects(actions, "actions", diagnostics)
    if not isinstance(actions, list) or not actions:
        diagnostics.append(Diagnostic(
            "warning", "actions.unresolved", "actions",
            "No executable player, system or chance action was identified.",
            suggestion="Describe at least one action or pass this partial schema to Function 2.",
            requires_llm=True,
        ))
    for index, action in enumerate(actions if isinstance(actions, list) else []):
        if not isinstance(action, Mapping):
            continue
        if action.get("verb") not in ACTION_VERBS:
            diagnostics.append(Diagnostic("error", "action.verb", "actions[{0}].verb".format(index), "Unsupported action verb."))
        actor = action.get("actor")
        if actor not in ("current_role", "any", "system", "chance") and actor not in participant_ids:
            diagnostics.append(Diagnostic("error", "action.actor_reference", "actions[{0}].actor".format(index), "Action actor is not declared."))
        if action.get("executable") is False:
            if action.get("semantic_status") == "proven":
                diagnostics.append(Diagnostic(
                    "info", "action.source_runtime", "actions[{0}]".format(index),
                    "The action is understood and executable in the original game, but is not compiled into STAL yet.",
                ))
            else:
                diagnostics.append(Diagnostic(
                    "warning", "action.semantic_handoff", "actions[{0}]".format(index),
                    "The action was detected, but its source function was not converted into declarative preconditions/effects.",
                    requires_llm=True,
                ))
        actor_state_required = any(
            isinstance(effect, Mapping) and effect.get("value") == "$actor_state"
            for effect in action.get("effects", []) if isinstance(action.get("effects"), list)
        )
        if actor_state_required and not any(
            isinstance(entity, Mapping) and isinstance(entity.get("state_value"), int)
            for entity in (entities if isinstance(entities, list) else [])
        ):
            diagnostics.append(Diagnostic(
                "warning", "action.actor_state_unresolved", "actions[{0}].effects".format(index),
                "$actor_state is used but no entity maps a participant to an integer board state.",
                requires_llm=True,
            ))

    _validate_unique_objects(schema.get("outcomes"), "outcomes", diagnostics)
    for index, outcome in enumerate(schema.get("outcomes") if isinstance(schema.get("outcomes"), list) else []):
        if not isinstance(outcome, Mapping):
            continue
        result = _mapping(outcome.get("result"))
        _require_nonempty_string(result.get("status"), "outcomes[{0}].result.status".format(index), diagnostics)
        if not isinstance(result.get("is_terminal"), bool):
            diagnostics.append(Diagnostic("error", "outcome.terminal_type", "outcomes[{0}].result.is_terminal".format(index), "is_terminal must be boolean."))
        if outcome.get("executable") is False:
            if outcome.get("semantic_status") == "proven":
                diagnostics.append(Diagnostic(
                    "info", "outcome.source_runtime", "outcomes[{0}]".format(index),
                    "The outcome is understood in the original runtime but is not compiled into STAL yet.",
                ))
            else:
                diagnostics.append(Diagnostic(
                    "warning", "outcome.semantic_handoff", "outcomes[{0}]".format(index),
                    "A result function was detected but not safely converted into a declarative condition.",
                    requires_llm=True,
                ))

    randomness = _mapping(schema.get("randomness"))
    if randomness.get("model") not in ("deterministic", "stochastic", "mixed", "unknown"):
        diagnostics.append(Diagnostic("error", "randomness.model", "randomness.model", "Unsupported randomness model."))
    if randomness.get("model") in ("stochastic", "mixed") and not randomness.get("events"):
        diagnostics.append(Diagnostic(
            "warning", "randomness.distribution_missing", "randomness.events",
            "Randomness was detected but no event distribution is defined.",
            requires_llm=True,
        ))

    modes = schema.get("modes")
    _validate_unique_objects(modes, "modes", diagnostics)
    return diagnostics


def classify_schema(schema: MutableMapping[str, Any]) -> None:
    """Derive orthogonal classifications from normalized content."""

    game = schema.get("game")
    if not isinstance(game, MutableMapping):
        game = {}
        schema["game"] = game
    classification = game.get("classification")
    if not isinstance(classification, MutableMapping):
        classification = {}
        game["classification"] = classification
    actions = schema.get("actions") if isinstance(schema.get("actions"), list) else []
    verbs = sorted({
        action.get("verb")
        for action in actions
        if isinstance(action, Mapping) and action.get("verb") not in (None, "unknown")
    })
    classification["mechanic_families"] = verbs
    if any(verb in verbs for verb in ("move", "control", "shoot", "push")):
        classification["agency"] = "controlled_entity"
    elif any(verb in verbs for verb in ("place", "select", "reveal", "toggle")):
        classification["agency"] = "direct_space_interaction"
    elif any(action.get("actor") == "system" for action in actions if isinstance(action, Mapping)):
        classification["agency"] = "system_driven"
    else:
        classification["agency"] = "unknown"
    state = schema.get("state") if isinstance(schema.get("state"), Mapping) else {}
    flow = schema.get("flow") if isinstance(schema.get("flow"), Mapping) else {}
    randomness = schema.get("randomness") if isinstance(schema.get("randomness"), Mapping) else {}
    extended_state = bool(state.get("variables"))
    classification["state_scope"] = "extended_game_state" if extended_state else "board_only"
    classification["participant_model"] = _participant_model(schema.get("participants", []))
    classification["temporal_model"] = flow.get("model", "unknown")
    classification["uncertainty"] = randomness.get("model", "unknown")
    classification["information"] = state.get("information", "unknown")


def set_path(target: MutableMapping[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: MutableMapping[str, Any] = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, MutableMapping):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def get_path(target: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = target
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _deep_merge(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), MutableMapping):
            _deep_merge(target[key], value)  # type: ignore[index]
        else:
            target[key] = deepcopy(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _require_nonempty_string(value: Any, path: str, diagnostics: List[Diagnostic]) -> None:
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(Diagnostic("error", "field.required_string", path, "A non-empty string is required."))


def _validate_unique_objects(value: Any, path: str, diagnostics: List[Diagnostic]) -> set:
    if not isinstance(value, list):
        diagnostics.append(Diagnostic("error", "field.list", path, "Field must be a list."))
        return set()
    identifiers = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            diagnostics.append(Diagnostic("error", "field.object", "{0}[{1}]".format(path, index), "List item must be an object."))
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            diagnostics.append(Diagnostic("error", "field.id", "{0}[{1}].id".format(path, index), "A non-empty id is required."))
        elif identifier in identifiers:
            diagnostics.append(Diagnostic("error", "field.duplicate_id", "{0}[{1}].id".format(path, index), "IDs must be unique within this section."))
        else:
            identifiers.add(identifier)
    return identifiers


def _participant_model(participants: Any) -> str:
    if not isinstance(participants, list):
        return "unknown"
    decision_roles = [
        item for item in participants
        if isinstance(item, Mapping) and item.get("kind") not in ("system", "chance")
    ]
    if not decision_roles:
        return "zero_player"
    if len(decision_roles) == 1:
        return "single_player"
    symmetry = {item.get("symmetry_group") for item in decision_roles}
    return "multi_player_symmetric" if len(symmetry) == 1 and None not in symmetry else "multi_player_asymmetric"
