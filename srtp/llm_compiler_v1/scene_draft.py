"""Engine-drafted Source Scene for grid games, built only from measured source facts.

The draft uses what the engine already verified: the per-cell drawings the
source makes and the state values they are drawn for (``player == 1``), the
cell frame and pitch, the window size and background fill, and the text the
source renders with its font size, colour and position. HUD result text reads
the Rule outcome (``flow.terminal``/``outcome``/``winner``), the turn label the
state that holds whose turn it is. Nothing is guessed from game names: when a
state value has no source drawing, axes are transposed, or the window/grid is
not measurable, no draft is produced (reasons are returned) and the model
authors the Scene as before. A draft is only offered after it passes every
Scene gate; the model then reviews it against the source.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PIECE_LIFT = 0.12  # local +z = toward the camera after the root's 180-degree turn


@dataclass
class SceneDraft:
    definition: Optional[Dict[str, Any]]
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


# ------------------------------------------------------------------ source measurements

def _constants(tree: ast.AST) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for statement in getattr(tree, "body", []):
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            try:
                values[statement.targets[0].id] = _number(statement.value, values)
            except ValueError:
                try:
                    values[statement.targets[0].id] = ast.literal_eval(statement.value)
                except (ValueError, TypeError, SyntaxError):
                    pass
    return values


def _number(node: ast.AST, names: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and isinstance(names.get(node.id), (int, float)):
        return names[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_number(node.operand, names)
    if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Div):
        left, right = _number(node.left, names), _number(node.right, names)
        return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
                ast.FloorDiv: lambda: left // right, ast.Div: lambda: left / right}[type(node.op)]()
    raise ValueError("not a measurable number")


def _rgba(node: ast.AST, names: Mapping[str, Any]) -> Optional[List[float]]:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        value = names.get(node.id) if isinstance(node, ast.Name) else None
    if isinstance(value, (tuple, list)) and len(value) in (3, 4) and all(isinstance(v, (int, float)) for v in value):
        channels = [float(v) / 255.0 for v in value[:3]] + [float(value[3]) / 255.0 if len(value) == 4 else 1.0]
        return [round(v, 6) for v in channels]
    return None


def measure_source(files: Mapping[str, Path]) -> Dict[str, Any]:
    """Window size, background fill and rendered texts (with font size, colour and position)."""
    result: Dict[str, Any] = {"window": None, "fill": None, "texts": []}
    for relative, path in files.items():
        if not str(relative).endswith(".py"):
            continue
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        names = _constants(tree)
        fonts: Dict[str, float] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Call):
                function = ast.unparse(node.value.func)
                if function.endswith(("font.Font", "font.SysFont")) and len(node.value.args) >= 2:
                    try:
                        fonts[node.targets[0].id] = float(_number(node.value.args[1], names))
                    except ValueError:
                        pass
        for function_node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            assigned: Dict[str, ast.AST] = {}
            body = function_node.body if hasattr(function_node, "body") else []
            for node in ast.walk(function_node) if function_node is not tree else ():
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    assigned[node.targets[0].id] = node.value
            for node in ast.walk(function_node) if function_node is not tree else body:
                if not isinstance(node, ast.Call):
                    continue
                function = ast.unparse(node.func)
                if function.endswith("display.set_mode") and node.args and isinstance(node.args[0], (ast.Tuple, ast.List)):
                    try:
                        result["window"] = [float(_number(item, names)) for item in node.args[0].elts[:2]]
                        result["window_source"] = "{0}:{1}-{1}@{2}".format(relative, node.lineno, digest)
                    except ValueError:
                        pass
                elif function.endswith(".fill") and node.args and result["fill"] is None:
                    color = _rgba(node.args[0], names)
                    if color:
                        result["fill"] = color
                        result["fill_source"] = "{0}:{1}-{1}@{2}".format(relative, node.lineno, digest)
                elif function.endswith(".blit") and len(node.args) >= 2:
                    surface = node.args[0]
                    if isinstance(surface, ast.Name) and isinstance(assigned.get(surface.id), ast.Call):
                        surface = assigned[surface.id]
                    if not (isinstance(surface, ast.Call) and ast.unparse(surface.func).endswith(".render")):
                        continue
                    font_name = ast.unparse(surface.func).rsplit(".", 1)[0]
                    try:
                        position = [float(_number(item, names)) for item in node.args[1].elts[:2]] \
                            if isinstance(node.args[1], (ast.Tuple, ast.List)) else None
                    except ValueError:
                        position = None
                    if position is None or not surface.args:
                        continue
                    label = surface.args[0]
                    entry = {"font_size": fonts.get(font_name), "position": position,
                             "color": _rgba(surface.args[2], names) if len(surface.args) > 2 else None,
                             "source": "{0}:{1}-{1}@{2}".format(relative, node.lineno, digest)}
                    if isinstance(label, ast.Constant) and isinstance(label.value, str):
                        entry["text"] = label.value
                    else:
                        phrases: List[str] = []
                        origin = assigned.get(label.id) if isinstance(label, ast.Name) else label
                        for item in ast.walk(origin) if origin is not None else ():
                            if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.strip():
                                phrases.append(item.value)
                        entry["phrases"] = phrases
                    result["texts"].append(entry)
    return result


def _drawings(files: Mapping[str, Path]) -> List[Dict[str, Any]]:
    """Per-cell drawings with their frame pitch (px per loop step), measured like the source oracle does."""
    from srtp.drawn_shapes import _FileExtractor, discover_drawn_shapes
    from .source_oracle import _evaluate
    result = []
    constants_by_file: Dict[str, Dict[str, Any]] = {}
    for group in discover_drawn_shapes(files):
        frame = group.get("frame") or {}
        loops = [loop["target"] for loop in group.get("loops") or [] if loop.get("target")]
        if frame.get("kind") != "source_rect" or not frame.get("origin") or len(loops) < 2 or not group.get("derivation"):
            continue
        path = group["source"]["path"]
        if path not in constants_by_file:
            raw = Path(files[path]).read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            constants_by_file[path] = dict(_FileExtractor(path, hashlib.sha256(raw).hexdigest(), text, ast.parse(text)).constants)
        constants = constants_by_file[path]
        axes = []
        for origin in frame["origin"][:2]:
            names = [n.id for n in ast.walk(ast.parse(origin["base"], mode="eval")) if isinstance(n, ast.Name)]
            loop = next((name for name in names if name in loops), None)
            def at(value, base=origin["base"], loop=loop):
                bindings = dict(constants)
                bindings.update({name: 0 for name in loops})
                if loop:
                    bindings[loop] = value
                return _evaluate(ast.parse(base, mode="eval").body, bindings)
            try:
                start, pitch = at(0) + origin["offset"], (at(1) - at(0)) if loop else 0
            except (ValueError, KeyError, ZeroDivisionError, TypeError):
                start, pitch, loop = None, 0, None
            axes.append({"loop": loop, "start": start, "pitch": pitch})
        tests = []
        for condition in group.get("conditions") or []:
            match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*==\s*(-?\d+)\s*|\s*(-?\d+)\s*==\s*([A-Za-z_]\w*)\s*",
                                 str(condition.get("test") or ""))
            if match and condition.get("branch") == "then":
                name = match.group(1) or match.group(4)
                tests.append((name, int(match.group(2) or match.group(3))))
        result.append({"id": group["derivation"]["id"], "draw_order": frame.get("draw_order", 0),
                       "size": frame["size"], "axes": axes, "conditioned": bool(group.get("conditions")),
                       "value_tests": tests,
                       "source": "{0}:{1}-{2}@{3}".format(path, group["source"]["line_start"], group["source"]["line_end"],
                                                         group["source"]["file_sha256"])})
    return result


# ------------------------------------------------------------------ Rule facts

def _literal(expression: Any) -> Any:
    if isinstance(expression, Mapping) and expression.get("op") == "literal":
        return expression.get("value")
    return None


def _literals(value: Any) -> List[Any]:
    found = []
    if isinstance(value, Mapping):
        if value.get("op") == "literal" and isinstance(value.get("value"), int) and not isinstance(value.get("value"), bool):
            found.append(value["value"])
        for item in value.values():
            found.extend(_literals(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_literals(item))
    return found


def _grid_sets(rule: Mapping[str, Any], grid: str) -> List[Mapping[str, Any]]:
    commands = []

    def walk(items: Any) -> None:
        for command in items or []:
            if not isinstance(command, Mapping):
                continue
            if command.get("op") == "grid.set" and command.get("state") == grid:
                commands.append(command)
            for key in ("effects", "then", "else"):
                if isinstance(command.get(key), list):
                    walk(command[key])
    for action in rule.get("actions") or []:
        walk(action.get("effects"))
    for system in rule.get("systems") or []:
        walk(system.get("effects"))
    walk((rule.get("state") or {}).get("initial_effects"))
    return commands


def _turn_label(rule: Mapping[str, Any], grid: str, piece_values: Sequence[int]) -> Tuple[Optional[Dict[str, Any]], Dict[Any, str]]:
    """(read source of whose turn it is, value -> participant name), or (None, {})."""
    participants = [p for p in rule.get("participants") or [] if isinstance(p, Mapping)]
    names = {p["id"]: str(p.get("name") or p["id"].rsplit(".", 1)[-1]) for p in participants}
    flow = rule.get("flow") or {}
    if flow.get("turn_order"):
        return {"kind": "flow", "property": "current_actor"}, {pid: names.get(pid, pid) for pid in flow["turn_order"]}
    variables = [v for v in (rule.get("state") or {}).get("variables") or []
                 if isinstance(v, Mapping) and v.get("scope") == "global"]
    for variable in variables:
        if variable.get("type") == "core:participant_id":
            return ({"kind": "state", "scope": "global", "variable": variable["id"]}, dict(names))
    # An int state written into the board by placement holds the mover's piece value.
    for command in _grid_sets(rule, grid):
        value = command.get("value")
        if isinstance(value, Mapping) and value.get("op") == "call" and value.get("function") == "core:state.get":
            state_id = _literal((value.get("args") or [None])[0])
            variable = next((v for v in variables if v.get("id") == state_id and v.get("type") == "core:int"), None)
            owners = _piece_owners(rule, participants, piece_values)
            if variable is not None and owners:
                return {"kind": "state", "scope": "global", "variable": variable["id"]}, owners
    return None, {}


def _piece_owners(rule: Mapping[str, Any], participants: Sequence[Mapping[str, Any]],
                  piece_values: Sequence[int]) -> Dict[Any, str]:
    """Piece value -> participant name: declared legacy owners, else the declared participant order."""
    names = {p["id"]: str(p.get("name") or p["id"]) for p in participants}
    owners: Dict[Any, str] = {}
    for entity in (rule.get("state") or {}).get("entity_types") or []:
        legacy = entity.get("legacy") if isinstance(entity, Mapping) else None
        if isinstance(legacy, Mapping) and legacy.get("owner") in names and isinstance(legacy.get("state_value"), int):
            owners[legacy["state_value"]] = names[legacy["owner"]]
    if owners:
        return owners
    players = [p for p in participants if str(p.get("kind") or "human_or_agent") != "system"]
    if len(players) == len(piece_values):
        return {value: names[p["id"]] for value, p in zip(sorted(piece_values), players)}
    return {}


# ------------------------------------------------------------------ draft

def build_scene_draft(documents: Mapping[str, Mapping[str, Any]], files: Mapping[str, Path],
                      title: str = "", value_map: Optional[Mapping[str, Any]] = None) -> SceneDraft:
    """``value_map`` (source board value as JSON text -> Rule grid value) comes from running the source."""
    rule, asset = documents.get("rule_ir") or {}, documents.get("asset_ir") or {}
    grids = [v for v in (rule.get("state") or {}).get("variables") or []
             if isinstance(v, Mapping) and v.get("scope") == "topology_site"]
    topologies = {t["id"]: t for t in rule.get("topologies") or [] if isinstance(t, Mapping)}
    if len(grids) != 1 or grids[0].get("topology") not in topologies:
        return SceneDraft(None, ["draft needs exactly one board state on one topology"])
    grid, topology = grids[0], topologies[grids[0]["topology"]]
    if topology.get("kind") != "rect_grid" or len(topology.get("axes") or []) != 2:
        return SceneDraft(None, ["draft supports 2D rect_grid boards"])
    derivation_ids = {d.get("id") for d in asset.get("derivations") or [] if isinstance(d, Mapping)}
    drawings = [d for d in _drawings(files) if d["id"] in derivation_ids]
    backgrounds = sorted((d for d in drawings if not d["conditioned"]), key=lambda d: d["draw_order"])
    if not backgrounds:
        return SceneDraft(None, ["no unconditional per-cell source drawing (cell background)"])
    background = backgrounds[0]
    axes = background["axes"]
    if any(axis["start"] is None or not axis["pitch"] for axis in axes):
        return SceneDraft(None, ["cell pitch is not measurable from the source drawing frame"])
    names = [str(axis.get("name")) for axis in topology["axes"]]
    screen_loops = [axes[0]["loop"], axes[1]["loop"]]
    if names == screen_loops[::-1] and names != screen_loops:
        return SceneDraft(None, ["Rule axis 0 is the source's screen y (transposed); left to the model"])

    empty = _literal(grid.get("initial"))
    types = {t.get("id"): t for t in rule.get("types") or [] if isinstance(t, Mapping)}
    enum = types.get(grid.get("type")) if isinstance(types.get(grid.get("type")), Mapping) else None
    domain: Dict[Any, str] = {}
    if enum and isinstance(enum.get("values"), Mapping):
        domain = {value: str(name) for name, value in enum["values"].items()}
    pieces: Dict[int, str] = {}
    variable_names = set()
    for drawing in drawings:
        for name, value in drawing["value_tests"]:
            variable_names.add(name)
            if value_map is not None:
                if str(value) not in value_map:
                    continue
                value = value_map[str(value)]
            pieces.setdefault(value, drawing["id"])
    if len(variable_names) > 1:
        return SceneDraft(None, ["piece drawings test different variables {0}".format(sorted(variable_names))])
    values = set(domain) | set(pieces) | {v for c in _grid_sets(rule, grid["id"]) for v in _literals(c.get("value"))}
    values.add(empty)
    missing = sorted(v for v in values if v != empty and v not in pieces)
    if not pieces or missing:
        return SceneDraft(None, ["no source drawing for state value(s) {0}".format(missing or "any")])

    def variant(value: Any) -> str:
        if value in domain:
            return re.sub(r"[^a-z0-9_]", "_", domain[value].lower())
        return "empty" if value == empty else "value_" + str(value).replace("-", "m")

    measured = measure_source(files)
    width_px, height_px = measured["window"] or (None, None)
    pitch_x, pitch_y = abs(axes[0]["pitch"]), abs(axes[1]["pitch"])
    k = 1.0 / pitch_x
    size_x, size_y = background["size"]
    columns, rows = int(topology["axes"][0]["extent"]), int(topology["axes"][1]["extent"])
    if width_px is None:
        width_px = axes[0]["start"] * 2 + (columns - 1) * pitch_x + size_x
        height_px = axes[1]["start"] * 2 + (rows - 1) * pitch_y + size_y
    tx = (axes[0]["start"] + size_x / 2 - width_px / 2) * k
    ty = -(axes[1]["start"] + size_y / 2 - height_px / 2) * k
    ratio = pitch_y / pitch_x
    index_to_world = [1, 0, 0, round(tx, 6), 0, -round(ratio, 6), 0, round(ty, 6), 0, 0, 1, 0, 0, 0, 0, 1]
    norm = max(size_x, size_y)
    scale = [round(size_x / pitch_x / (size_x / norm), 6), round(size_y / pitch_y / (size_y / norm), 6), 1]
    evidence = [background["source"]] + sorted({d["source"] for d in drawings if d["id"] in pieces.values()})
    first_piece = pieces[min(pieces)]
    piece_variants = {variant(value): ({"visible": False} if value == empty else {"geometry": pieces[value], "visible": True})
                      for value in sorted(values, key=str)}
    identity = {"translation": [0, 0, 0], "rotation_euler_deg": [0, 0, 0], "scale": [1, 1, 1]}
    prefab = {"id": "scene:prefab.cell", "name": "Source cell", "root": {
        "local_id": "cell", "name": "Cell", "active": True,
        # Rows run down the screen (negative y in index_to_world); turning the
        # root keeps the y-up source artwork upright and only mirrors depth.
        "transform": {"translation": [0, 0, 0], "rotation_euler_deg": [180, 0, 0], "scale": [1, 1, 1]},
        "components": [{"id": "pick", "type": "collider", "enabled": True, "properties": {
            "shape": "box", "size": [1, round(1 / ratio, 6), 0.3], "is_trigger": False, "selectable": True}}],
        "children": [
            {"local_id": "tile", "name": "Source cell drawing", "active": True, "transform": dict(identity),
             "components": [{"id": "tile_art", "type": "renderer", "enabled": True, "properties": {
                 "geometry": background["id"], "visible": True, "spatial_role": "content", "scale": scale}}],
             "children": []},
            {"local_id": "piece", "name": "Source piece drawing", "active": True,
             "transform": {"translation": [0, 0, PIECE_LIFT], "rotation_euler_deg": [0, 0, 0], "scale": [1, 1, 1]},
             "components": [{"id": "piece_art", "type": "renderer", "enabled": True, "properties": {
                 "geometry": first_piece, "visible": False, "spatial_role": "content", "scale": scale,
                 "variant": variant(empty), "variants": piece_variants}}],
             "children": []}]}}
    layer = "scene:layer.runtime"

    def node(identifier: str, name: str, components: List[Dict[str, Any]], translation=(0, 0, 0), scale=(1, 1, 1)):
        return {"id": identifier, "name": name, "parent": None, "active": True, "layer": layer,
                "transform": {"translation": list(translation), "rotation_euler_deg": [0, 0, 0], "scale": list(scale)},
                "components": components}

    nodes = [node("scene:board", "Board", [{"id": "sites", "type": "topology_visualizer", "enabled": True, "properties": {
                 "rule_topology": topology["id"], "prefab": prefab["id"], "index_to_world": index_to_world}}]),
             node("scene:camera", "Source view", [{"id": "view", "type": "camera", "enabled": True, "properties": {
                 "projection": "orthographic", "near_clip": 0.1, "far_clip": 100, "active": True,
                 "orthographic_size": round(height_px * k, 6)}}], translation=(0, 0, -10))]
    notes = ["engine scene draft: cell drawing {0}, pieces {1}, pitch {2}px".format(
        background["id"], {variant(v): d for v, d in sorted(pieces.items())}, [pitch_x, pitch_y])]
    if measured.get("fill"):
        nodes.append(node("scene:backdrop", "Source background", [{"id": "backdrop_art", "type": "renderer",
            "enabled": True, "properties": {"geometry": "builtin:quad", "visible": True, "spatial_role": "source_backdrop",
                                            "color": measured["fill"]}}],
            translation=(0, 0, 2), scale=(round(width_px * k, 6), round(height_px * k, 6), 1)))
        evidence.append(measured["fill_source"])
    bindings = [{"id": "scene:binding.piece", "name": "Piece drawing from the board state",
                 "source": {"kind": "state", "scope": "topology_site", "variable": grid["id"]},
                 "target": {"selector": "topology_sites", "node": "scene:board", "visualizer": "sites",
                            "component": "piece_art", "property": "variant"},
                 "transform": {"kind": "map", "cases": [{"equals": value, "value": variant(value)}
                                                        for value in sorted(values, key=str)]}}]
    fonts = [a for a in asset.get("assets") or [] if isinstance(a, Mapping) and a.get("kind") == "font"]
    font = fonts[0]["id"] if len(fonts) == 1 else None
    for index, item in enumerate(measured["texts"]):
        properties = {"mode": "overlay", "visible": True, "origin": [-0.5, 0.5],
                      "position": [round((item["position"][0] - width_px / 2) / height_px, 4),
                                   round(0.5 - item["position"][1] / height_px, 4)]}
        if item.get("font_size"):
            properties["scale"] = round(item["font_size"] / (height_px * 0.025), 3)
        if item.get("color"):
            properties["color"] = item["color"]
        if font:
            properties["font"] = font
        identifier = "scene:hud.text_{0}".format(index)
        if "text" in item:
            properties["text"] = item["text"]
        else:
            expression = _status_expression(rule, grid["id"], sorted(pieces), item.get("phrases") or [])
            if expression is None:
                notes.append("dynamic text at {0} left for the model (no measurable turn/result wording)".format(
                    item["source"].split("@")[0]))
                continue
            properties["text"] = ""
            identifier = "scene:hud.status"
            bindings.append({"id": "scene:binding.status", "name": "Turn and result from the Rule",
                             "source": {"kind": "expression", "expression": expression},
                             "target": {"selector": "node", "node": identifier, "component": "status_text",
                                        "property": "text"}, "transform": {"kind": "direct"}})
        nodes.append(node(identifier, "Source text", [{"id": "status_text" if identifier == "scene:hud.status"
                                                        else "text", "type": "ui_canvas", "enabled": True,
                                                        "properties": properties}]))
        evidence.append(item["source"])
    definition = {"layers": [{"id": layer, "name": "Runtime", "kind": "runtime", "visible": True, "pickable": True,
                              "opacity": 1}],
                  "prefabs": [prefab], "nodes": nodes, "bindings": bindings, "unresolved": []}
    return SceneDraft(definition, [], notes, list(dict.fromkeys(evidence)))


def _status_expression(rule: Mapping[str, Any], grid: str, piece_values: Sequence[int],
                       phrases: Sequence[str]) -> Optional[Dict[str, Any]]:
    """Result text from the Rule outcome, turn text from whose turn it is, worded like the source."""
    lower = [p for p in phrases]
    win = next((p for p in lower if "win" in p.lower()), None)
    draw = next((p for p in lower if re.search(r"draw|tie", p, re.I)), None)
    turn = next((p for p in lower if re.search(r"turn|move|player", p, re.I) and p not in (win, draw)), None)
    if not (win or draw or turn):
        return None

    def lit(value: Any) -> Dict[str, Any]:
        return {"op": "literal", "value": value}

    def read(source: Dict[str, Any]) -> Dict[str, Any]:
        return {"op": "read", "source": source}

    def iff(condition, then, otherwise):
        return {"op": "if", "args": [condition, then, otherwise]}

    def eq(a, b):
        return {"op": "eq", "args": [a, b]}

    participants = {p["id"]: str(p.get("name") or p["id"]) for p in rule.get("participants") or [] if isinstance(p, Mapping)}
    turn_source, turn_labels = _turn_label(rule, grid, piece_values)
    ongoing: Dict[str, Any] = lit("")
    if turn and turn_source:
        for key, name in sorted(turn_labels.items(), key=lambda item: str(item[0]), reverse=True):
            ongoing = iff(eq(read(turn_source), lit(key)), lit(turn + name), ongoing)
    result: Dict[str, Any] = lit(draw.strip() if draw else "")
    if win:
        for pid, name in sorted(participants.items(), reverse=True):
            result = iff(eq(read({"kind": "flow", "property": "winner"}), lit(pid)), lit(win + name), result)
    return iff(read({"kind": "flow", "property": "terminal"}), result, ongoing)
