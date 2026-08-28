"""Golden step-snake four-IR bundle for Project Session playability.

Each arrow-key action advances the snake one cell (no automatic tick).
Gameplay mirrors pygame_snake enough for Workbench acceptance: reverse guard,
wall/self collision, food growth, and a visible board_cell grid.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from srtp.asset_ir_v2 import new_asset_ir, seal_asset_ir
from srtp.input_ir_v2 import default_processing, new_input_ir, seal_input_ir
from srtp.ir_v2 import new_rule_ir, seal_rule_ir
from srtp.project_manifest_v2 import new_project_manifest, seal_project_manifest
from srtp.integration_gate_v1.runner import ProjectArtifacts
from srtp.scene_ir_v2 import (
    identity_matrix,
    identity_transform,
    new_scene_ir,
    seal_scene_ir,
)

# board_cell visual values (Workbench: >0 P1, <0 P2)
EMPTY = 0
BODY = 1
HEAD = 2
FOOD = -1

# trail_dir toward the next newer segment (toward head)
DIR_NONE = 0
DIR_UP = 1
DIR_DOWN = 2
DIR_LEFT = 3
DIR_RIGHT = 4

DEFAULT_EXTENT = 20
DEFAULT_HEAD = (5, 10)
DEFAULT_BODY = ((4, 10), (3, 10))  # neck then tip/tail
DEFAULT_FOOD = (10, 10)


def build_snake_playable_artifacts(
    *,
    project_id: str = "project:game.snake_step_playable",
    extent: int = DEFAULT_EXTENT,
    repository_root: Optional[Path] = None,
    document_suffix: str = "snake_step_playable",
) -> ProjectArtifacts:
    """Build a sealed, compile-ready step-snake Project Session bundle."""

    root = Path(
        repository_root or Path(__file__).resolve().parents[3]
    ).resolve()
    rule = build_snake_step_rule(
        document_id="rule:game.{0}".format(document_suffix),
        extent=extent,
    )
    asset = _asset_document(document_suffix)
    scene = _scene_document(rule, asset, document_suffix)
    input_document = _input_document(rule, document_suffix)
    manifest = new_project_manifest(project_id, title="Step Snake Playable")
    for slot, document in (
        ("rule_ir", rule),
        ("scene_ir", scene),
        ("asset_ir", asset),
        ("input_ir", input_document),
    ):
        manifest["documents"][slot] = {
            "document_id": document["document_id"],
            "ir_version": document["ir_version"],
            "content_hash": document["content_hash"],
        }
    manifest["unresolved"] = []
    manifest["metadata"]["description"] = (
        "Arrow keys advance one cell per press. Reviewed fixture / dev harness "
        "(provenance=fixture; not an LLM evidence product)."
    )
    provenance = dict(manifest.get("provenance") or {})
    provenance["kind"] = "fixture"
    provenance["source"] = "snake_playable_fixture"
    manifest["provenance"] = provenance
    manifest = seal_project_manifest(manifest)
    return ProjectArtifacts(
        manifest, rule, scene, asset, input_document, root,
    )


def write_snake_playable_artifacts(
    out_dir: Path, *, extent: int = DEFAULT_EXTENT,
) -> Path:
    artifacts = build_snake_playable_artifacts(extent=extent)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    ir_dir = root / "ir"
    ir_dir.mkdir(parents=True, exist_ok=True)
    _write_json(root / "project.manifest.json", artifacts.manifest)
    _write_json(ir_dir / "game.rule-ir.json", artifacts.rule)
    _write_json(ir_dir / "game.scene-ir.json", artifacts.scene)
    _write_json(ir_dir / "game.asset-ir.json", artifacts.asset)
    _write_json(ir_dir / "game.input-ir.json", artifacts.input)
    return root


def snake_step_rule_overlay(extent: int = DEFAULT_EXTENT) -> Dict[str, Any]:
    """Unsealed rule fragment used by the LLM compiler family overlay."""

    return build_snake_step_rule(
        document_id="rule:game.snake_step_overlay",
        extent=extent,
        seal=False,
    )


def build_snake_step_rule(
    *,
    document_id: str,
    extent: int = DEFAULT_EXTENT,
    seal: bool = True,
) -> Dict[str, Any]:
    document = new_rule_ir(document_id, "Step Snake")
    document["metadata"]["description"] = (
        "One cell per arrow-key action. No automatic tick."
    )
    document["participants"] = [{
        "id": "rule:participant.player",
        "name": "Player",
        "kind": "human",
    }]
    document["topologies"] = [{
        "id": "rule:topology.board",
        "name": "Board",
        "kind": "rect_grid",
        "anchor": "cell",
        "axes": [
            {"name": "x", "extent": int(extent), "boundary": "bounded"},
            {"name": "y", "extent": int(extent), "boundary": "bounded"},
        ],
        "neighborhoods": [],
    }]
    document["types"] = []
    document["state"] = {
        "variables": _state_variables(extent),
        "entity_types": [],
        "initial_effects": _initial_effects(),
        "information_model": "perfect",
    }
    document["actions"] = [
        _move_action("up", 0, -1, DIR_UP, DIR_DOWN),
        _move_action("down", 0, 1, DIR_DOWN, DIR_UP),
        _move_action("left", -1, 0, DIR_LEFT, DIR_RIGHT),
        _move_action("right", 1, 0, DIR_RIGHT, DIR_LEFT),
    ]
    document["systems"] = []
    document["queries"] = []
    document["events"] = []
    document["goals"] = []
    document["outcomes"] = [{
        "id": "rule:outcome.collision_loss",
        "name": "Collision Loss",
        "priority": 100,
        "condition": {
            "op": "eq",
            "args": [
                {"op": "ref", "path": "rule:state.alive"},
                {"op": "literal", "value": False},
            ],
        },
        "result": {
            "status": "loss",
            "terminal": True,
            "winners": [],
            "losers": [{"op": "literal", "value": "rule:participant.player"}],
            "scores": {},
        },
    }]
    document["random_streams"] = []
    document["flow"] = {
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
        "turn_order": [],
    }
    document["unresolved"] = []
    document["invariants"] = []
    if seal:
        return seal_rule_ir(document)
    return document


def _state_variables(extent: int) -> List[Dict[str, Any]]:
    head_x, head_y = DEFAULT_HEAD
    tail_x, tail_y = DEFAULT_BODY[-1]
    food_x, food_y = DEFAULT_FOOD
    return [
        _global_int("rule:state.head_x", "Head X", head_x),
        _global_int("rule:state.head_y", "Head Y", head_y),
        _global_int("rule:state.tail_x", "Tail X", tail_x),
        _global_int("rule:state.tail_y", "Tail Y", tail_y),
        _global_int("rule:state.food_x", "Food X", food_x),
        _global_int("rule:state.food_y", "Food Y", food_y),
        _global_int("rule:state.last_dx", "Last DX", 0),
        _global_int("rule:state.last_dy", "Last DY", 0),
        _global_int("rule:state.length", "Length", 3),
        _global_int("rule:state.score", "Score", 0),
        {
            "id": "rule:state.alive",
            "name": "Alive",
            "type": "core:bool",
            "scope": "global",
            "initial": {"op": "literal", "value": True},
        },
        {
            "id": "rule:state.grow_pending",
            "name": "Grow Pending",
            "type": "core:bool",
            "scope": "global",
            "initial": {"op": "literal", "value": False},
        },
        {
            "id": "rule:state.board_cell",
            "name": "Board Cell",
            "type": "core:int",
            "scope": "topology_site",
            "topology": "rule:topology.board",
            "initial": {"op": "literal", "value": EMPTY},
        },
        {
            "id": "rule:state.trail_dir",
            "name": "Trail Direction",
            "type": "core:int",
            "scope": "topology_site",
            "topology": "rule:topology.board",
            "initial": {"op": "literal", "value": DIR_NONE},
        },
        _global_int("rule:state.extent", "Extent", extent),
        _global_int("rule:state.clear_x", "Clear X", tail_x),
        _global_int("rule:state.clear_y", "Clear Y", tail_y),
        _global_int("rule:state.next_tail_x", "Next Tail X", tail_x),
        _global_int("rule:state.next_tail_y", "Next Tail Y", tail_y),
    ]


def _global_int(identifier: str, name: str, value: int) -> Dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "type": "core:int",
        "scope": "global",
        "initial": {"op": "literal", "value": int(value)},
    }


def _initial_effects() -> List[Dict[str, Any]]:
    head = DEFAULT_HEAD
    neck, tip = DEFAULT_BODY
    food = DEFAULT_FOOD
    return [
        _grid_set_board(tip, BODY),
        _grid_set_board(neck, BODY),
        _grid_set_board(head, HEAD),
        _grid_set_trail(tip, DIR_RIGHT),
        _grid_set_trail(neck, DIR_RIGHT),
        _grid_set_board(food, FOOD),
    ]


def _grid_set_board(coordinate: Sequence[int], value: int) -> Dict[str, Any]:
    return {
        "op": "grid.set",
        "state": "rule:state.board_cell",
        "topology": "rule:topology.board",
        "coordinate": {"op": "literal", "value": [int(coordinate[0]), int(coordinate[1])]},
        "value": {"op": "literal", "value": int(value)},
    }


def _grid_set_trail(coordinate: Sequence[int], value: int) -> Dict[str, Any]:
    return {
        "op": "grid.set",
        "state": "rule:state.trail_dir",
        "topology": "rule:topology.board",
        "coordinate": {"op": "literal", "value": [int(coordinate[0]), int(coordinate[1])]},
        "value": {"op": "literal", "value": int(value)},
    }


def _lit(value: Any) -> Dict[str, Any]:
    return {"op": "literal", "value": value}


def _ref(path: str) -> Dict[str, Any]:
    return {"op": "ref", "path": path}


def _call(function: str, *args: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "call", "function": function, "args": list(args)}


def _eq(left: Mapping[str, Any], right: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "eq", "args": [left, right]}


def _and(*args: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "and", "args": list(args)}


def _or(*args: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "or", "args": list(args)}


def _not(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "not", "args": [value]}


def _add(*args: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "add", "args": list(args)}


def _mod(left: Mapping[str, Any], right: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "mod", "args": [left, right]}


def _if(
    condition: Mapping[str, Any],
    then: Mapping[str, Any],
    otherwise: Mapping[str, Any],
) -> Dict[str, Any]:
    return {"op": "if", "condition": condition, "then": then, "else": otherwise}


def _vector(x: Mapping[str, Any], y: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "vector", "items": [x, y]}


def _head_coord() -> Dict[str, Any]:
    return _vector(_ref("rule:state.head_x"), _ref("rule:state.head_y"))


def _tail_coord() -> Dict[str, Any]:
    return _vector(_ref("rule:state.tail_x"), _ref("rule:state.tail_y"))


def _new_head(dx: int, dy: int) -> Dict[str, Any]:
    return _vector(
        _add(_ref("rule:state.head_x"), _lit(dx)),
        _add(_ref("rule:state.head_y"), _lit(dy)),
    )


def _grid_get(state_id: str, coordinate: Mapping[str, Any]) -> Dict[str, Any]:
    return _call("core:grid.get", _lit(state_id), coordinate)


def _grid_equals(state_id: str, coordinate: Mapping[str, Any], value: int) -> Dict[str, Any]:
    return _call("core:grid.equals", _lit(state_id), coordinate, _lit(value))


def _state_set(target: str, value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "op": "state.set",
        "target": _lit(target),
        "value": value,
    }


def _move_action(
    name: str, dx: int, dy: int, dir_code: int, opposite_dir: int,
) -> Dict[str, Any]:
    new_head = _new_head(dx, dy)
    reverse_blocked = _and(
        _eq(_ref("rule:state.last_dx"), _lit(-dx)),
        _eq(_ref("rule:state.last_dy"), _lit(-dy)),
        # Allow first move while last_dx/dy are still 0.
        _not(_and(
            _eq(_ref("rule:state.last_dx"), _lit(0)),
            _eq(_ref("rule:state.last_dy"), _lit(0)),
        )),
    )
    # Simpler reverse: last direction equals opposite of this move.
    reverse_blocked = _and(
        _eq(_ref("rule:state.last_dx"), _lit(-dx)),
        _eq(_ref("rule:state.last_dy"), _lit(-dy)),
    )
    cell_free = _or(
        _grid_equals("rule:state.board_cell", new_head, EMPTY),
        _grid_equals("rule:state.board_cell", new_head, FOOD),
    )
    precondition = _and(
        _eq(_ref("rule:state.alive"), _lit(True)),
        _not(reverse_blocked),
        _call("core:topology.contains", _lit("rule:topology.board"), new_head),
        cell_free,
    )
    grow = _grid_equals("rule:state.board_cell", new_head, FOOD)
    growing = _ref("rule:state.grow_pending")
    trail_at_tail = _grid_get("rule:state.trail_dir", _tail_coord())
    next_tail_x = _if(
        growing,
        _ref("rule:state.tail_x"),
        _add(
            _ref("rule:state.tail_x"),
            _if(
                _eq(trail_at_tail, _lit(DIR_RIGHT)), _lit(1),
                _if(_eq(trail_at_tail, _lit(DIR_LEFT)), _lit(-1), _lit(0)),
            ),
        ),
    )
    next_tail_y = _if(
        growing,
        _ref("rule:state.tail_y"),
        _add(
            _ref("rule:state.tail_y"),
            _if(
                _eq(trail_at_tail, _lit(DIR_DOWN)), _lit(1),
                _if(_eq(trail_at_tail, _lit(DIR_UP)), _lit(-1), _lit(0)),
            ),
        ),
    )
    clear_coord = _vector(_ref("rule:state.clear_x"), _ref("rule:state.clear_y"))
    # Deterministic food hop when growing.
    next_food_x = _mod(
        _add(_ref("rule:state.food_x"), _lit(7)),
        _ref("rule:state.extent"),
    )
    next_food_y = _mod(
        _add(_ref("rule:state.food_y"), _lit(5)),
        _ref("rule:state.extent"),
    )
    effects: List[Dict[str, Any]] = [
        _state_set("rule:state.grow_pending", grow),
        _state_set("rule:state.clear_x", _ref("rule:state.tail_x")),
        _state_set("rule:state.clear_y", _ref("rule:state.tail_y")),
        _state_set("rule:state.next_tail_x", next_tail_x),
        _state_set("rule:state.next_tail_y", next_tail_y),
        {
            "op": "grid.set",
            "state": "rule:state.trail_dir",
            "topology": "rule:topology.board",
            "coordinate": _head_coord(),
            "value": _lit(dir_code),
        },
        {
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": _head_coord(),
            "value": _lit(BODY),
        },
        {
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": new_head,
            "value": _lit(HEAD),
        },
        {
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": clear_coord,
            "value": _if(
                growing,
                _grid_get("rule:state.board_cell", clear_coord),
                _lit(EMPTY),
            ),
        },
        {
            "op": "grid.set",
            "state": "rule:state.trail_dir",
            "topology": "rule:topology.board",
            "coordinate": clear_coord,
            "value": _if(
                growing,
                _grid_get("rule:state.trail_dir", clear_coord),
                _lit(DIR_NONE),
            ),
        },
        _state_set("rule:state.tail_x", _ref("rule:state.next_tail_x")),
        _state_set("rule:state.tail_y", _ref("rule:state.next_tail_y")),
        _state_set("rule:state.head_x", _add(_ref("rule:state.head_x"), _lit(dx))),
        _state_set("rule:state.head_y", _add(_ref("rule:state.head_y"), _lit(dy))),
        _state_set("rule:state.last_dx", _lit(dx)),
        _state_set("rule:state.last_dy", _lit(dy)),
        _state_set(
            "rule:state.score",
            _if(growing, _add(_ref("rule:state.score"), _lit(1)), _ref("rule:state.score")),
        ),
        _state_set(
            "rule:state.length",
            _if(growing, _add(_ref("rule:state.length"), _lit(1)), _ref("rule:state.length")),
        ),
        _state_set(
            "rule:state.food_x",
            _if(growing, next_food_x, _ref("rule:state.food_x")),
        ),
        _state_set(
            "rule:state.food_y",
            _if(growing, next_food_y, _ref("rule:state.food_y")),
        ),
        {
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": _vector(_ref("rule:state.food_x"), _ref("rule:state.food_y")),
            "value": _if(
                growing,
                _if(
                    _or(
                        _grid_equals(
                            "rule:state.board_cell",
                            _vector(_ref("rule:state.food_x"), _ref("rule:state.food_y")),
                            EMPTY,
                        ),
                        _grid_equals(
                            "rule:state.board_cell",
                            _vector(_ref("rule:state.food_x"), _ref("rule:state.food_y")),
                            FOOD,
                        ),
                    ),
                    _lit(FOOD),
                    _grid_get(
                        "rule:state.board_cell",
                        _vector(_ref("rule:state.food_x"), _ref("rule:state.food_y")),
                    ),
                ),
                _grid_get(
                    "rule:state.board_cell",
                    _vector(_ref("rule:state.food_x"), _ref("rule:state.food_y")),
                ),
            ),
        },
        _state_set("rule:state.grow_pending", _lit(False)),
    ]
    return {
        "id": "rule:action.move_{0}".format(name),
        "name": "Move {0}".format(name.replace("_", " ").title()),
        "actor": _lit("rule:participant.player"),
        "parameters": [],
        "precondition": precondition,
        "effects": effects,
        "timing": {"phase": "rule:phase.input"},
        "encoding": {"kind": "none"},
    }


def _component(identifier: str, kind: str, properties: Mapping[str, Any]) -> Dict[str, Any]:
    return {"id": identifier, "type": kind, "enabled": True, "properties": dict(properties)}


def _asset_document(suffix: str) -> Dict[str, Any]:
    document = new_asset_ir("asset:game.{0}".format(suffix), "Step Snake Cell Asset")
    document["derivations"] = [{
        "id": "asset:model.cell_cube_{0}".format(suffix),
        "name": "Cell Cube",
        "kind": "model",
        "media_type": "application/vnd.cubeengine.presentation+json",
        "strategy": "procedural_mesh",
        "inputs": [],
        "settings": {"primitive": "cube", "dimensions": [1.0, 1.0, 1.0]},
        "expected_content_hash": "",
        "license_policy": "inherit",
    }]
    document["roles"] = [{
        "id": "asset:role.board_cell_{0}".format(suffix),
        "name": "Board Cell",
        "semantic": "board.cell",
        "resource": "asset:model.cell_cube_{0}".format(suffix),
        "usage": "world_mesh",
        "required": True,
    }]
    document["unresolved"] = []
    return seal_asset_ir(document)


def _scene_document(rule: Mapping[str, Any], asset: Mapping[str, Any], suffix: str) -> Dict[str, Any]:
    resource = "asset:model.cell_cube_{0}".format(suffix)
    document = new_scene_ir("scene:game.{0}".format(suffix), "Step Snake Scene")
    document["dependencies"]["rule_ir"] = {
        "document_id": rule["document_id"], "content_hash": rule["content_hash"],
    }
    document["dependencies"]["asset_ir"] = {
        "document_id": asset["document_id"], "content_hash": asset["content_hash"],
    }
    document["prefabs"] = [{
        "id": "scene:prefab.cell",
        "name": "Cell",
        "root": {
            "local_id": "root",
            "name": "Cell",
            "active": True,
            "transform": identity_transform(),
            "components": [
                _component("renderer", "renderer", {
                    "geometry": resource,
                    "visible": True,
                    "opacity": 1.0,
                    "variant": "empty",
                }),
                _component("collider", "collider", {
                    "shape": "box",
                    "size": [1.0, 1.0, 1.0],
                    "is_trigger": False,
                    "selectable": True,
                }),
            ],
            "children": [],
        },
    }]
    document["nodes"] = [{
        "id": "scene:node.board",
        "name": "Board",
        "parent": None,
        "active": True,
        "layer": "scene:layer.runtime",
        "transform": identity_transform(),
        "components": [_component("sites", "topology_visualizer", {
            "rule_topology": "rule:topology.board",
            "prefab": "scene:prefab.cell",
            "index_to_world": identity_matrix(),
        })],
    }]
    document["bindings"] = [{
        "id": "scene:binding.cell_variant",
        "name": "Cell Variant",
        "source": {
            "kind": "state",
            "scope": "topology_site",
            "variable": "rule:state.board_cell",
        },
        "target": {
            "selector": "topology_sites",
            "node": "scene:node.board",
            "visualizer": "sites",
            "component": "renderer",
            "property": "variant",
        },
        "transform": {
            "kind": "map",
            "cases": [
                {"equals": EMPTY, "value": "empty"},
                {"equals": BODY, "value": "body"},
                {"equals": HEAD, "value": "head"},
                {"equals": FOOD, "value": "food"},
            ],
        },
    }]
    document["unresolved"] = []
    return seal_scene_ir(document)


def _input_document(rule: Mapping[str, Any], suffix: str) -> Dict[str, Any]:
    document = new_input_ir("input:game.{0}".format(suffix), "Step Snake Input")
    document["dependencies"]["rule_ir"] = {
        "document_id": rule["document_id"], "content_hash": rule["content_hash"],
    }
    document["contexts"] = [{
        "id": "input:context.play",
        "name": "Play",
        "priority": 100,
        "enabled_by_default": True,
        "focus": "viewport",
        "consume_policy": "first_match",
        "exclusive_group": "runtime_mode",
    }]
    intents = []
    bindings = []
    for name, control in (
        ("up", "keyboard.key.arrow_up"),
        ("down", "keyboard.key.arrow_down"),
        ("left", "keyboard.key.arrow_left"),
        ("right", "keyboard.key.arrow_right"),
    ):
        intent_id = "input:action.intent.move.{0}".format(name)
        action_id = "rule:action.move_{0}".format(name)
        intents.append({
            "id": intent_id,
            "name": "Move {0}".format(name.title()),
            "value_type": "digital",
            "required": True,
            "target": {
                "kind": "rule_action",
                "action": action_id,
                "parameters": {},
            },
        })
        bindings.append({
            "id": "input:binding.{0}".format(name),
            "name": name.title(),
            "context": "input:context.play",
            "intent": intent_id,
            "priority": 100,
            "enabled": True,
            "consume": True,
            "rebindable": True,
            "slot": "primary",
            "accessibility_label": name.title(),
            "trigger": {
                "kind": "control",
                "device": "keyboard",
                "control": control,
                "phase": "press",
                "modifiers": [],
                "modifier_policy": "exact",
            },
            "processing": default_processing(),
        })
    document["intents"] = intents
    document["bindings"] = bindings
    document["unresolved"] = []
    return seal_input_ir(document)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def overlay_rule_semantics_onto(
    rule: Mapping[str, Any], *, extent: Optional[int] = None,
) -> Dict[str, Any]:
    """Replace empty snake mechanics with the golden step-snake rule body."""

    document = deepcopy(dict(rule))
    axes = []
    for topology in document.get("topologies") or []:
        if isinstance(topology, Mapping) and topology.get("id") == "rule:topology.board":
            axes = list(topology.get("axes") or [])
            break
    resolved_extent = extent
    if resolved_extent is None and axes:
        try:
            resolved_extent = int(axes[0].get("extent"))
        except Exception:
            resolved_extent = DEFAULT_EXTENT
    if resolved_extent is None:
        resolved_extent = DEFAULT_EXTENT
    template = build_snake_step_rule(
        document_id=str(document.get("document_id") or "rule:game.snake"),
        extent=int(resolved_extent),
        seal=False,
    )
    for key in (
        "types", "participants", "topologies", "state", "actions", "systems",
        "queries", "events", "goals", "outcomes", "random_streams", "flow",
        "unresolved", "invariants",
    ):
        document[key] = deepcopy(template[key])
    document["document_id"] = rule.get("document_id") or template["document_id"]
    if isinstance(rule.get("metadata"), Mapping):
        meta = dict(document.get("metadata") or {})
        meta.update({k: v for k, v in rule["metadata"].items() if k != "description"})
        meta["description"] = template["metadata"]["description"]
        document["metadata"] = meta
    return seal_rule_ir(document, revision=int(document.get("revision") or 0))


def apply_snake_playable_bundle_overlay(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    extent: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Replace empty snake four-IR shells with the golden step-snake semantics."""

    docs = {key: dict(value) for key, value in documents.items()}
    rule = overlay_rule_semantics_onto(docs["rule_ir"], extent=extent)
    suffix = str(rule["document_id"]).split(":", 1)[-1]
    asset = _asset_document(suffix.replace(".", "_")[:48] or "snake")
    # Keep existing asset document_id/hash lineage when present.
    if isinstance(docs.get("asset_ir"), Mapping) and docs["asset_ir"].get("document_id"):
        asset["document_id"] = docs["asset_ir"]["document_id"]
        asset = seal_asset_ir(asset, revision=int(docs["asset_ir"].get("revision") or 0))
    scene = _scene_document(rule, asset, suffix.replace(".", "_")[:48] or "snake")
    if isinstance(docs.get("scene_ir"), Mapping) and docs["scene_ir"].get("document_id"):
        scene["document_id"] = docs["scene_ir"]["document_id"]
        scene["dependencies"]["rule_ir"] = {
            "document_id": rule["document_id"], "content_hash": rule["content_hash"],
        }
        scene["dependencies"]["asset_ir"] = {
            "document_id": asset["document_id"], "content_hash": asset["content_hash"],
        }
        scene = seal_scene_ir(scene, revision=int(docs["scene_ir"].get("revision") or 0))
    input_document = _input_document(rule, suffix.replace(".", "_")[:48] or "snake")
    if isinstance(docs.get("input_ir"), Mapping) and docs["input_ir"].get("document_id"):
        input_document["document_id"] = docs["input_ir"]["document_id"]
        input_document["dependencies"]["rule_ir"] = {
            "document_id": rule["document_id"], "content_hash": rule["content_hash"],
        }
        input_document = seal_input_ir(
            input_document, revision=int(docs["input_ir"].get("revision") or 0),
        )
    docs["rule_ir"] = rule
    docs["asset_ir"] = asset
    docs["scene_ir"] = scene
    docs["input_ir"] = input_document
    return docs


def _actions_lack_effects(rule: Mapping[str, Any]) -> bool:
    actions = rule.get("actions") or []
    if not isinstance(actions, list) or not actions:
        return True
    for item in actions:
        if not isinstance(item, Mapping):
            continue
        effects = item.get("effects")
        if isinstance(effects, list) and effects:
            return False
    return True
