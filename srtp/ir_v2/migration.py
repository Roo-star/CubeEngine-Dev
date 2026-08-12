"""Compatibility conversion from the executable Rule Schema v1 subset.

The migration preserves unknown mechanics as required unresolved items.  It
does not turn an uncompiled v1 source function into a guessed v2 rule.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .rule_ir import new_rule_ir


def upgrade_rule_schema_v1(schema: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(schema, Mapping) or schema.get("schema_version") != "cubeengine.srtp/rule-schema-v1":
        raise ValueError("migration requires cubeengine.srtp/rule-schema-v1")
    game = schema.get("game") if isinstance(schema.get("game"), Mapping) else {}
    game_slug = _slug(game.get("id", "imported_game"))
    result = new_rule_ir("rule:game.{0}".format(game_slug), str(game.get("name", game_slug)))
    result["metadata"].update({
        "description": str(game.get("description", "")),
        "source_project_hash": str(_mapping(schema.get("source")).get("sha256", "")),
        "determinism": _determinism(schema),
    })
    result["types"] = [_cell_state_type(schema)]
    result["participants"], participant_map = _participants(schema)
    result["topologies"] = [_topology(schema)]
    result["state"] = {
        "variables": [{
            "id": "rule:state.board_cell",
            "name": "Board cell",
            "type": "rule:type.cell_state",
            "scope": "topology_site",
            "topology": "rule:topology.board",
            "initial": {"op": "literal", "value": _empty_value(schema)},
        }],
        "entity_types": _entity_types(schema, participant_map),
        "initial_effects": _initial_effects(schema),
        "information_model": _information_model(schema),
    }
    result["flow"] = _flow(schema, participant_map)
    result["random_streams"] = _random_streams(schema)
    result["goals"] = _goals(schema)
    result["actions"], action_gaps = _actions(schema, participant_map)
    result["outcomes"], outcome_gaps = _outcomes(schema, participant_map)
    result["modes"] = _modes(schema)
    result["provenance"] = deepcopy(_mapping(_mapping(schema.get("source")).get("provenance")))
    result["unresolved"] = _source_contract_gaps(schema) + action_gaps + outcome_gaps
    if not result["actions"]:
        result["unresolved"].append({
            "path": "/actions",
            "reason": "Rule Schema v1 contains no action that can be migrated without guessing semantics.",
            "required": True,
            "owner": "llm",
        })
    return result


def _topology(schema: Mapping[str, Any]) -> Dict[str, Any]:
    space = _mapping(schema.get("space"))
    dimensions = _mapping(space.get("dimensions"))
    source_kind = str(space.get("topology", "rectangular_grid"))
    kinds = {
        "rectangular_grid": "rect_grid", "hex_grid": "hex_grid",
        "graph": "graph", "continuous": "continuous",
    }
    anchors = {
        "cell_center": "cell", "grid_intersection": "vertex",
        "edge": "edge", "free": "free",
    }
    axes = []
    for name in ("x", "y", "z"):
        extent = dimensions.get(name)
        axes.append({
            "name": name,
            "extent": int(extent) if isinstance(extent, int) and not isinstance(extent, bool) and extent > 0 else 1,
            "boundary": str(_mapping(space.get("boundaries")).get(name, "bounded")),
        })
    adjacency = _mapping(space.get("adjacency"))
    neighborhoods = []
    if adjacency:
        neighborhoods.append({
            "id": "rule:neighborhood.source",
            "name": "Source neighborhood",
            "kind": str(adjacency.get("kind", "custom")),
            "diagonals": bool(adjacency.get("diagonals", False)),
        })
    return {
        "id": "rule:topology.board", "name": "Board",
        "kind": kinds.get(source_kind, "hybrid"),
        "anchor": anchors.get(str(space.get("coordinate_anchor")), "cell"),
        "axes": axes, "neighborhoods": neighborhoods,
    }


def _source_contract_gaps(schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    gaps = []
    space = _mapping(schema.get("space"))
    dimensions = _mapping(space.get("dimensions"))
    for axis in ("x", "y", "z"):
        value = dimensions.get(axis)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            gaps.append(_gap(
                "/topologies/0/axes/{0}/extent".format(("x", "y", "z").index(axis)),
                "Source Rule Schema does not prove the {0}-axis extent; migration placeholder 1 is not compile-authoritative.".format(axis.upper()),
            ))
    if space.get("coordinate_anchor") not in ("cell_center", "grid_intersection", "edge", "free"):
        gaps.append(_gap(
            "/topologies/0/anchor",
            "Source Rule Schema does not prove whether interaction targets cells, vertices, edges or free space.",
        ))
    if space.get("topology") not in ("rectangular_grid", "hex_grid", "graph", "continuous"):
        gaps.append(_gap(
            "/topologies/0/kind",
            "Source topology cannot be migrated without a designer/LLM decision.",
        ))
    flow = _mapping(schema.get("flow"))
    if flow.get("model") in (None, "unknown"):
        gaps.append(_gap("/flow/model", "Source flow model is unresolved."))
    if flow.get("model") == "tick_based":
        tick = flow.get("tick_rate")
        if isinstance(tick, bool) or not isinstance(tick, (int, float)) or tick <= 0:
            gaps.append(_gap("/flow/scheduler/tick_hz", "Tick-based source has no proven positive tick rate."))
    if _mapping(schema.get("randomness")).get("model") in ("stochastic", "mixed"):
        gaps.append(_gap(
            "/random_streams/0",
            "Source randomness needs a declared distribution plus recorded results or an approved deterministic seed policy.",
        ))
    return gaps


def _participants(schema: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    result = []
    mapping = {}
    kinds = {"ai": "agent", "human_or_ai": "human_or_agent"}
    for item in schema.get("participants", []) if isinstance(schema.get("participants"), list) else []:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("id", "participant"))
        target_id = "rule:participant.{0}".format(_slug(source_id))
        mapping[source_id] = target_id
        result.append({
            "id": target_id,
            "name": str(item.get("name", source_id)),
            "kind": kinds.get(str(item.get("kind", "human")), str(item.get("kind", "human"))),
        })
    return result, mapping


def _cell_state_type(schema: Mapping[str, Any]) -> Dict[str, Any]:
    board = _mapping(_mapping(schema.get("state")).get("board"))
    raw = board.get("cell_states", {})
    values = {}
    if isinstance(raw, Mapping):
        for value, name in raw.items():
            try:
                values[_slug(name)] = int(value)
            except (TypeError, ValueError):
                continue
    for entity in schema.get("entities", []) if isinstance(schema.get("entities"), list) else []:
        if not isinstance(entity, Mapping) or isinstance(entity.get("state_value"), bool) or not isinstance(entity.get("state_value"), int):
            continue
        name = _slug(entity.get("id", "state_{0}".format(entity["state_value"])))
        if name in values and values[name] != entity["state_value"]:
            name = "state_{0}".format(entity["state_value"])
        values[name] = entity["state_value"]
    if not values:
        values = {"empty": _empty_value(schema)}
    return {"id": "rule:type.cell_state", "name": "Cell State", "kind": "enum", "values": values}


def _entity_types(schema: Mapping[str, Any], participants: Mapping[str, str]) -> List[Dict[str, Any]]:
    result = []
    for item in schema.get("entities", []) if isinstance(schema.get("entities"), list) else []:
        if not isinstance(item, Mapping):
            continue
        legacy = deepcopy(dict(item))
        if legacy.get("owner") in participants:
            legacy["owner"] = participants[str(legacy["owner"])]
        result.append({
            "id": "rule:entity_type.{0}".format(_slug(item.get("id", "entity"))),
            "name": str(item.get("name", item.get("id", "Entity"))),
            "components": [{
                "name": "legacy_state_value",
                "type": "core:int",
                "default": {"op": "literal", "value": int(item["state_value"])},
            }] if isinstance(item.get("state_value"), int) else [],
            "legacy": legacy,
        })
    return result


def _actions(schema: Mapping[str, Any], participants: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    result = []
    gaps = []
    for index, item in enumerate(schema.get("actions", []) if isinstance(schema.get("actions"), list) else []):
        if not isinstance(item, Mapping):
            continue
        gap_path = "/actions/{0}".format(index)
        if item.get("executable") is False:
            gaps.append(_gap(gap_path, "Source action is not executable in Rule Schema v1."))
            continue
        precondition = _preconditions(item.get("preconditions"))
        effects = _effects(item.get("effects"))
        if precondition is None or effects is None:
            gaps.append(_gap(gap_path, "Action uses a v1 condition/effect that has no deterministic v2 migration."))
            continue
        target = _mapping(item.get("target"))
        parameters = []
        if target.get("kind", "coordinate") in ("coordinate", "cell", "site") or item.get("verb") in ("place", "select", "toggle", "reveal"):
            parameters.append({
                "name": "target", "type": "core:coord",
                "domain": {"op": "call", "function": "core:topology.sites", "args": [_literal("rule:topology.board")]},
            })
        result.append({
            "id": "rule:action.{0}".format(_slug(item.get("id", "action_{0}".format(index)))),
            "name": str(item.get("name", item.get("verb", "Action"))),
            "actor": _actor(item.get("actor"), participants),
            "parameters": parameters,
            "precondition": precondition,
            "effects": effects,
            "timing": {"phase": "rule:phase.input"},
            "encoding": {
                "kind": "parameter_product" if parameters else "finite_catalogue",
                "parameters": [parameter["name"] for parameter in parameters],
                "ordering": "lexicographic",
            },
            "source_ref": item.get("source_ref"),
        })
    return result, gaps


def _preconditions(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, list):
        return None
    expressions = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        operation = item.get("op")
        if operation == "in_bounds":
            expressions.append({"op": "call", "function": "core:topology.contains", "args": [_literal("rule:topology.board"), _param("target")]})
        elif operation == "cell_equals":
            expressions.append({"op": "call", "function": "core:grid.equals", "args": [_literal("rule:state.board_cell"), _param("target"), _value(item.get("value"))]})
        else:
            return None
    if not expressions:
        return _literal(True)
    return expressions[0] if len(expressions) == 1 else {"op": "and", "args": expressions}


def _effects(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, list) or not value:
        return None
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        operation = item.get("op")
        if operation == "set_cell":
            result.append({
                "op": "grid.set", "state": "rule:state.board_cell", "topology": "rule:topology.board",
                "coordinate": _param("target"), "value": _value(item.get("value")),
            })
        elif operation == "toggle_cell":
            result.append({
                "op": "grid.toggle", "state": "rule:state.board_cell", "topology": "rule:topology.board",
                "coordinate": _param("target"), "off": _value(item.get("off", 0)), "on": _value(item.get("on", 1)),
            })
        else:
            return None
    return result


def _outcomes(schema: Mapping[str, Any], participants: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    result = []
    gaps = []
    functions = {
        "line": "core:grid.has_line",
        "all_cells_not_equal": "core:grid.none_equal",
        "count_state_at_least": "core:grid.count_state_at_least",
        "state_at_coordinate": "core:grid.state_at_coordinate",
    }
    for index, item in enumerate(schema.get("outcomes", []) if isinstance(schema.get("outcomes"), list) else []):
        if not isinstance(item, Mapping):
            continue
        condition = _mapping(item.get("condition"))
        operation = condition.get("op")
        if item.get("executable") is False or operation not in functions:
            gaps.append(_gap("/outcomes/{0}".format(index), "Outcome has no deterministic v2 migration."))
            continue
        args = [_literal("rule:state.board_cell")]
        for key in ("value", "state", "count", "length", "coordinate"):
            if key in condition and condition[key] != "$matching_role_state":
                args.append(_value(condition[key]))
        source_result = _mapping(item.get("result"))
        target_result = {
            "status": str(source_result.get("status", "completed")),
            "terminal": bool(source_result.get("is_terminal", True)),
        }
        for key in ("winners", "losers"):
            if isinstance(source_result.get(key), list):
                target_result[key] = [
                    {
                        "op": "call", "function": "core:grid.line_owner",
                        "args": [_literal("rule:state.board_cell"), _value(condition.get("length"))],
                    }
                    if value == "$matching_role" and operation == "line"
                    else _literal(participants.get(str(value), value))
                    for value in source_result[key]
                ]
        result.append({
            "id": "rule:outcome.{0}".format(_slug(item.get("id", "outcome_{0}".format(index)))),
            "name": str(item.get("id", "Outcome")),
            "priority": int(item.get("priority", 0)),
            "condition": {"op": "call", "function": functions[operation], "args": args},
            "result": target_result,
            "source_ref": item.get("source_ref"),
        })
    return result, gaps


def _flow(schema: Mapping[str, Any], participants: Mapping[str, str]) -> Dict[str, Any]:
    source = _mapping(schema.get("flow"))
    model = str(source.get("model", "event_driven"))
    model_map = {"tick_based": "fixed_tick", "unknown": "event_driven"}
    target_model = model_map.get(model, model)
    clock = {
        "turn_based": "turn", "simultaneous": "turn", "fixed_tick": "fixed_tick",
        "real_time": "real_time", "hybrid": "event_queue", "event_driven": "event_queue",
    }.get(target_model, "event_queue")
    raw_tick = source.get("tick_rate")
    tick_hz = int(raw_tick) if isinstance(raw_tick, (int, float)) and not isinstance(raw_tick, bool) and raw_tick > 0 else None
    if clock == "fixed_tick" and tick_hz is None:
        clock = "event_queue"
        target_model = "event_driven"
    return {
        "model": target_model,
        "phases": [
            {"id": "rule:phase.input", "order": 100},
            {"id": "rule:phase.update", "order": 200},
            {"id": "rule:phase.outcome", "order": 300},
        ],
        "initial_phase": "rule:phase.input",
        "scheduler": {"clock": clock, "tick_hz": tick_hz, "ordering": "phase_priority_id"},
        "turn_order": [participants.get(str(value), str(value)) for value in source.get("turn_order", [])] if isinstance(source.get("turn_order"), list) else [],
    }


def _random_streams(schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    model = _mapping(schema.get("randomness")).get("model")
    if model not in ("stochastic", "mixed"):
        return []
    return [{
        "id": "rule:random.gameplay", "name": "Gameplay randomness",
        "algorithm": "cubeengine.recorded/1", "seed_policy": "recorded",
    }]


def _initial_effects(schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    setup = _mapping(schema.get("setup"))
    effects = []
    for item in setup.get("placements", []) if isinstance(setup.get("placements"), list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("coordinate"), Sequence):
            continue
        coordinate = list(item["coordinate"])
        if len(coordinate) == 2:
            coordinate.append(0)
        effects.append({
            "op": "grid.set", "state": "rule:state.board_cell", "topology": "rule:topology.board",
            "coordinate": _literal(coordinate), "value": _value(item.get("state", 0)),
        })
    return effects


def _goals(schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for index, item in enumerate(schema.get("goals", []) if isinstance(schema.get("goals"), list) else []):
        if isinstance(item, Mapping):
            result.append({
                "id": "rule:goal.{0}".format(_slug(item.get("id", "goal_{0}".format(index)))),
                "name": str(item.get("name", item.get("id", "Goal"))),
                "legacy": deepcopy(dict(item)),
            })
    return result


def _modes(schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for index, item in enumerate(schema.get("modes", []) if isinstance(schema.get("modes"), list) else []):
        if isinstance(item, Mapping):
            result.append({
                "id": "rule:mode.{0}".format(_slug(item.get("id", "mode_{0}".format(index)))),
                "name": str(item.get("name", item.get("id", "Mode"))),
                "overrides": [],
                "legacy_overrides": deepcopy(item.get("overrides", {})),
            })
    return result or [{"id": "rule:mode.default", "name": "Default", "overrides": []}]


def _actor(value: Any, participants: Mapping[str, str]) -> Dict[str, Any]:
    if value == "current_role":
        return {"op": "ref", "path": "flow.current_actor"}
    return _literal(participants.get(str(value), value))


def _value(value: Any) -> Dict[str, Any]:
    if value == "$actor_state":
        return {"op": "call", "function": "core:participant.state", "args": [{"op": "ref", "path": "flow.current_actor"}]}
    return _literal(value)


def _literal(value: Any) -> Dict[str, Any]:
    return {"op": "literal", "value": value}


def _param(name: str) -> Dict[str, Any]:
    return {"op": "param", "name": name}


def _empty_value(schema: Mapping[str, Any]) -> int:
    value = _mapping(_mapping(schema.get("state")).get("board")).get("empty_value", 0)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _information_model(schema: Mapping[str, Any]) -> str:
    value = str(_mapping(schema.get("state")).get("information", "perfect"))
    return value if value in ("perfect", "imperfect", "hidden", "mixed") else "mixed" if value == "partial" else "perfect"


def _determinism(schema: Mapping[str, Any]) -> str:
    model = _mapping(schema.get("randomness")).get("model")
    return "seeded" if model in ("stochastic", "mixed") else "deterministic"


def _gap(path: str, reason: str) -> Dict[str, Any]:
    return {"path": path, "reason": reason, "required": True, "owner": "llm"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    if not text:
        return "unnamed"
    if not text[0].isalpha():
        text = "id_" + text
    return text
