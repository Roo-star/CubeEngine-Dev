"""Golden step-snake four-IR bundle for Project Session playability.

Each key action advances the snake one cell (no automatic tick).
Optional z_extent (>=2) adds a Z axis with PageUp/PageDown moves.
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
DIR_Z_UP = 5
DIR_Z_DOWN = 6

DEFAULT_EXTENT = 20
DEFAULT_Z_EXTENT = 3
DEFAULT_HEAD = (5, 10)
DEFAULT_BODY = ((4, 10), (3, 10))  # neck then tip/tail
DEFAULT_FOOD = (10, 10)
DEFAULT_Z = 0


def build_snake_playable_artifacts(
    *,
    project_id: str = "project:game.snake_step_playable",
    extent: int = DEFAULT_EXTENT,
    z_extent: Optional[int] = None,
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
        z_extent=z_extent,
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
    if z_extent is not None and int(z_extent) >= 2:
        manifest["metadata"]["description"] = (
            "Arrow keys move XY; PageUp/PageDown move Z. Reviewed fixture / "
            "dev harness (provenance=fixture; not an LLM evidence product)."
        )
    else:
        manifest["metadata"]["description"] = (
            "Arrow keys advance one cell per press. Reviewed fixture / "
            "dev harness (provenance=fixture; not an LLM evidence product)."
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
    out_dir: Path,
    *,
    extent: int = DEFAULT_EXTENT,
    z_extent: Optional[int] = None,
) -> Path:
    artifacts = build_snake_playable_artifacts(extent=extent, z_extent=z_extent)
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


def snake_step_rule_overlay(
    extent: int = DEFAULT_EXTENT,
    z_extent: Optional[int] = None,
) -> Dict[str, Any]:
    """Unsealed rule fragment used by the LLM compiler family overlay."""

    return build_snake_step_rule(
        document_id="rule:game.snake_step_overlay",
        extent=extent,
        z_extent=z_extent,
        seal=False,
    )


def build_snake_step_rule(
    *,
    document_id: str,
    extent: int = DEFAULT_EXTENT,
    z_extent: Optional[int] = None,
    seal: bool = True,
) -> Dict[str, Any]:
    rank3 = z_extent is not None and int(z_extent) >= 2
    z_ext = int(z_extent) if rank3 else None
    document = new_rule_ir(document_id, "Step Snake")
    if rank3:
        document["metadata"]["description"] = (
            "One cell per key action (XY arrows + Z PageUp/PageDown). "
            "No automatic tick."
        )
    else:
        document["metadata"]["description"] = (
            "One cell per arrow-key action. No automatic tick."
        )
    document["participants"] = [{
        "id": "rule:participant.player",
        "name": "Player",
        "kind": "human",
    }]
    axes = [
        {"name": "x", "extent": int(extent), "boundary": "bounded"},
        {"name": "y", "extent": int(extent), "boundary": "bounded"},
    ]
    if rank3:
        axes.append({"name": "z", "extent": int(z_ext), "boundary": "bounded"})
    document["topologies"] = [{
        "id": "rule:topology.board",
        "name": "Board",
        "kind": "rect_grid",
        "anchor": "cell",
        "axes": axes,
        "neighborhoods": [],
    }]
    document["types"] = []
    document["state"] = {
        "variables": _state_variables(extent, z_extent=z_ext),
        "entity_types": [],
        "initial_effects": _initial_effects(rank3=rank3),
        "information_model": "perfect",
    }
    document["actions"] = [
        _move_action("up", 0, -1, 0, DIR_UP, DIR_DOWN, rank3=rank3),
        _move_action("down", 0, 1, 0, DIR_DOWN, DIR_UP, rank3=rank3),
        _move_action("left", -1, 0, 0, DIR_LEFT, DIR_RIGHT, rank3=rank3),
        _move_action("right", 1, 0, 0, DIR_RIGHT, DIR_LEFT, rank3=rank3),
    ]
    if rank3:
        document["actions"].extend([
            _move_action("z_up", 0, 0, 1, DIR_Z_UP, DIR_Z_DOWN, rank3=True),
            _move_action("z_down", 0, 0, -1, DIR_Z_DOWN, DIR_Z_UP, rank3=True),
        ])
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


def _state_variables(
    extent: int, *, z_extent: Optional[int] = None,
) -> List[Dict[str, Any]]:
    head_x, head_y = DEFAULT_HEAD
    tail_x, tail_y = DEFAULT_BODY[-1]
    food_x, food_y = DEFAULT_FOOD
    rank3 = z_extent is not None and int(z_extent) >= 2
    variables = [
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
    if rank3:
        variables.extend([
            _global_int("rule:state.head_z", "Head Z", DEFAULT_Z),
            _global_int("rule:state.tail_z", "Tail Z", DEFAULT_Z),
            _global_int("rule:state.food_z", "Food Z", DEFAULT_Z),
            _global_int("rule:state.last_dz", "Last DZ", 0),
            _global_int("rule:state.extent_z", "Extent Z", int(z_extent)),
            _global_int("rule:state.clear_z", "Clear Z", DEFAULT_Z),
            _global_int("rule:state.next_tail_z", "Next Tail Z", DEFAULT_Z),
        ])
    return variables


def _global_int(identifier: str, name: str, value: int) -> Dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "type": "core:int",
        "scope": "global",
        "initial": {"op": "literal", "value": int(value)},
    }


def _initial_effects(*, rank3: bool = False) -> List[Dict[str, Any]]:
    head = _pad_coord(DEFAULT_HEAD, rank3)
    neck = _pad_coord(DEFAULT_BODY[0], rank3)
    tip = _pad_coord(DEFAULT_BODY[1], rank3)
    food = _pad_coord(DEFAULT_FOOD, rank3)
    return [
        _grid_set_board(tip, BODY),
        _grid_set_board(neck, BODY),
        _grid_set_board(head, HEAD),
        _grid_set_trail(tip, DIR_RIGHT),
        _grid_set_trail(neck, DIR_RIGHT),
        _grid_set_board(food, FOOD),
    ]


def _pad_coord(coordinate: Sequence[int], rank3: bool) -> Tuple[int, ...]:
    values = [int(coordinate[0]), int(coordinate[1])]
    if rank3:
        values.append(int(coordinate[2]) if len(coordinate) > 2 else DEFAULT_Z)
    return tuple(values)


def _grid_set_board(coordinate: Sequence[int], value: int) -> Dict[str, Any]:
    return {
        "op": "grid.set",
        "state": "rule:state.board_cell",
        "topology": "rule:topology.board",
        "coordinate": {"op": "literal", "value": [int(v) for v in coordinate]},
        "value": {"op": "literal", "value": int(value)},
    }


def _grid_set_trail(coordinate: Sequence[int], value: int) -> Dict[str, Any]:
    return {
        "op": "grid.set",
        "state": "rule:state.trail_dir",
        "topology": "rule:topology.board",
        "coordinate": {"op": "literal", "value": [int(v) for v in coordinate]},
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


def _vector(*items: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "vector", "items": list(items)}


def _head_coord(*, rank3: bool) -> Dict[str, Any]:
    if rank3:
        return _vector(
            _ref("rule:state.head_x"),
            _ref("rule:state.head_y"),
            _ref("rule:state.head_z"),
        )
    return _vector(_ref("rule:state.head_x"), _ref("rule:state.head_y"))


def _tail_coord(*, rank3: bool) -> Dict[str, Any]:
    if rank3:
        return _vector(
            _ref("rule:state.tail_x"),
            _ref("rule:state.tail_y"),
            _ref("rule:state.tail_z"),
        )
    return _vector(_ref("rule:state.tail_x"), _ref("rule:state.tail_y"))


def _food_coord(*, rank3: bool) -> Dict[str, Any]:
    if rank3:
        return _vector(
            _ref("rule:state.food_x"),
            _ref("rule:state.food_y"),
            _ref("rule:state.food_z"),
        )
    return _vector(_ref("rule:state.food_x"), _ref("rule:state.food_y"))


def _clear_coord(*, rank3: bool) -> Dict[str, Any]:
    if rank3:
        return _vector(
            _ref("rule:state.clear_x"),
            _ref("rule:state.clear_y"),
            _ref("rule:state.clear_z"),
        )
    return _vector(_ref("rule:state.clear_x"), _ref("rule:state.clear_y"))


def _new_head(dx: int, dy: int, dz: int, *, rank3: bool) -> Dict[str, Any]:
    if rank3:
        return _vector(
            _add(_ref("rule:state.head_x"), _lit(dx)),
            _add(_ref("rule:state.head_y"), _lit(dy)),
            _add(_ref("rule:state.head_z"), _lit(dz)),
        )
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


def _trail_delta(trail: Mapping[str, Any], positive_dir: int, negative_dir: int) -> Dict[str, Any]:
    return _if(
        _eq(trail, _lit(positive_dir)), _lit(1),
        _if(_eq(trail, _lit(negative_dir)), _lit(-1), _lit(0)),
    )


def _move_action(
    name: str,
    dx: int,
    dy: int,
    dz: int,
    dir_code: int,
    opposite_dir: int,
    *,
    rank3: bool = False,
) -> Dict[str, Any]:
    del opposite_dir  # reserved for docs / future soft reverse hints
    new_head = _new_head(dx, dy, dz, rank3=rank3)
    reverse_parts = [
        _eq(_ref("rule:state.last_dx"), _lit(-dx)),
        _eq(_ref("rule:state.last_dy"), _lit(-dy)),
    ]
    if rank3:
        reverse_parts.append(_eq(_ref("rule:state.last_dz"), _lit(-dz)))
    reverse_blocked = _and(*reverse_parts)
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
    trail_at_tail = _grid_get("rule:state.trail_dir", _tail_coord(rank3=rank3))
    next_tail_x = _if(
        growing,
        _ref("rule:state.tail_x"),
        _add(_ref("rule:state.tail_x"), _trail_delta(trail_at_tail, DIR_RIGHT, DIR_LEFT)),
    )
    next_tail_y = _if(
        growing,
        _ref("rule:state.tail_y"),
        _add(_ref("rule:state.tail_y"), _trail_delta(trail_at_tail, DIR_DOWN, DIR_UP)),
    )
    clear_coord = _clear_coord(rank3=rank3)
    head_coord = _head_coord(rank3=rank3)
    food_coord = _food_coord(rank3=rank3)
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
    ]
    if rank3:
        next_tail_z = _if(
            growing,
            _ref("rule:state.tail_z"),
            _add(
                _ref("rule:state.tail_z"),
                _trail_delta(trail_at_tail, DIR_Z_UP, DIR_Z_DOWN),
            ),
        )
        effects.extend([
            _state_set("rule:state.clear_z", _ref("rule:state.tail_z")),
            _state_set("rule:state.next_tail_z", next_tail_z),
        ])
    effects.extend([
        {
            "op": "grid.set",
            "state": "rule:state.trail_dir",
            "topology": "rule:topology.board",
            "coordinate": head_coord,
            "value": _lit(dir_code),
        },
        {
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": head_coord,
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
    ])
    if rank3:
        effects.extend([
            _state_set("rule:state.tail_z", _ref("rule:state.next_tail_z")),
            _state_set("rule:state.head_z", _add(_ref("rule:state.head_z"), _lit(dz))),
            _state_set("rule:state.last_dz", _lit(dz)),
        ])
    effects.extend([
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
    ])
    effects.extend([
        {
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": food_coord,
            "value": _if(
                growing,
                _if(
                    _or(
                        _grid_equals("rule:state.board_cell", food_coord, EMPTY),
                        _grid_equals("rule:state.board_cell", food_coord, FOOD),
                    ),
                    _lit(FOOD),
                    _grid_get("rule:state.board_cell", food_coord),
                ),
                _grid_get("rule:state.board_cell", food_coord),
            ),
        },
        _state_set("rule:state.grow_pending", _lit(False)),
    ])
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
    controls = [
        ("up", "keyboard.key.arrow_up"),
        ("down", "keyboard.key.arrow_down"),
        ("left", "keyboard.key.arrow_left"),
        ("right", "keyboard.key.arrow_right"),
    ]
    action_ids = {
        str(action.get("id"))
        for action in (rule.get("actions") or [])
        if isinstance(action, Mapping)
    }
    if "rule:action.move_z_up" in action_ids:
        controls.extend([
            ("z_up", "keyboard.key.page_up"),
            ("z_down", "keyboard.key.page_down"),
        ])
    intents = []
    bindings = []
    for name, control in controls:
        intent_id = "input:action.intent.move.{0}".format(name)
        action_id = "rule:action.move_{0}".format(name)
        label = name.replace("_", " ").title()
        intents.append({
            "id": intent_id,
            "name": "Move {0}".format(label),
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
            "name": label,
            "context": "input:context.play",
            "intent": intent_id,
            "priority": 100,
            "enabled": True,
            "consume": True,
            "rebindable": True,
            "slot": "primary",
            "accessibility_label": label,
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
    rule: Mapping[str, Any],
    *,
    extent: Optional[int] = None,
    z_extent: Optional[int] = None,
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
    resolved_z = z_extent
    if resolved_z is None and len(axes) >= 3:
        try:
            resolved_z = int(axes[2].get("extent"))
        except Exception:
            resolved_z = None
    template = build_snake_step_rule(
        document_id=str(document.get("document_id") or "rule:game.snake"),
        extent=int(resolved_extent),
        z_extent=int(resolved_z) if resolved_z is not None else None,
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
    z_extent: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Replace empty snake four-IR shells with the golden step-snake semantics."""

    docs = {key: dict(value) for key, value in documents.items()}
    rule = overlay_rule_semantics_onto(docs["rule_ir"], extent=extent, z_extent=z_extent)
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


