"""Runnable reference fixture for the complete non-LLM integration gate."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from srtp.alphazero_v1 import load_alphazero_manifest
from srtp.asset_ir_v2 import new_asset_ir, seal_asset_ir
from srtp.extension_sdk import ExtensionRegistry
from srtp.input_ir_v2 import default_processing, new_input_ir, seal_input_ir
from srtp.ir_v2 import load_rule_ir, seal_rule_ir
from srtp.project_manifest_v2 import new_project_manifest, seal_project_manifest
from srtp.scene_ir_v2 import (
    identity_matrix, identity_transform, new_scene_ir, seal_scene_ir,
)

from .manifest import new_integration_gate, seal_integration_gate
from .runner import ProjectArtifacts, run_non_llm_integration_gate


EXTENSION_CAPABILITY = "extension:cubeengine.parity/is_even/1"


@dataclass(frozen=True)
class ReferenceGateFixture:
    gate: Any
    source: ProjectArtifacts
    target: ProjectArtifacts
    ai_manifest: Any
    extension_registry: ExtensionRegistry

    def run(self):
        return run_non_llm_integration_gate(
            self.gate,
            source=self.source,
            target=self.target,
            ai_manifest=self.ai_manifest,
            extension_registry=self.extension_registry,
        )


def build_reference_gate(repository_root: Path = None) -> ReferenceGateFixture:
    root = Path(repository_root or Path(__file__).resolve().parents[2]).resolve()
    target_rule_path = root / "srtp" / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
    ai_path = root / "srtp" / "examples" / "alphazero_v1" / "tictactoe_3d.alphazero.json"
    extension_root = root / "srtp" / "examples" / "extensions" / "parity"

    target_rule = seal_rule_ir(load_rule_ir(target_rule_path))
    source_rule = _source_rule(target_rule)
    source = build_project_artifacts(
        source_rule, "project:game.tictactoe_2d_source", root,
    )
    target = build_project_artifacts(
        target_rule, "project:game.tictactoe_3d_target", root,
        source_manifest=source.manifest,
    )
    ai_manifest = load_alphazero_manifest(ai_path)
    registry = ExtensionRegistry()
    package = registry.register(extension_root)

    gate = new_integration_gate(
        "gate:tictactoe_source_to_3d", "Tic-Tac-Toe Source-to-3D Gate",
    )
    gate["metadata"]["description"] = (
        "Reference proof for source/target projects, four IRs, replay, "
        "Extension isolation and AlphaZero compilation."
    )
    gate["source_project"] = _project_pin(source.manifest)
    gate["target_project"] = _project_pin(target.manifest)
    gate["ai_adapter"] = {
        "adapter_id": ai_manifest["adapter_id"],
        "content_hash": ai_manifest["content_hash"],
    }
    gate["extension_probes"] = [{
        "extension_id": package.extension_id,
        "version": package.version,
        "content_hash": package.content_hash,
        "cases": [{
            "capability_id": EXTENSION_CAPABILITY,
            "requests": [{"arguments": [2]}, {"arguments": [3]}],
        }],
    }]
    gate["acceptance"] = {
        "source_inputs": [_input_step(1, [0, 0])],
        "target_inputs": [_input_step(1, [0, 0, 0])],
        "ai_maximum_plies": 64,
    }
    gate["provenance"] = {
        "/source_project": [{"author": "engineer", "method": "sealed_reference_fixture"}],
        "/target_project": [{"author": "engineer", "method": "sealed_reference_fixture"}],
        "/ai_adapter": [{"author": "engineer", "method": "package_9_acceptance"}],
        "/extension_probes": [{"author": "engineer", "method": "package_8_acceptance"}],
    }
    gate["unresolved"] = []
    return ReferenceGateFixture(
        seal_integration_gate(gate), source, target, ai_manifest, registry,
    )


def run_reference_gate(repository_root: Path = None):
    return build_reference_gate(repository_root).run()


def _source_rule(target_rule):
    document = deepcopy(target_rule)
    document["document_id"] = "rule:game.tictactoe_2d_source"
    document["metadata"]["title"] = "2D Tic-Tac-Toe Source Contract"
    document["metadata"]["description"] = (
        "Source-plane fixture for the final source-to-3D integration gate."
    )
    document["topologies"][0]["name"] = "2D Source Board"
    document["topologies"][0]["axes"] = document["topologies"][0]["axes"][:2]
    document["content_hash"] = ""
    document["provenance"]["/topologies/0"] = [{
        "author": "engineer", "method": "integration_source_fixture", "confidence": 1.0,
    }]
    return seal_rule_ir(document)


def build_project_artifacts(
    rule, project_id, repository_root=None, source_manifest=None,
):
    """Build a minimal four-IR placement project for acceptance/preview hosts.

    This helper does not infer gameplay. The supplied Rule IR remains the
    authority; it only creates a procedural cell presentation and a typed
    primary-click binding for the declared placement contract.
    """

    root = Path(
        repository_root or Path(__file__).resolve().parents[2]
    ).resolve()
    asset = _asset_document(project_id)
    scene = _scene_document(rule, asset, project_id)
    input_document = _input_document(rule, project_id)
    manifest = new_project_manifest(project_id, project_id)
    for slot, document in (
        ("rule_ir", rule), ("scene_ir", scene),
        ("asset_ir", asset), ("input_ir", input_document),
    ):
        manifest["documents"][slot] = {
            "document_id": document["document_id"],
            "ir_version": document["ir_version"],
            "content_hash": document["content_hash"],
        }
    if source_manifest is not None:
        manifest["variant"] = "target"
        manifest["source_manifest"] = _project_pin(source_manifest)
    manifest["unresolved"] = []
    manifest = seal_project_manifest(manifest)
    return ProjectArtifacts(manifest, rule, scene, asset, input_document, root)


def _asset_document(project_id):
    suffix = project_id.rsplit(".", 1)[-1]
    document = new_asset_ir("asset:game." + suffix, "Integration Cell Asset")
    document["derivations"] = [{
        "id": "asset:model.cell_cube_" + suffix,
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
        "id": "asset:role.board_cell_" + suffix,
        "name": "Board Cell",
        "semantic": "board.cell",
        "resource": "asset:model.cell_cube_" + suffix,
        "usage": "world_mesh",
        "required": True,
    }]
    document["unresolved"] = []
    return seal_asset_ir(document)


def _component(identifier, kind, properties):
    return {"id": identifier, "type": kind, "enabled": True, "properties": properties}


def _scene_document(rule, asset, project_id):
    suffix = project_id.rsplit(".", 1)[-1]
    resource = "asset:model.cell_cube_" + suffix
    document = new_scene_ir("scene:game." + suffix, "Integration Scene")
    document["dependencies"]["rule_ir"] = {
        "document_id": rule["document_id"], "content_hash": rule["content_hash"],
    }
    document["dependencies"]["asset_ir"] = {
        "document_id": asset["document_id"], "content_hash": asset["content_hash"],
    }
    document["prefabs"] = [{
        "id": "scene:prefab.cell", "name": "Cell",
        "root": {
            "local_id": "root", "name": "Cell", "active": True,
            "transform": identity_transform(),
            "components": [
                _component("renderer", "renderer", {
                    "geometry": resource, "visible": True,
                    "opacity": 1.0, "variant": "empty",
                }),
                _component("collider", "collider", {
                    "shape": "box", "size": [1.0, 1.0, 1.0],
                    "is_trigger": False, "selectable": True,
                }),
            ],
            "children": [],
        },
    }]
    document["nodes"] = [{
        "id": "scene:node.board", "name": "Board", "parent": None,
        "active": True, "layer": "scene:layer.runtime",
        "transform": identity_transform(),
        "components": [_component("sites", "topology_visualizer", {
            "rule_topology": "rule:topology.board",
            "prefab": "scene:prefab.cell",
            "index_to_world": identity_matrix(),
        })],
    }]
    document["bindings"] = [{
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
    }]
    document["unresolved"] = []
    return seal_scene_ir(document)


def _input_document(rule, project_id):
    suffix = project_id.rsplit(".", 1)[-1]
    document = new_input_ir("input:game." + suffix, "Integration Input")
    document["dependencies"]["rule_ir"] = {
        "document_id": rule["document_id"], "content_hash": rule["content_hash"],
    }
    document["contexts"] = [{
        "id": "input:context.play", "name": "Play", "priority": 100,
        "enabled_by_default": True, "focus": "viewport",
        "consume_policy": "first_match", "exclusive_group": "runtime_mode",
    }]
    document["intents"] = [{
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
    }]
    document["bindings"] = [{
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
    }]
    document["unresolved"] = []
    return seal_input_ir(document)


def _input_step(sequence, coordinate):
    return {
        "event": {
            "sequence": sequence,
            "device": "mouse",
            "control": "mouse.button.primary",
            "phase": "press",
            "position": [10, 10],
            "data": {"rule_coordinate": coordinate},
        },
        "active_contexts": None,
        "focus": "viewport",
    }


def _project_pin(manifest):
    return {
        "project_id": manifest["project_id"],
        "content_hash": manifest["content_hash"],
    }


if __name__ == "__main__":
    print(json.dumps(run_reference_gate().to_mapping(), ensure_ascii=False, indent=2))
