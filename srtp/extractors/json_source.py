"""JSON-to-Rule-Schema extraction with explicit alias evidence."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..report import Diagnostic, ParseReport, SourceEvidence
from ..schema import RULE_SCHEMA_VERSION, new_rule_schema, normalize_rule_schema, set_path


_VERB_ALIASES = {
    "add": "place", "drop": "place", "put": "place", "place_mark": "place",
    "choose": "select", "click": "select", "mark": "select",
    "uncover": "reveal", "open": "reveal",
    "walk": "move", "slide": "move", "translate": "move",
    "delete": "remove", "destroy": "remove", "fire": "shoot",
}


def extract_json_source(value: Mapping[str, Any], source_name: str) -> ParseReport:
    if not isinstance(value, Mapping):
        report = ParseReport(new_rule_schema(Path(source_name).stem), source_path=source_name, source_format="json")
        report.add_diagnostic(Diagnostic("error", "json.root_type", "$", "JSON root must be an object."))
        return report
    if value.get("schema_version") == RULE_SCHEMA_VERSION:
        schema = normalize_rule_schema(value)
        report = ParseReport(schema, source_path=source_name, source_format="canonical_json")
        _record_explicit_tree(report, value)
        return report

    game_name = _first(value, "display_name", "title", "name", "game_name", "game_id")
    game_id = _slug(_first(value, "game_id", "id", "slug", default=game_name or Path(source_name).stem))
    schema = new_rule_schema(game_id, str(game_name or game_id))
    report = ParseReport(schema, source_path=source_name, source_format="json")
    _evidence(report, "game.id", "json_alias", source_name, 1.0, "game id/name alias")
    if game_name:
        _evidence(report, "game.name", "json_alias", source_name, 1.0, "title/name alias")
    description = _first(value, "description", "summary", "instructions")
    if isinstance(description, str):
        set_path(schema, "game.description", description)
        _evidence(report, "game.description", "explicit", source_name, 1.0, "description")

    consumed = {
        "schema_version", "game_id", "id", "slug", "display_name", "title", "name", "game_name",
        "description", "summary", "instructions", "dimensions", "board", "grid", "space", "map",
        "level", "initial_board", "players", "participants", "roles", "single_player", "entities",
        "pieces", "sprites", "objects", "actions", "moves", "controls", "win_conditions",
        "draw_conditions", "outcomes", "termination", "terminal_conditions", "turn_order",
        "turn_based", "realtime", "real_time", "tick_rate", "flow", "randomness", "chance",
        "modes", "variants", "state_legend", "initial_state", "setup", "coordinate_anchor",
        "placement", "anchor",
    }

    dimensions = _extract_dimensions(value)
    if dimensions is not None:
        x_size, y_size, z_size, path, method = dimensions
        set_path(schema, "space.dimensions", {"x": x_size, "y": y_size, "z": z_size})
        for axis in ("x", "y", "z"):
            _evidence(report, "space.dimensions.{0}".format(axis), method, source_name, 0.98, path)
    else:
        array_shape = _find_grid_array(value)
        if array_shape is not None:
            x_size, y_size, path = array_shape
            set_path(schema, "space.dimensions", {"x": x_size, "y": y_size, "z": 1})
            _evidence(report, "space.dimensions.x", "array_shape", source_name, 0.92, path)
            _evidence(report, "space.dimensions.y", "array_shape", source_name, 0.92, path)
            _evidence(report, "space.dimensions.z", "2d_plane", source_name, 1.0, "2D source defaults to one layer")

    anchor = _extract_anchor(value)
    if anchor is not None:
        set_path(schema, "space.coordinate_anchor", anchor[0])
        _evidence(report, "space.coordinate_anchor", "explicit_alias", source_name, 0.98, anchor[1])

    participants = _extract_participants(value)
    schema["participants"] = participants
    if participants:
        _evidence(report, "participants", "json_alias", source_name, 0.95, "players/roles")

    entities = _extract_entities(value, participants)
    schema["entities"] = entities
    if entities:
        _evidence(report, "entities", "json_alias", source_name, 0.9, "pieces/entities/sprites")

    state_legend = value.get("state_legend")
    if isinstance(state_legend, Mapping):
        schema["state"]["board"]["cell_states"] = {str(key): str(label) for key, label in state_legend.items()}
        _evidence(report, "state.board.cell_states", "explicit", source_name, 1.0, "state_legend")

    flow = _extract_flow(value, participants)
    schema["flow"].update(flow)
    if flow.get("model") != "unknown":
        _evidence(report, "flow.model", "json_alias", source_name, 0.95, "flow/turn/realtime fields")

    raw_actions = _first(value, "actions", "moves", "controls", default=[])
    schema["actions"] = _extract_actions(raw_actions, participants, schema["flow"], schema["space"])
    if schema["actions"]:
        _evidence(report, "actions", "json_rule_mapping", source_name, 0.88, "actions/moves/controls")

    schema["outcomes"] = _extract_outcomes(value)
    if schema["outcomes"]:
        _evidence(report, "outcomes", "json_rule_mapping", source_name, 0.86, "win/draw/termination fields")
    schema["goals"] = _goals_from_outcomes(schema["outcomes"])

    randomness = _extract_randomness(value)
    schema["randomness"] = randomness
    if randomness["model"] != "deterministic":
        _evidence(report, "randomness", "json_alias", source_name, 0.9, "randomness/chance")

    raw_modes = _first(value, "modes", "variants")
    if raw_modes is not None:
        schema["modes"] = _normalise_modes(raw_modes)
        _evidence(report, "modes", "json_alias", source_name, 0.9, "modes/variants")

    initial_state = value.get("initial_state")
    if isinstance(initial_state, list):
        schema["setup"]["placements"] = deepcopy(initial_state)
        _evidence(report, "setup.placements", "explicit_alias", source_name, 1.0, "initial_state")
    if isinstance(value.get("setup"), Mapping):
        schema["setup"].update(deepcopy(dict(value["setup"])))

    schema["extensions"]["unmapped_source_fields"] = {
        key: deepcopy(item) for key, item in value.items() if key not in consumed
    }
    if schema["extensions"]["unmapped_source_fields"]:
        report.add_diagnostic(Diagnostic(
            "info", "json.unmapped_fields", "extensions.unmapped_source_fields",
            "Unrecognised fields were preserved instead of discarded.",
            evidence=", ".join(sorted(schema["extensions"]["unmapped_source_fields"])),
        ))
    return report


def _extract_dimensions(value: Mapping[str, Any]) -> Optional[Tuple[int, int, int, str, str]]:
    candidates = [
        (value.get("dimensions"), "dimensions"),
        (value.get("space"), "space"),
        (value.get("board"), "board"),
        (value.get("grid"), "grid"),
        (value, "$"),
    ]
    for candidate, path in candidates:
        pixel_result = _pixel_dimensions(candidate)
        if pixel_result is not None:
            return pixel_result[0], pixel_result[1], pixel_result[2], path, "derived_pixel_ratio"
        result = _dimensions_from_value(candidate)
        if result is not None:
            return result[0], result[1], result[2], path, "explicit_dimensions"
    return None


def _dimensions_from_value(value: Any) -> Optional[Tuple[int, int, int]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) in (2, 3) and all(_positive_int(item) for item in value):
            return int(value[0]), int(value[1]), int(value[2]) if len(value) == 3 else 1
    if not isinstance(value, Mapping):
        return None
    nested = value.get("dimensions") or value.get("size") or value.get("shape")
    if nested is not None and nested is not value:
        found = _dimensions_from_value(nested)
        if found is not None:
            return found
    x_size = _first(value, "x", "width", "columns", "cols", "board_width", "grid_width")
    y_size = _first(value, "y", "height", "rows", "board_height", "grid_height")
    z_size = _first(value, "z", "depth", "layers", "board_depth", "grid_depth", default=1)
    if _positive_int(x_size) and _positive_int(y_size) and _positive_int(z_size):
        return int(x_size), int(y_size), int(z_size)
    square = _first(value, "n", "side", "side_length")
    if _positive_int(square):
        return int(square), int(square), int(z_size) if _positive_int(z_size) else 1
    return None


def _pixel_dimensions(value: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(value, Mapping):
        return None
    unit = str(_first(value, "unit", "measurement", "dimension_unit", default="")).lower()
    cell_size = _first(value, "cell_size", "tile_size", "square_size", "grid_unit")
    if unit not in ("pixel", "pixels", "px") or cell_size is None:
        return None
    width = _first(value, "width", "pixel_width", "board_pixel_width", "playfield_width")
    height = _first(value, "height", "pixel_height", "board_pixel_height", "playfield_height")
    if isinstance(cell_size, Mapping):
        x_unit = _first(cell_size, "x", "width")
        y_unit = _first(cell_size, "y", "height")
    else:
        x_unit = y_unit = cell_size
    if not all(_positive_int(item) for item in (width, height, x_unit, y_unit)):
        return None
    if width % x_unit or height % y_unit:
        return None
    return int(width // x_unit), int(height // y_unit), 1


def _find_grid_array(value: Mapping[str, Any]) -> Optional[Tuple[int, int, str]]:
    for key in ("board", "grid", "map", "level", "initial_board"):
        candidate = value.get(key)
        shape = _rectangular_2d_shape(candidate)
        if shape is not None:
            return shape[0], shape[1], key
        if isinstance(candidate, Mapping):
            for child_key in ("cells", "data", "layout", "initial"):
                shape = _rectangular_2d_shape(candidate.get(child_key))
                if shape is not None:
                    return shape[0], shape[1], "{0}.{1}".format(key, child_key)
    return None


def _rectangular_2d_shape(value: Any) -> Optional[Tuple[int, int]]:
    if isinstance(value, list) and value and all(isinstance(row, str) for row in value):
        widths = {len(row) for row in value}
        if len(widths) == 1 and next(iter(widths)) > 0:
            return next(iter(widths)), len(value)
    if not isinstance(value, list) or not value or not all(isinstance(row, list) for row in value):
        return None
    widths = {len(row) for row in value}
    if len(widths) != 1 or next(iter(widths)) <= 0:
        return None
    return next(iter(widths)), len(value)


def _extract_anchor(value: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    for container, prefix in ((value, "$"), (value.get("board"), "board"), (value.get("grid"), "grid"), (value.get("space"), "space")):
        if not isinstance(container, Mapping):
            continue
        raw = _first(container, "coordinate_anchor", "anchor", "placement", "site_type")
        if isinstance(raw, str):
            token = raw.lower().replace("-", "_").replace(" ", "_")
            if any(word in token for word in ("intersection", "vertex", "point")):
                return "grid_intersection", "{0}.{1}".format(prefix, "anchor")
            if "edge" in token or "line" in token:
                return "edge", "{0}.{1}".format(prefix, "anchor")
            if any(word in token for word in ("cell", "square", "tile", "center", "centre")):
                return "cell_center", "{0}.{1}".format(prefix, "anchor")
        if container.get("intersections") is True:
            return "grid_intersection", "{0}.intersections".format(prefix)
    return None


def _extract_participants(value: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw = _first(value, "participants", "players", "roles")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return [
            {"id": "player_{0}".format(index + 1), "name": "Player {0}".format(index + 1), "kind": "human_or_ai", "symmetry_group": "players"}
            for index in range(raw)
        ]
    result: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, str):
                result.append({"id": _slug(item), "name": item, "kind": "human_or_ai"})
            elif isinstance(item, Mapping):
                identifier = _slug(_first(item, "id", "role", "name", default="player_{0}".format(index + 1)))
                kind = str(_first(item, "kind", "controller", "type", default="human_or_ai"))
                if kind not in ("human", "ai", "human_or_ai", "system", "chance"):
                    kind = "human_or_ai"
                participant = {"id": identifier, "name": str(item.get("name", identifier)), "kind": kind}
                if item.get("symmetry_group") is not None:
                    participant["symmetry_group"] = str(item["symmetry_group"])
                result.append(participant)
    elif value.get("single_player") is True:
        result.append({"id": "player", "name": "Player", "kind": "human"})
    return result


def _extract_entities(value: Mapping[str, Any], participants: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    raw = _first(value, "entities", "pieces", "sprites", "objects")
    result: List[Dict[str, Any]] = []
    if isinstance(raw, Mapping):
        raw = [{"id": key, **(dict(item) if isinstance(item, Mapping) else {"name": str(item)})} for key, item in raw.items()]
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, str):
                result.append({"id": _slug(item), "name": item, "kind": "piece", "owner": "none", "supply": {"model": "unknown"}})
            elif isinstance(item, Mapping):
                identifier = _slug(_first(item, "id", "name", "type", default="entity_{0}".format(index + 1)))
                kind = str(_first(item, "kind", "category", "type", default="piece")).lower()
                if kind not in ("piece", "avatar", "token", "terrain", "cell_marker", "projectile", "collectible", "obstacle", "region", "system_object"):
                    kind = "unknown"
                entity: Dict[str, Any] = {
                    "id": identifier,
                    "name": str(item.get("name", identifier)),
                    "kind": kind,
                    "owner": item.get("owner", "none"),
                    "supply": deepcopy(item.get("supply", {"model": "unknown"})),
                }
                if _positive_int(item.get("state")) or item.get("state") == 0:
                    entity["state_value"] = int(item["state"])
                result.append(entity)
    if not result:
        raw_players = value.get("players")
        if isinstance(raw_players, list):
            for index, player in enumerate(raw_players):
                if not isinstance(player, Mapping) or not isinstance(player.get("piece"), Mapping):
                    continue
                role = participants[index]["id"] if index < len(participants) else "none"
                piece = player["piece"]
                result.append({
                    "id": _slug(piece.get("id", "{0}_piece".format(role))),
                    "name": str(piece.get("name", "{0} piece".format(role))),
                    "kind": "piece",
                    "owner": role,
                    "state_value": int(piece.get("state", index + 1)),
                    "supply": deepcopy(piece.get("supply", {"model": "unlimited"})),
                })
    return result


def _extract_flow(value: Mapping[str, Any], participants: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    flow = value.get("flow")
    result: Dict[str, Any] = dict(flow) if isinstance(flow, Mapping) else {}
    if value.get("real_time") is True or value.get("realtime") is True:
        result["model"] = "real_time"
    elif value.get("turn_based") is True or value.get("turn_order") is not None:
        result["model"] = "turn_based"
    if value.get("tick_rate") is not None:
        result["tick_rate"] = value["tick_rate"]
        if result.get("model") in (None, "unknown"):
            result["model"] = "tick_based"
    result.setdefault("model", "unknown")
    order = value.get("turn_order", result.get("turn_order"))
    if isinstance(order, list):
        result["turn_order"] = [_slug(item.get("id", item.get("name"))) if isinstance(item, Mapping) else _slug(item) for item in order]
    elif result["model"] == "turn_based" and participants:
        result["turn_order"] = [str(item["id"]) for item in participants]
    result.setdefault("turn_order", [])
    result.setdefault("phases", [])
    result.setdefault("tick_rate", None)
    return result


def _extract_actions(raw: Any, participants: Sequence[Mapping[str, Any]], flow: Mapping[str, Any], space: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(raw, Mapping):
        raw = [{"id": key, **(dict(item) if isinstance(item, Mapping) else {"verb": item})} for key, item in raw.items()]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    result = []
    for index, item in enumerate(raw):
        source = {"id": str(item), "verb": str(item)} if isinstance(item, str) else dict(item) if isinstance(item, Mapping) else {}
        raw_verb = str(_first(source, "verb", "type", "kind", "name", default="unknown")).lower().replace("-", "_").replace(" ", "_")
        verb = _VERB_ALIASES.get(raw_verb, raw_verb)
        if verb not in ("place", "move", "select", "reveal", "toggle", "rotate", "transform", "spawn", "remove", "capture", "swap", "push", "shoot", "control", "pass", "resolve", "tick"):
            verb = "unknown"
        action_id = _slug(source.get("id", source.get("name", "{0}_{1}".format(verb, index + 1))))
        actor = source.get("actor", "current_role" if flow.get("model") == "turn_based" else (participants[0]["id"] if len(participants) == 1 else "any"))
        anchor = source.get("anchor", space.get("coordinate_anchor", "unknown"))
        action: Dict[str, Any] = {
            "id": action_id,
            "name": str(source.get("name", action_id)),
            "actor": actor,
            "verb": verb,
            "target": deepcopy(source.get("target", {"kind": "coordinate", "anchor": anchor})),
            "parameters": deepcopy(source.get("parameters", [{"name": "target", "type": "coordinate"}])),
            "preconditions": deepcopy(source.get("preconditions", [])),
            "effects": deepcopy(source.get("effects", [])),
            "timing": deepcopy(source.get("timing", {"phase": "act"})),
            "executable": bool(source.get("executable", True)),
        }
        empty_required = source.get("requires_empty") is True or str(source.get("on", "")).lower() in ("empty", "empty_cell", "vacant")
        if empty_required and not action["preconditions"]:
            action["preconditions"] = [{"op": "cell_equals", "at": "$target", "value": 0}]
        if not action["effects"]:
            if verb == "place":
                action["effects"] = [{"op": "set_cell", "at": "$target", "value": "$actor_state"}]
            elif verb in ("select", "toggle", "reveal"):
                action["effects"] = [{"op": "toggle_cell", "at": "$target", "off": 0, "on": int(source.get("selected_state", 1))}]
            else:
                action["executable"] = False
        result.append(action)
    return result


def _extract_outcomes(value: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    groups = [
        (_first(value, "outcomes", "termination", "terminal_conditions", "win_conditions"), "win"),
        (value.get("draw_conditions"), "draw"),
    ]
    for raw, default_status in groups:
        if isinstance(raw, Mapping):
            raw = [raw]
        if isinstance(raw, str):
            raw = [{"type": raw}]
        if not isinstance(raw, list):
            continue
        for index, item in enumerate(raw):
            source = {"type": item} if isinstance(item, str) else dict(item) if isinstance(item, Mapping) else {}
            raw_type = str(_first(source, "type", "condition", "kind", default="unknown")).lower().replace("-", "_").replace(" ", "_")
            status = str(source.get("status", default_status))
            condition: Dict[str, Any]
            executable = bool(source.get("executable", True))
            if raw_type in ("line", "connect", "n_in_row", "in_a_row", "alignment"):
                length = source.get("length", source.get("count", source.get("n")))
                condition = {"op": "line", "length": length, "state": "$matching_role_state", "directions": source.get("directions", "grid_all")}
                if not _positive_int(length):
                    executable = False
            elif raw_type in ("board_full", "full", "no_empty_cells", "draw"):
                condition = {"op": "all_cells_not_equal", "value": int(source.get("empty_state", 0))}
                status = str(source.get("status", "draw"))
            elif isinstance(source.get("condition"), Mapping):
                condition = deepcopy(dict(source["condition"]))
            else:
                condition = {"op": "source_defined", "source": deepcopy(source)}
                executable = False
            result_value = deepcopy(source.get("result", {})) if isinstance(source.get("result"), Mapping) else {}
            result_value.setdefault("status", status)
            result_value.setdefault("is_terminal", bool(source.get("is_terminal", True)))
            if "winners" not in result_value and status not in ("draw", "loss", "timeout"):
                result_value["winners"] = [source.get("winner", "$matching_role")]
            result.append({
                "id": _slug(source.get("id", "{0}_{1}".format(status, len(result) + 1))),
                "priority": int(source.get("priority", 100 if status not in ("draw", "ongoing") else 10)),
                "condition": condition,
                "result": result_value,
                "executable": executable,
            })
    return result


def _goals_from_outcomes(outcomes: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": "goal_{0}".format(item["id"]),
            "type": "terminal" if item.get("result", {}).get("is_terminal") else "intermediate",
            "condition_ref": item["id"],
            "owner": "$matching_role",
        }
        for item in outcomes
        if item.get("result", {}).get("status") not in ("draw", "loss", "timeout")
    ]


def _extract_randomness(value: Mapping[str, Any]) -> Dict[str, Any]:
    raw = _first(value, "randomness", "chance")
    if raw is None:
        return {"model": "deterministic", "events": []}
    if isinstance(raw, bool):
        return {"model": "stochastic" if raw else "deterministic", "events": []}
    if isinstance(raw, Mapping):
        model = raw.get("model", "stochastic")
        return {"model": model, "events": deepcopy(raw.get("events", raw.get("outcomes", [])))}
    if isinstance(raw, list):
        return {"model": "stochastic", "events": deepcopy(raw)}
    return {"model": "unknown", "events": []}


def _normalise_modes(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, Mapping):
        raw = [{"id": key, "overrides": item if isinstance(item, Mapping) else {}} for key, item in raw.items()]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return [{"id": "default", "name": "Default", "overrides": {}}]
    result = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            result.append({"id": _slug(item), "name": item, "overrides": {}})
        elif isinstance(item, Mapping):
            identifier = _slug(item.get("id", item.get("name", "mode_{0}".format(index + 1))))
            result.append({"id": identifier, "name": str(item.get("name", identifier)), "overrides": deepcopy(item.get("overrides", {}))})
    return result or [{"id": "default", "name": "Default", "overrides": {}}]


def _record_explicit_tree(report: ParseReport, value: Mapping[str, Any], prefix: str = "") -> None:
    for key, item in value.items():
        path = "{0}.{1}".format(prefix, key) if prefix else str(key)
        if isinstance(item, Mapping):
            _record_explicit_tree(report, item, path)
        else:
            _evidence(report, path, "canonical_explicit", report.source_path, 1.0, path)


def _evidence(report: ParseReport, path: str, method: str, source: str, confidence: float, detail: str) -> None:
    report.add_evidence(path, SourceEvidence(method, source, confidence, detail))


def _first(value: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return default


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _slug(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "unidentified").strip().lower()).strip("_")
    return token or "unidentified"
