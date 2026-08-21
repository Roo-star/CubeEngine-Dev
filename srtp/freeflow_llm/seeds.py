"""Seed sealed four-IR bases from Function 1 without inventing mechanics."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Mapping

from srtp.asset_ir_v2 import new_asset_ir, seal_asset_ir
from srtp.input_ir_v2 import new_input_ir, seal_input_ir
from srtp.ir_v2 import new_rule_ir, seal_rule_ir, upgrade_rule_schema_v1
from srtp.project_manifest_v2 import new_project_manifest, seal_project_manifest
from srtp.scene_ir_v2 import identity_matrix, identity_transform, new_scene_ir, seal_scene_ir
from srtp.source_game import SourceGamePackage

from .evidence import sha256_json


def game_slug(package: SourceGamePackage) -> str:
    schema = package.rule_report.schema if package.rule_report is not None else {}
    game = schema.get("game") if isinstance(schema, Mapping) else {}
    raw = ""
    if isinstance(game, Mapping):
        raw = str(game.get("id") or game.get("name") or "")
    if not raw:
        raw = package.entrypoint.stem or package.title
    return _slug(raw)


def seed_documents(package: SourceGamePackage, *, variant: str = "source") -> Dict[str, Any]:
    slug = game_slug(package)
    source_hash = sha256_json({"entrypoint": str(package.entrypoint), "title": package.title})
    rule = _seed_rule(package, slug, source_hash)
    asset = _seed_asset(slug, source_hash)
    scene = _seed_scene(slug, source_hash)
    input_document = _seed_input(slug, source_hash)
    project_id = "project:game.{0}_{1}".format(slug, variant)
    manifest = new_project_manifest(project_id, package.title or slug, variant=variant)
    manifest["metadata"]["description"] = "FreeFlow-LLM seed project. Pins are filled after validated patches."
    return {
        "slug": slug,
        "rule_ir": rule,
        "scene_ir": scene,
        "asset_ir": asset,
        "input_ir": input_document,
        "manifest": seal_project_manifest(manifest),
    }


def base_documents(documents: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        slot: {
            "document_id": documents[slot]["document_id"],
            "revision": int(documents[slot]["revision"]),
            "content_hash": str(documents[slot]["content_hash"]),
        }
        for slot in ("rule_ir", "scene_ir", "asset_ir", "input_ir")
    }


def _seed_rule(package: SourceGamePackage, slug: str, source_hash: str) -> Dict[str, Any]:
    schema = package.rule_report.schema if package.rule_report is not None else {}
    document_id = "rule:game.{0}".format(slug)
    try:
        document = upgrade_rule_schema_v1(schema)
        document["document_id"] = document_id
    except (TypeError, ValueError):
        document = new_rule_ir(document_id, package.title or slug)
    document["metadata"]["title"] = package.title or slug
    document["metadata"]["source_project_hash"] = source_hash
    return seal_rule_ir(document)


def _seed_scene(slug: str, source_hash: str) -> Dict[str, Any]:
    document = new_scene_ir("scene:game.{0}".format(slug), "{0} scene seed".format(slug))
    document["metadata"]["source_project_hash"] = source_hash
    return seal_scene_ir(document)


def _seed_asset(slug: str, source_hash: str) -> Dict[str, Any]:
    document = new_asset_ir("asset:game.{0}".format(slug), "{0} asset seed".format(slug))
    document["metadata"]["source_project_hash"] = source_hash
    return seal_asset_ir(document)


def _seed_input(slug: str, source_hash: str) -> Dict[str, Any]:
    document = new_input_ir("input:game.{0}".format(slug), "{0} input seed".format(slug))
    document["metadata"]["source_project_hash"] = source_hash
    return seal_input_ir(document)


def placement_asset_body(slug: str) -> Dict[str, Any]:
    return {
        "derivations": [{
            "id": "asset:model.cell_cube_{0}".format(slug),
            "name": "Cell Cube",
            "kind": "model",
            "media_type": "application/vnd.cubeengine.presentation+json",
            "strategy": "procedural_mesh",
            "inputs": [],
            "settings": {"primitive": "cube", "dimensions": [1.0, 1.0, 1.0]},
            "expected_content_hash": "",
            "license_policy": "inherit",
        }],
        "roles": [{
            "id": "asset:role.board_cell_{0}".format(slug),
            "name": "Board Cell",
            "semantic": "board.cell",
            "resource": "asset:model.cell_cube_{0}".format(slug),
            "usage": "world_mesh",
            "required": True,
        }],
        "unresolved": [],
    }


def placement_scene_body(slug: str, rule_id: str, asset_id: str) -> Dict[str, Any]:
    resource = "asset:model.cell_cube_{0}".format(slug)
    return {
        "dependencies": {
            "rule_ir": {"document_id": rule_id, "content_hash": "$pin:rule_ir"},
            "asset_ir": {"document_id": asset_id, "content_hash": "$pin:asset_ir"},
            "extensions": [],
        },
        "prefabs": [{
            "id": "scene:prefab.cell", "name": "Cell",
            "root": {
                "local_id": "root", "name": "Cell", "active": True,
                "transform": identity_transform(),
                "components": [
                    {
                        "id": "renderer", "type": "renderer", "enabled": True,
                        "properties": {
                            "geometry": resource, "visible": True,
                            "opacity": 1.0, "variant": "empty",
                        },
                    },
                    {
                        "id": "collider", "type": "collider", "enabled": True,
                        "properties": {
                            "shape": "box", "size": [1.0, 1.0, 1.0],
                            "is_trigger": False, "selectable": True,
                        },
                    },
                ],
                "children": [],
            },
        }],
        "nodes": [{
            "id": "scene:node.board", "name": "Board", "parent": None,
            "active": True, "layer": "scene:layer.runtime",
            "transform": identity_transform(),
            "components": [{
                "id": "sites", "type": "topology_visualizer", "enabled": True,
                "properties": {
                    "rule_topology": "rule:topology.board",
                    "prefab": "scene:prefab.cell",
                    "index_to_world": identity_matrix(),
                },
            }],
        }],
        "bindings": [{
            "id": "scene:binding.cell_variant", "name": "Cell Variant",
            "source": {
                "kind": "state", "scope": "topology_site",
                "variable": "rule:state.board_cell",
            },
            "target": {
                "selector": "topology_sites", "node": "scene:node.board",
                "visualizer": "sites", "component": "renderer", "property": "variant",
            },
            "transform": {
                "kind": "map", "cases": [
                    {"equals": 0, "value": "empty"},
                    {"equals": 1, "value": "positive"},
                    {"equals": -1, "value": "negative"},
                ],
            },
        }],
        "unresolved": [],
    }


def placement_input_body(rule_id: str) -> Dict[str, Any]:
    from srtp.input_ir_v2 import default_processing

    return {
        "dependencies": {
            "rule_ir": {"document_id": rule_id, "content_hash": "$pin:rule_ir"},
            "extensions": [],
        },
        "contexts": [{
            "id": "input:context.play", "name": "Play", "priority": 100,
            "enabled_by_default": True, "focus": "viewport",
            "consume_policy": "first_match", "exclusive_group": "runtime_mode",
        }],
        "intents": [{
            "id": "input:action.place", "name": "Place", "value_type": "digital",
            "required": True,
            "target": {
                "kind": "rule_action", "action": "rule:action.place",
                "parameters": {
                    "target": {
                        "source": "event_data", "key": "rule_coordinate",
                        "value_type": "core:coord",
                    },
                },
            },
        }],
        "bindings": [{
            "id": "input:binding.place_mouse", "name": "Place with Mouse",
            "context": "input:context.play", "intent": "input:action.place",
            "priority": 100, "enabled": True, "consume": True,
            "rebindable": True, "slot": "primary", "accessibility_label": "Place",
            "trigger": {
                "kind": "control", "device": "mouse",
                "control": "mouse.button.primary", "phase": "press",
                "modifiers": [], "modifier_policy": "exact",
            },
            "processing": default_processing(),
        }],
        "unresolved": [],
    }


def tictactoe_source_rule_fields(document_id: str, source_hash: str = "") -> Dict[str, Any]:
    from pathlib import Path
    from srtp.ir_v2 import load_rule_ir

    path = Path(__file__).resolve().parents[1] / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
    document = deepcopy(load_rule_ir(path))
    document["document_id"] = document_id
    document["metadata"]["title"] = "2D Tic-Tac-Toe Source Contract"
    document["metadata"]["description"] = "Source-plane reconstruction of the tictactoe_2d Function 1 fixture."
    document["metadata"]["source_project_hash"] = source_hash
    document["topologies"][0]["name"] = "2D Source Board"
    document["topologies"][0]["axes"] = document["topologies"][0]["axes"][:2]
    document["content_hash"] = ""
    document["unresolved"] = []
    return document


def tictactoe_target_rule_fields(document_id: str, source_hash: str = "") -> Dict[str, Any]:
    from pathlib import Path
    from srtp.ir_v2 import load_rule_ir

    path = Path(__file__).resolve().parents[1] / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
    document = deepcopy(load_rule_ir(path))
    document["document_id"] = document_id
    document["metadata"]["title"] = "3D Tic-Tac-Toe Target Contract"
    document["metadata"]["source_project_hash"] = source_hash
    document["content_hash"] = ""
    document["unresolved"] = []
    return document


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    if not text:
        return "unnamed"
    if not text[0].isalpha():
        text = "id_" + text
    return text