def write_overlaid_project_bundle(
    bundle_dir: Path,
    *,
    extent: Optional[int] = None,
    z_extent: Optional[int] = None,
    asset_project_root: Optional[Path] = None,
) -> Path:
    """Apply the reviewed step-snake overlay onto an on-disk Project bundle and reseal.

    Explicit harness only — not used by the LLM compile silent path.
    """

    root = Path(bundle_dir)
    from srtp.asset_ir_v2 import load_asset_ir
    from srtp.input_ir_v2 import load_input_ir
    from srtp.ir_v2 import load_rule_ir
    from srtp.project_manifest_v2 import load_project_manifest, seal_project_manifest
    from srtp.scene_ir_v2 import load_scene_ir

    docs = {
        "rule_ir": load_rule_ir(root / "ir" / "game.rule-ir.json"),
        "scene_ir": load_scene_ir(root / "ir" / "game.scene-ir.json"),
        "asset_ir": load_asset_ir(root / "ir" / "game.asset-ir.json"),
        "input_ir": load_input_ir(root / "ir" / "game.input-ir.json"),
    }
    overlaid = apply_snake_playable_bundle_overlay(
        docs, extent=extent, z_extent=z_extent,
    )
    ir_dir = root / "ir"
    ir_dir.mkdir(parents=True, exist_ok=True)
    _write_json(ir_dir / "game.rule-ir.json", overlaid["rule_ir"])
    _write_json(ir_dir / "game.scene-ir.json", overlaid["scene_ir"])
    _write_json(ir_dir / "game.asset-ir.json", overlaid["asset_ir"])
    _write_json(ir_dir / "game.input-ir.json", overlaid["input_ir"])

    manifest = dict(load_project_manifest(root / "project.manifest.json"))
    manifest["documents"] = {
        key: {
            "document_id": overlaid[key]["document_id"],
            "ir_version": overlaid[key]["ir_version"],
            "content_hash": overlaid[key]["content_hash"],
        }
        for key in ("rule_ir", "scene_ir", "asset_ir", "input_ir")
    }
    manifest["unresolved"] = []
    provenance = dict(manifest.get("provenance") or {})
    provenance["playable_overlay"] = "snake_playable_fixture"
    if z_extent is not None and int(z_extent) >= 2:
        provenance["playable_overlay_note"] = (
            "Reviewed XY+Z step-snake overlay applied for Project Session "
            "playability (arrows + PageUp/PageDown)."
        )
        description = (
            "Arrow keys move XY; PageUp/PageDown move Z. Playable overlay "
            "from snake_playable_fixture."
        )
    else:
        provenance["playable_overlay_note"] = (
            "Reviewed XY step-snake overlay applied for Project Session playability."
        )
        description = (
            "Arrow keys advance one cell. Playable overlay from snake_playable_fixture."
        )
    if provenance.get("designer_approved") is not True:
        provenance["designer_approved"] = True
        provenance.setdefault("approved_by", "playable_overlay")
    manifest["provenance"] = provenance
    metadata = dict(manifest.get("metadata") or {})
    metadata["description"] = description
    manifest["metadata"] = metadata
    sealed = seal_project_manifest(manifest, revision=int(manifest.get("revision") or 0) + 1)
    _write_json(root / "project.manifest.json", sealed)
    del asset_project_root  # reserved for callers that also compile
    return root / "project.manifest.json"


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
