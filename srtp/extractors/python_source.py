"""Static Python source analysis for SRTP without importing or executing it."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..report import Diagnostic, ParseReport, SourceEvidence
from ..schema import new_rule_schema, set_path


MAX_AST_NODES = 20000

_X_NAMES = {"WIDTH", "BOARD_WIDTH", "GRID_WIDTH", "X_SIZE", "COLS", "COLUMNS", "NUM_COLS"}
_Y_NAMES = {"HEIGHT", "BOARD_HEIGHT", "GRID_HEIGHT", "Y_SIZE", "ROWS", "NUM_ROWS"}
_Z_NAMES = {"DEPTH", "BOARD_DEPTH", "GRID_DEPTH", "Z_SIZE", "LAYERS", "NUM_LAYERS"}
_SQUARE_NAMES = {"N", "SIZE", "BOARD_SIZE", "GRID_SIZE", "SIDE", "SIDE_LENGTH"}


def extract_python_source(source: str, source_name: str) -> ParseReport:
    report = ParseReport(new_rule_schema(_slug(Path(source_name).stem), Path(source_name).stem), source_path=source_name, source_format="python_ast")
    try:
        tree = ast.parse(source, filename=source_name, mode="exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        report.add_diagnostic(Diagnostic(
            "error", "python.syntax", "$", "Python source could not be parsed into an AST.",
            evidence=str(error), requires_llm=True,
        ))
        return report
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_AST_NODES:
        report.add_diagnostic(Diagnostic(
            "error", "python.complexity_limit", "$",
            "Source AST exceeds the Function 1 safety limit.",
            evidence="{0} nodes > {1}".format(node_count, MAX_AST_NODES),
            suggestion="Isolate the game-rule module or use Function 2.", requires_llm=True,
        ))
        return report

    constants = _top_level_constants(tree)
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    class_methods = {
        "{0}.{1}".format(cls.name, method.name): method
        for cls in tree.body if isinstance(cls, ast.ClassDef)
        for method in cls.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    all_functions = dict(functions)
    all_functions.update(class_methods)

    dimensions = _infer_dimensions(constants, all_functions)
    if dimensions is not None:
        x_size, y_size, z_size, detail, confidence, lines = dimensions
        set_path(report.schema, "space.dimensions", {"x": x_size, "y": y_size, "z": z_size})
        for axis in ("x", "y", "z"):
            report.add_evidence(
                "space.dimensions.{0}".format(axis),
                SourceEvidence("python_static", source_name, confidence, detail, lines),
            )
    else:
        report.add_diagnostic(Diagnostic(
            "error", "python.grid_unresolved", "space.dimensions",
            "No unique logical grid extent could be proven from constants, board construction or getBoardSize().",
            suggestion="Expose BOARD_WIDTH/BOARD_HEIGHT, a literal board shape, or use Function 2.",
            requires_llm=True,
        ))

    participants, entities = _infer_participants_entities(constants, all_functions)
    report.schema["participants"] = participants
    report.schema["entities"] = entities
    if participants:
        report.add_evidence("participants", SourceEvidence("python_static", source_name, 0.72, "player constants/API", _first_line(all_functions.values())))

    actions = _infer_actions(all_functions, constants)
    report.schema["actions"] = actions
    if actions:
        report.add_evidence("actions", SourceEvidence("python_function_signatures", source_name, 0.78, ", ".join(item["source_ref"] for item in actions)))
    outcomes = _infer_outcomes(all_functions, constants, dimensions)
    report.schema["outcomes"] = outcomes
    report.schema["goals"] = [
        {"id": "goal_{0}".format(item["id"]), "type": "terminal", "condition_ref": item["id"], "owner": "$matching_role"}
        for item in outcomes if item.get("result", {}).get("status") not in ("draw", "ongoing")
    ]
    if outcomes:
        report.add_evidence("outcomes", SourceEvidence("python_function_signatures", source_name, 0.7, ", ".join(item["source_ref"] for item in outcomes)))

    flow = _infer_flow(constants, all_functions, participants)
    flow_confidence = flow.pop("_confidence", 0.55)
    flow_detail = flow.pop("_detail", "function structure")
    report.schema["flow"].update(flow)
    report.add_evidence("flow.model", SourceEvidence("python_static", source_name, flow_confidence, flow_detail))
    _infer_randomness(report, tree, all_functions)
    _infer_anchor(report, constants, all_functions)

    report.schema["extensions"]["python"] = {
        "constants": {key: value for key, value in constants.items() if _json_scalar_or_container(value)},
        "functions": sorted(all_functions),
        "analysis": "AST only; source was not imported or executed.",
    }
    report.add_diagnostic(Diagnostic(
        "info", "python.not_executed", "$",
        "The Python file was analysed as syntax only and was never imported or executed.",
    ))
    return report


def _top_level_constants(tree: ast.Module) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value_node = node.value
            if value_node is None:
                continue
            try:
                value = _safe_eval(value_node, result, depth=0)
            except (ValueError, TypeError, OverflowError):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    result[target.id] = value
    return result


def _safe_eval(node: ast.AST, names: Mapping[str, Any], depth: int) -> Any:
    if depth > 12:
        raise ValueError("expression too deep")
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_safe_eval(item, names, depth + 1) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {
            _safe_eval(key, names, depth + 1): _safe_eval(value, names, depth + 1)
            for key, value in zip(node.keys, node.values) if key is not None
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _safe_eval(node.operand, names, depth + 1)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("not numeric")
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)):
        left = _safe_eval(node.left, names, depth + 1)
        right = _safe_eval(node.right, names, depth + 1)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise ValueError("not numeric")
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left // right
    raise ValueError("non-literal expression")


def _infer_dimensions(constants: Mapping[str, Any], functions: Mapping[str, ast.AST]) -> Optional[Tuple[int, int, int, str, float, Optional[int]]]:
    upper = {key.upper(): value for key, value in constants.items()}
    x_size = _named_positive(upper, _X_NAMES)
    y_size = _named_positive(upper, _Y_NAMES)
    z_size = _named_positive(upper, _Z_NAMES) or 1
    if x_size and y_size:
        return x_size[0], y_size[0], z_size[0] if isinstance(z_size, tuple) else z_size, "named width/height/depth constants", 0.98, x_size[1]
    square = _named_positive(upper, _SQUARE_NAMES)
    if square:
        dimensions_used = _max_subscript_rank(functions.values())
        if dimensions_used >= 3:
            return square[0], square[0], square[0], "square-size constant with 3-axis board indexing", 0.9, square[1]
        return square[0], square[0], 1, "square-size constant with 2D source plane", 0.9, square[1]
    for name, function in functions.items():
        if name.split(".")[-1].lower() in ("getboardsize", "get_board_size", "board_size"):
            returned = _literal_return(function, constants)
            if isinstance(returned, (list, tuple)) and len(returned) in (2, 3) and all(_positive_int(item) for item in returned):
                values = tuple(int(item) for item in returned)
                if len(values) == 2:
                    return values[1], values[0], 1, "getBoardSize literal return (rows, columns)", 0.96, getattr(function, "lineno", None)
                return values[0], values[1], values[2], "getBoardSize literal return", 0.94, getattr(function, "lineno", None)
    array_shape = _numpy_shape(functions.values(), constants)
    if array_shape:
        shape, line = array_shape
        if len(shape) == 2:
            return shape[1], shape[0], 1, "NumPy board constructor shape (rows, columns)", 0.9, line
        if len(shape) == 3:
            return shape[0], shape[1], shape[2], "NumPy 3D board constructor shape", 0.86, line
    comprehension_shape = _list_comprehension_shape(functions, constants)
    if comprehension_shape:
        x_size, y_size, line = comprehension_shape
        return x_size, y_size, 1, "nested board list-comprehension bounds", 0.86, line
    loop_shape = _axis_loop_bounds(functions.values(), constants)
    if loop_shape and "x" in loop_shape and "y" in loop_shape:
        return loop_shape["x"], loop_shape["y"], loop_shape.get("z", 1), "coordinate loop bounds", 0.76, None
    return None


def _infer_participants_entities(constants: Mapping[str, Any], functions: Mapping[str, ast.AST]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    player_count = None
    state_values: List[int] = []
    for key in ("NUM_PLAYERS", "PLAYER_COUNT", "PLAYERS"):
        value = constants.get(key)
        if _positive_int(value):
            player_count = int(value)
            break
    explicit_states = [
        int(value) for key, value in constants.items()
        if re.match(r"^(PLAYER|P)[_]?\d+$", key.upper())
        and isinstance(value, int) and not isinstance(value, bool) and value != 0
    ]
    if explicit_states:
        state_values = list(dict.fromkeys(explicit_states))
        player_count = player_count or len(state_values)
    function_tokens = {name.split(".")[-1].lower() for name in functions}
    if player_count is None and any(token in function_tokens for token in ("getcanonicalform", "getnextstate", "get_game_ended", "getgameended")):
        player_count = 2
    if _uses_signed_player_encoding(functions.values()):
        state_values = [1, -1]
        player_count = 2
    elif not state_values:
        state_values = _player_literals(functions)
        if len(state_values) >= 2:
            player_count = player_count or len(state_values)
    if player_count and len(state_values) < player_count:
        state_values = list(range(1, player_count + 1))
    participants = [
        {"id": "player_{0}".format(index + 1), "name": "Player {0}".format(index + 1), "kind": "human_or_ai", "symmetry_group": "players"}
        for index in range(player_count or 0)
    ]
    entities = [
        {
            "id": "player_{0}_mark".format(index + 1),
            "name": "Player {0} mark".format(index + 1),
            "kind": "piece",
            "owner": "player_{0}".format(index + 1),
            "state_value": state_values[index],
            "supply": {"model": "unlimited"},
        }
        for index in range(player_count or 0)
    ]
    return participants, entities


def _uses_signed_player_encoding(functions: Iterable[ast.AST]) -> bool:
    return any(
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Name)
        and node.operand.id.lower() in ("player", "current_player")
        for function in functions for node in ast.walk(function)
    )


def _player_literals(functions: Mapping[str, ast.AST]) -> List[int]:
    values = []
    for name, function in functions.items():
        token = name.split(".")[-1].lower()
        if "player" not in token and token not in ("game_status", "gamestatus"):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool) and node.value != 0:
                values.append(int(node.value))
    return sorted(set(values))


def _infer_actions(functions: Mapping[str, ast.AST], constants: Mapping[str, Any]) -> List[Dict[str, Any]]:
    names = {name.split(".")[-1].lower(): (name, node) for name, node in functions.items()}
    place_function = next((value for token, value in names.items() if token in ("set_cell", "place_piece", "place_mark", "make_move", "apply_move")), None)
    legal_function = next((value for token, value in names.items() if token in ("is_valid_move", "is_legal_move", "getvalidmoves", "get_valid_moves", "get_legal_moves", "legal_actions")), None)
    move_function = next((value for token, value in names.items() if token in ("move", "move_entity", "move_object", "move_player", "step", "update_position")), None)
    reveal_function = next((value for token, value in names.items() if token in ("reveal", "reveal_cell", "open_cell", "select_cell")), None)
    result = []
    if place_function or (legal_function and _contains_empty_cell_comparison(legal_function[1])):
        refs = [item[0] for item in (place_function, legal_function) if item is not None]
        executable = bool(place_function and legal_function and _contains_direct_cell_write(place_function[1]) and _contains_empty_cell_comparison(legal_function[1]))
        result.append({
            "id": "place_mark",
            "name": "Place mark",
            "actor": "current_role",
            "verb": "place",
            "target": {"kind": "coordinate", "anchor": "cell_center"},
            "parameters": [{"name": "target", "type": "coordinate"}],
            "preconditions": [{"op": "cell_equals", "at": "$target", "value": 0}],
            "effects": [{"op": "set_cell", "at": "$target", "value": "$actor_state"}],
            "timing": {"phase": "act"},
            "executable": executable,
            "source_ref": ",".join(refs),
        })
    if move_function:
        result.append({
            "id": "move_object", "name": "Move object", "actor": "current_role", "verb": "move",
            "target": {"kind": "coordinate", "anchor": "cell_center"},
            "parameters": [{"name": "source", "type": "coordinate"}, {"name": "target", "type": "coordinate"}],
            "preconditions": [], "effects": [], "timing": {"phase": "act"},
            "executable": False, "source_ref": move_function[0],
        })
    if reveal_function:
        result.append({
            "id": "reveal_cell", "name": "Reveal cell", "actor": "current_role", "verb": "reveal",
            "target": {"kind": "coordinate", "anchor": "cell_center"},
            "parameters": [{"name": "target", "type": "coordinate"}],
            "preconditions": [], "effects": [], "timing": {"phase": "act"},
            "executable": False, "source_ref": reveal_function[0],
        })
    return result


def _infer_outcomes(functions: Mapping[str, ast.AST], constants: Mapping[str, Any], dimensions: Any) -> List[Dict[str, Any]]:
    result = []
    names = {name.split(".")[-1].lower(): (name, node) for name, node in functions.items()}
    winner = next((value for token, value in names.items() if token in ("check_winner", "winner", "get_winner", "getgameended", "get_game_ended")), None)
    full = next((value for token, value in names.items() if token in ("is_full", "board_full", "is_draw")), None)
    if winner:
        length = next((constants.get(key) for key in ("WIN_LENGTH", "CONNECT_N", "LINE_LENGTH") if _positive_int(constants.get(key))), None)
        if length is None and dimensions is not None and dimensions[0] == dimensions[1]:
            length = dimensions[0]
        result.append({
            "id": "detected_win", "priority": 100,
            "condition": {"op": "line", "length": length, "state": "$matching_role_state", "directions": "grid_all"},
            "result": {"status": "win", "is_terminal": True, "winners": ["$matching_role"]},
            "executable": bool(_positive_int(length) and _looks_like_line_check(winner[1])),
            "source_ref": winner[0],
        })
    if full:
        result.append({
            "id": "detected_draw", "priority": 10,
            "condition": {"op": "all_cells_not_equal", "value": 0},
            "result": {"status": "draw", "is_terminal": True, "winners": []},
            "executable": _looks_like_full_check(full[1]), "source_ref": full[0],
        })
    return result


def _infer_flow(constants: Mapping[str, Any], functions: Mapping[str, ast.AST], participants: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    names = {name.split(".")[-1].lower() for name in functions}
    if any(name in names for name in ("update", "tick", "update_position")) or _positive_number(constants.get("TICK_RATE")):
        return {"model": "tick_based", "turn_order": [], "phases": [], "tick_rate": constants.get("TICK_RATE"), "_confidence": 0.76, "_detail": "update/tick function or rate"}
    if any(name in names for name in ("getnextstate", "get_next_state", "make_move", "apply_move", "is_valid_move")):
        return {"model": "turn_based", "turn_order": [item["id"] for item in participants], "phases": [], "tick_rate": None, "_confidence": 0.82, "_detail": "move/state-transition API"}
    return {"model": "unknown", "turn_order": [], "phases": [], "tick_rate": None, "_confidence": 0.3, "_detail": "no reliable temporal API"}


def _infer_randomness(report: ParseReport, tree: ast.AST, functions: Mapping[str, ast.AST]) -> None:
    random_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name.startswith("random.") or name.startswith("np.random.") or name in ("choice", "randint", "shuffle", "sample"):
            random_calls.append((name, getattr(node, "lineno", None)))
    if random_calls:
        report.schema["randomness"] = {
            "model": "stochastic",
            "events": [{"id": "source_random_event", "distribution": "unresolved", "source_calls": [name for name, _ in random_calls]}],
        }
        report.add_evidence("randomness", SourceEvidence("python_call_detection", report.source_path, 0.9, ", ".join(name for name, _ in random_calls), random_calls[0][1]))
    else:
        report.schema["randomness"] = {"model": "deterministic", "events": []}


def _infer_anchor(report: ParseReport, constants: Mapping[str, Any], functions: Mapping[str, ast.AST]) -> None:
    raw = next((constants[key] for key in constants if key.upper() in ("COORDINATE_ANCHOR", "PLACEMENT_ANCHOR", "SITE_TYPE")), None)
    if isinstance(raw, str):
        token = raw.lower()
        anchor = "grid_intersection" if any(word in token for word in ("intersection", "vertex", "point")) else "cell_center" if any(word in token for word in ("cell", "tile", "square")) else "unknown"
        set_path(report.schema, "space.coordinate_anchor", anchor)
        report.add_evidence("space.coordinate_anchor", SourceEvidence("python_constant", report.source_path, 0.98, str(raw)))
    elif report.schema.get("actions"):
        set_path(report.schema, "space.coordinate_anchor", "unknown")


def _named_positive(values: Mapping[str, Any], names: set) -> Optional[Tuple[int, Optional[int]]]:
    for name in names:
        if _positive_int(values.get(name)):
            return int(values[name]), None
    return None


def _literal_return(function: ast.AST, constants: Mapping[str, Any]) -> Any:
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and node.value is not None:
            try:
                return _safe_eval(node.value, constants, 0)
            except (ValueError, TypeError, OverflowError):
                continue
    return None


def _numpy_shape(functions: Iterable[ast.AST], constants: Mapping[str, Any]) -> Optional[Tuple[Tuple[int, ...], Optional[int]]]:
    for function in functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or _call_name(node.func) not in ("np.zeros", "numpy.zeros", "np.empty", "numpy.empty", "np.full", "numpy.full") or not node.args:
                continue
            try:
                value = _safe_eval(node.args[0], constants, 0)
            except (ValueError, TypeError, OverflowError):
                continue
            if isinstance(value, (list, tuple)) and len(value) in (2, 3) and all(_positive_int(item) for item in value):
                return tuple(int(item) for item in value), getattr(node, "lineno", None)
    return None


def _list_comprehension_shape(
    functions: Mapping[str, ast.AST],
    constants: Mapping[str, Any],
) -> Optional[Tuple[int, int, Optional[int]]]:
    candidates = [
        function for name, function in functions.items()
        if name.split(".")[-1].lower() in ("create_board", "createboard", "getinitboard", "initial_board")
    ]
    for function in candidates:
        for node in ast.walk(function):
            if not isinstance(node, ast.ListComp) or len(node.generators) != 1:
                continue
            outer = _comprehension_bound(node, constants)
            inner_node = node.elt
            if outer is None or not isinstance(inner_node, ast.ListComp) or len(inner_node.generators) != 1:
                continue
            inner = _comprehension_bound(inner_node, constants)
            if inner is not None:
                return inner, outer, getattr(node, "lineno", None)
    return None


def _comprehension_bound(node: ast.ListComp, constants: Mapping[str, Any]) -> Optional[int]:
    generator = node.generators[0]
    if not isinstance(generator.iter, ast.Call) or _call_name(generator.iter.func) != "range":
        return None
    return _range_count(generator.iter, constants)


def _axis_loop_bounds(functions: Iterable[ast.AST], constants: Mapping[str, Any]) -> Dict[str, int]:
    candidates: Dict[str, set] = {"x": set(), "y": set(), "z": set()}
    for function in functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name) or not isinstance(node.iter, ast.Call):
                continue
            if _call_name(node.iter.func) != "range":
                continue
            target = node.target.id.lower()
            axis = "x" if target in ("x", "col", "column") else "y" if target in ("y", "row") else "z" if target in ("z", "layer", "depth") else None
            count = _range_count(node.iter, constants)
            if axis and count:
                candidates[axis].add(count)
    return {
        axis: next(iter(values))
        for axis, values in candidates.items()
        if len(values) == 1
    }


def _range_count(call: ast.Call, constants: Mapping[str, Any]) -> Optional[int]:
    try:
        args = [_safe_eval(item, constants, 0) for item in call.args]
    except (ValueError, TypeError, OverflowError):
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in args):
        return None
    if len(args) == 1 and args[0] > 0:
        return int(args[0])
    if len(args) in (2, 3):
        start, stop = int(args[0]), int(args[1])
        step = int(args[2]) if len(args) == 3 else 1
        if step == 0:
            return None
        return len(range(start, stop, step))
    return None


def _max_subscript_rank(functions: Iterable[ast.AST]) -> int:
    maximum = 0
    for function in functions:
        for node in ast.walk(function):
            rank = 0
            current = node
            while isinstance(current, ast.Subscript):
                rank += 1
                current = current.value
            maximum = max(maximum, rank)
    return maximum


def _contains_empty_cell_comparison(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Compare) and any(isinstance(operator, ast.Eq) for operator in node.ops):
            values = [node.left] + list(node.comparators)
            if any(isinstance(value, ast.Constant) and value.value == 0 for value in values) and any(isinstance(value, ast.Subscript) for value in values):
                return True
    return False


def _contains_direct_cell_write(function: ast.AST) -> bool:
    return any(
        isinstance(node, (ast.Assign, ast.AugAssign))
        and any(isinstance(target, ast.Subscript) for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))
        for node in ast.walk(function)
    )


def _looks_like_line_check(function: ast.AST) -> bool:
    calls = {_call_name(node.func).lower() for node in ast.walk(function) if isinstance(node, ast.Call)}
    has_all = "all" in calls or any(isinstance(node, ast.Compare) and len(node.ops) >= 1 for node in ast.walk(function))
    has_iteration = any(isinstance(node, (ast.For, ast.GeneratorExp, ast.ListComp)) for node in ast.walk(function))
    return has_all and has_iteration


def _looks_like_full_check(function: ast.AST) -> bool:
    return _contains_empty_cell_comparison(function) or any(
        isinstance(node, ast.Constant) and node.value == 0 for node in ast.walk(function)
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return "{0}.{1}".format(prefix, node.attr) if prefix else node.attr
    return ""


def _first_line(nodes: Iterable[ast.AST]) -> Optional[int]:
    return next((getattr(node, "lineno", None) for node in nodes), None)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _json_scalar_or_container(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_json_scalar_or_container(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _json_scalar_or_container(item) for key, item in value.items())
    return False


def _slug(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "unidentified").strip().lower()).strip("_")
    return token or "unidentified"
