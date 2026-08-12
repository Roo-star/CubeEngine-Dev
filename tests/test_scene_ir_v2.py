import json
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import canonical_rule_ir_hash, compile_rule_ir, new_rule_ir
from srtp.scene_ir_v2 import (
    SCENE_COMPILER_CAPABILITIES,
    SCENE_IR_PATCH_SCHEMA_PATH,
    SCENE_IR_SCHEMA_PATH,
    SCENE_IR_VERSION,
    SceneCompileError,
    SceneProjectionError,
    SceneIRPatchError,
    apply_scene_ir_patch,
    assess_scene_ir_conformance,
    canonical_scene_ir_hash,
    compile_scene_ir,
    identity_matrix,
    identity_transform,
    is_scene_ir_compile_ready,
    new_scene_ir,
    seal_scene_ir,
    validate_scene_ir,
)


ROOT = Path(__file__).resolve().parents[1]


def literal(value):
    return {"op": "literal", "value": value}


def rule_fixture():
    document = new_rule_ir("rule:game.scene_fixture", "Scene Fixture")
    document["participants"] = [{
        "id": "rule:participant.player", "name": "Player", "kind": "human_or_agent",
    }]
    document["topologies"] = [{
        "id": "rule:topology.board", "name": "Board", "kind": "rect_grid", "anchor": "cell",
        "axes": [
            {"name": "row", "extent": 2, "boundary": "bounded"},
            {"name": "column", "extent": 2, "boundary": "bounded"},
        ],
        "neighborhoods": [],
    }]
    document["state"] = {
        "variables": [
            {"id": "rule:state.score", "name": "Score", "type": "core:int", "scope": "global", "initial": literal(0)},
            {"id": "rule:state.cell", "name": "Cell", "type": "core:int", "scope": "topology_site", "topology": "rule:topology.board", "initial": literal(0)},
            {"id": "rule:state.ready", "name": "Ready", "type": "core:bool", "scope": "participant", "initial": literal(True)},
            {"id": "rule:state.selected", "name": "Selected", "type": "core:bool", "scope": "entity", "entity_type": "rule:entity.piece", "initial": literal(False)},
        ],
        "entity_types": [{
            "id": "rule:entity.piece", "name": "Piece",
            "components": [
                {"name": "position", "type": "core:coord", "default": literal([0, 0])},
                {"name": "label", "type": "core:string", "default": literal("seed")},
            ],
        }],
        "initial_effects": [
            {"op": "grid.set", "state": "rule:state.cell", "coordinate": literal([0, 0]), "value": literal(1)},
            {
                "op": "entity.spawn", "entity_type": "rule:entity.piece",
                "components": {"position": literal([0, 0]), "label": literal("seed")},
            },
        ],
        "information_model": "perfect",
    }
    common = {
        "actor": literal("rule:participant.player"), "parameters": [],
        "precondition": literal(True), "timing": {"phase": "rule:phase.input"},
        "encoding": {"kind": "finite_catalogue"},
    }
    move = dict(common)
    move.update({
        "id": "rule:action.move", "name": "Move",
        "effects": [
            {"op": "grid.set", "state": "rule:state.cell", "coordinate": literal([0, 0]), "value": literal(2)},
            {"op": "state.increment", "target": literal("rule:state.score"), "value": literal(1)},
            {"op": "state.set", "target": literal("rule:state.selected"), "scope": literal("entity:1"), "value": literal(True)},
            {"op": "entity.set", "entity": literal("entity:1"), "field": "position", "value": literal([1, 1])},
            {"op": "entity.set", "entity": literal("entity:1"), "field": "label", "value": literal("moved")},
        ],
    })
    despawn = dict(common)
    despawn.update({
        "id": "rule:action.despawn", "name": "Despawn",
        "effects": [{"op": "entity.despawn", "entity": literal("entity:1")}],
    })
    document["actions"] = [move, despawn]
    document["unresolved"] = []
    return document


def component(identifier, kind, properties):
    return {"id": identifier, "type": kind, "enabled": True, "properties": properties}


def prefab_node(local_id, components=(), children=()):
    return {
        "local_id": local_id, "name": local_id.title(), "active": True,
        "transform": identity_transform(), "components": list(components), "children": list(children),
    }


def scene_fixture(rule_document=None):
    rule_document = rule_document or rule_fixture()
    document = new_scene_ir("scene:game.scene_fixture", "Scene Fixture")
    document["dependencies"]["rule_ir"] = {
        "document_id": rule_document["document_id"],
        "content_hash": canonical_rule_ir_hash(rule_document),
    }
    document["layers"].append({
        "id": "scene:layer.editor", "name": "Editor", "kind": "editor",
        "visible": True, "pickable": True, "opacity": 0.5,
    })
    cell_renderer = component("renderer", "renderer", {
        "geometry": "builtin:cube", "visible": True, "opacity": 1.0, "variant": "empty",
    })
    cell_collider = component("collider", "collider", {
        "shape": "box", "size": [1.0, 1.0, 1.0],
        "is_trigger": False, "selectable": True,
    })
    document["prefabs"] = [
        {
            "id": "scene:prefab.cell", "name": "Cell",
            "root": prefab_node("root", [cell_renderer, cell_collider], [
                prefab_node("marker", [component("marker", "authoring_marker", {"visible": False, "label": "site"})]),
            ]),
        },
        {
            "id": "scene:prefab.piece", "name": "Piece",
            "root": prefab_node("root", [component("renderer", "renderer", {
                "geometry": "builtin:sphere", "visible": True, "opacity": 1.0,
                "variant": "seed",
            })]),
        },
    ]
    index_to_world = [
        2.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, -3.0, 0.0, 6.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    board_transform = identity_transform()
    board_transform["translation"] = [10.0, 0.0, 0.0]
    document["nodes"] = [
        {
            "id": "scene:node.board", "name": "Board", "parent": None,
            "active": True, "layer": "scene:layer.runtime", "transform": board_transform,
            "components": [component("sites", "topology_visualizer", {
                "rule_topology": "rule:topology.board", "prefab": "scene:prefab.cell",
                "index_to_world": index_to_world,
            })],
        },
        {
            "id": "scene:node.entities", "name": "Entities", "parent": None,
            "active": True, "layer": "scene:layer.runtime", "transform": identity_transform(),
            "components": [component("pieces", "rule_entity_visualizer", {
                "rule_entity_type": "rule:entity.piece", "prefab": "scene:prefab.piece",
                "coordinate_component": "position", "index_to_world": index_to_world,
            })],
        },
        {
            "id": "scene:node.hud", "name": "HUD", "parent": None,
            "active": True, "layer": "scene:layer.runtime", "transform": identity_transform(),
            "components": [component("canvas", "ui_canvas", {
                "mode": "overlay", "text": "", "visible": True, "value": "",
            })],
        },
        {
            "id": "scene:node.camera", "name": "Camera", "parent": None,
            "active": True, "layer": "scene:layer.runtime", "transform": identity_transform(),
            "components": [component("camera", "camera", {
                "projection": "perspective", "near_clip": 0.1, "far_clip": 1000.0,
                "active": True, "fov": 60.0,
            })],
        },
        {
            "id": "scene:node.preview_cell", "name": "Preview Cell", "parent": None,
            "active": True, "layer": "scene:layer.runtime", "transform": identity_transform(),
            "components": [], "prefab": "scene:prefab.cell",
            "overrides": {"/components/0/properties/opacity": 0.25},
        },
        {
            "id": "scene:node.editor_origin", "name": "Origin", "parent": None,
            "active": True, "layer": "scene:layer.editor", "transform": identity_transform(),
            "components": [component("marker", "authoring_marker", {"visible": True, "label": "origin"})],
        },
    ]
    document["bindings"] = [
        {
            "id": "scene:binding.cell_variant", "name": "Cell Variant",
            "source": {"kind": "state", "scope": "topology_site", "variable": "rule:state.cell"},
            "target": {
                "selector": "topology_sites", "node": "scene:node.board", "visualizer": "sites",
                "component": "renderer", "property": "variant",
            },
            "transform": {"kind": "map", "cases": [
                {"equals": 0, "value": "empty"}, {"equals": 1, "value": "source"},
                {"equals": 2, "value": "changed"},
            ]},
        },
        {
            "id": "scene:binding.score", "name": "Score",
            "source": {"kind": "state", "scope": "global", "variable": "rule:state.score"},
            "target": {"selector": "node", "node": "scene:node.hud", "component": "canvas", "property": "text"},
            "transform": {"kind": "format", "template": "Score: {value}"},
        },
        {
            "id": "scene:binding.ready", "name": "Ready",
            "source": {
                "kind": "state", "scope": "participant", "variable": "rule:state.ready",
                "participant": "rule:participant.player",
            },
            "target": {"selector": "node", "node": "scene:node.hud", "component": "canvas", "property": "visible"},
            "transform": {"kind": "direct"},
        },
        {
            "id": "scene:binding.selected", "name": "Selected",
            "source": {"kind": "state", "scope": "entity", "variable": "rule:state.selected"},
            "target": {
                "selector": "entity_nodes", "node": "scene:node.entities", "visualizer": "pieces",
                "component": "renderer", "property": "visible",
            },
            "transform": {"kind": "direct"},
        },
        {
            "id": "scene:binding.entity_label", "name": "Entity Label",
            "source": {"kind": "entity_component", "component": "label"},
            "target": {
                "selector": "entity_nodes", "node": "scene:node.entities", "visualizer": "pieces",
                "component": "renderer", "property": "variant",
            },
            "transform": {"kind": "direct"},
        },
        {
            "id": "scene:binding.phase", "name": "Phase",
            "source": {"kind": "flow", "property": "phase"},
            "target": {"selector": "node", "node": "scene:node.hud", "component": "canvas", "property": "value"},
            "transform": {"kind": "format", "template": "Phase: {value}"},
        },
    ]
    document["unresolved"] = []
    return document


class SceneIRContractTests(unittest.TestCase):
    def test_schema_and_capability_documents_are_valid_json(self):
        schema = json.loads(SCENE_IR_SCHEMA_PATH.read_text(encoding="utf-8"))
        patch_schema = json.loads(SCENE_IR_PATCH_SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["ir_version"]["const"], SCENE_IR_VERSION)
        self.assertIn("topology_visualizer", SCENE_COMPILER_CAPABILITIES["components"])
        self.assertIn("set_property", SCENE_COMPILER_CAPABILITIES["renderer_commands"])
        self.assertEqual(set(patch_schema["properties"]["operations"]["items"]["properties"]["op"]["enum"]), {
            "add", "remove", "replace", "move", "copy", "test",
        })

    def test_empty_draft_is_honest_and_not_compile_ready(self):
        document = new_scene_ir("scene:game.empty", "Empty")

        self.assertFalse(is_scene_ir_compile_ready(document))
        self.assertTrue(document["unresolved"][0]["required"])

    def test_sealing_is_stable_and_detects_semantic_change(self):
        document = scene_fixture()
        first = seal_scene_ir(document)
        second = seal_scene_ir(document)
        changed = deepcopy(first)
        changed["nodes"][0]["name"] = "Changed"

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["content_hash"], canonical_scene_ir_hash(first))
        self.assertNotEqual(first["content_hash"], canonical_scene_ir_hash(changed))

    def test_semantic_validator_rejects_cycle_duplicate_and_nonfinite_transform(self):
        cycle = scene_fixture()
        cycle["nodes"][0]["parent"] = "scene:node.camera"
        cycle["nodes"][3]["parent"] = "scene:node.board"
        malformed = scene_fixture()
        malformed["nodes"][1]["id"] = "scene:node.board"
        malformed["nodes"][2]["transform"]["translation"][0] = float("inf")

        codes = {
            item.code for document in (cycle, malformed)
            for item in validate_scene_ir(document)
        }

        self.assertIn("id.duplicate", codes)
        self.assertIn("node.cycle", codes)
        self.assertIn("transform.vector", codes)
        self.assertIn("number.finite", codes)


class SceneCompilerTests(unittest.TestCase):
    def test_othello_example_is_compile_ready_without_a_game_specific_renderer(self):
        scene_path = ROOT / "srtp" / "examples" / "scene_ir_v2" / "othello_board.scene-ir.json"
        rule_path = ROOT / "srtp" / "examples" / "rule_ir_v2" / "othello_2d.rule-ir.json"
        scene_document = json.loads(scene_path.read_text(encoding="utf-8"))
        rule_document = json.loads(rule_path.read_text(encoding="utf-8"))

        report = assess_scene_ir_conformance(scene_document, rule_document=rule_document)
        compiled = compile_scene_ir(scene_document, rule_document=rule_document)

        self.assertTrue(report.compile_ready)
        self.assertEqual(len(compiled.topology_sites["scene:node.board#sites"]), 64)
        self.assertEqual(compiled.nodes_by_id["scene:node.board.site.0_0"].rule_context["coordinate"], (0, 0))

    def test_compiler_expands_prefabs_and_logical_to_world_grid(self):
        rule = rule_fixture()
        scene = compile_scene_ir(scene_fixture(rule), rule_document=rule)

        site = scene.nodes_by_id["scene:node.board.site.1_1"]
        preview = scene.nodes_by_id["scene:node.preview_cell"]

        self.assertEqual(len(scene.topology_sites["scene:node.board#sites"]), 4)
        self.assertEqual(site.world_matrix[3], 12.0)
        self.assertEqual(site.world_matrix[7], 0.0)
        self.assertEqual(site.world_matrix[11], 3.0)
        self.assertIn("scene:node.board.site.1_1.marker", scene.nodes_by_id)
        self.assertEqual(preview.component("renderer").properties["opacity"], 0.25)

    def test_runtime_commands_exclude_editor_layer_without_losing_runtime_nodes(self):
        rule = rule_fixture()
        scene = compile_scene_ir(scene_fixture(rule), rule_document=rule)

        runtime = [item.to_mapping() for item in scene.build_commands("runtime")]
        editor = [item.to_mapping() for item in scene.build_commands("editor")]
        runtime_nodes = {item["node_id"] for item in runtime if item["op"] == "create_node"}
        editor_nodes = {item["node_id"] for item in editor if item["op"] == "create_node"}

        self.assertNotIn("scene:node.editor_origin", runtime_nodes)
        self.assertIn("scene:node.editor_origin", editor_nodes)
        self.assertIn("scene:node.board.site.0_0", runtime_nodes)
        self.assertTrue(any(item["op"] == "define_prefab" for item in runtime))

    def test_compile_rejects_wrong_rule_pin_and_unsafe_prefab_override(self):
        rule = rule_fixture()
        wrong_pin = scene_fixture(rule)
        wrong_pin["dependencies"]["rule_ir"]["content_hash"] = "0" * 64
        unsafe = scene_fixture(rule)
        unsafe["nodes"][4]["overrides"] = {"/components/0/type": "light"}

        with self.assertRaisesRegex(SceneCompileError, "hash"):
            compile_scene_ir(wrong_pin, rule_document=rule)
        with self.assertRaisesRegex(SceneCompileError, "identity/type"):
            compile_scene_ir(unsafe, rule_document=rule)

    def test_conformance_reports_missing_cross_document_dependency_as_blocked(self):
        rule = rule_fixture()
        document = scene_fixture(rule)

        report = assess_scene_ir_conformance(document)

        self.assertTrue(report.structurally_valid)
        self.assertFalse(report.compile_ready)
        self.assertIn("compiler.blocked", {item.code for item in report.diagnostics})


class ScenePatchTests(unittest.TestCase):
    def test_patch_is_atomic_revisioned_and_recompilable(self):
        rule = rule_fixture()
        base = seal_scene_ir(scene_fixture(rule))
        proposal = {
            "document_id": base["document_id"],
            "base_revision": base["revision"],
            "base_content_hash": base["content_hash"],
            "operations": [{
                "op": "replace", "path": "/nodes/0/transform/translation",
                "value": [20.0, 0.0, 0.0],
            }],
            "evidence": [{"author": "designer", "reason": "move board"}],
            "assumptions": [], "unresolved": [],
        }

        updated = apply_scene_ir_patch(base, proposal)
        compiled = compile_scene_ir(updated, rule_document=rule)

        self.assertEqual(base["nodes"][0]["transform"]["translation"], [10.0, 0.0, 0.0])
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["content_hash"], canonical_scene_ir_hash(updated))
        self.assertEqual(compiled.nodes_by_id["scene:node.board"].world_matrix[3], 20.0)
        self.assertEqual(updated["provenance"]["patch_history"][0]["operation_count"], 1)

    def test_patch_rejects_stale_protected_and_invalid_hierarchy_changes(self):
        base = seal_scene_ir(scene_fixture())
        common = {
            "document_id": base["document_id"], "base_revision": base["revision"],
            "base_content_hash": base["content_hash"],
            "evidence": [{"author": "designer"}], "assumptions": [], "unresolved": [],
        }
        stale = dict(common, base_revision=99, operations=[{"op": "replace", "path": "/metadata/title", "value": "X"}])
        protected = dict(common, operations=[{"op": "replace", "path": "/document_id", "value": "scene:game.other"}])
        cycle = dict(common, operations=[
            {"op": "replace", "path": "/nodes/0/parent", "value": "scene:node.camera"},
            {"op": "replace", "path": "/nodes/3/parent", "value": "scene:node.board"},
        ])

        with self.assertRaisesRegex(SceneIRPatchError, "stale"):
            apply_scene_ir_patch(base, stale)
        with self.assertRaisesRegex(SceneIRPatchError, "protected"):
            apply_scene_ir_patch(base, protected)
        with self.assertRaisesRegex(SceneIRPatchError, "parent cycle"):
            apply_scene_ir_patch(base, cycle)


class SceneProjectionTests(unittest.TestCase):
    def test_rule_state_projects_all_scopes_without_mutating_authority(self):
        rule = rule_fixture()
        runtime = compile_rule_ir(rule)
        scene = compile_scene_ir(scene_fixture(rule), rule_document=rule)
        session = scene.create_projection_session()
        before = runtime.state.state_hash()

        first = session.synchronize(runtime.state)
        second = session.synchronize(runtime.state)

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertFalse(second.commands)
        mappings = [item.to_mapping() for item in first.commands]
        self.assertTrue(any(item["op"] == "create_prefab_instance" for item in mappings))
        self.assertTrue(any(item["payload"].get("value") == "source" for item in mappings))
        self.assertTrue(any(item["payload"].get("value") == "Score: 0" for item in mappings))
        self.assertTrue(any(item["payload"].get("value") == "seed" for item in mappings))

    def test_rule_transition_emits_only_changed_scene_commands(self):
        rule = rule_fixture()
        runtime = compile_rule_ir(rule)
        scene = compile_scene_ir(scene_fixture(rule), rule_document=rule)
        session = scene.create_projection_session()
        session.synchronize(runtime.state)

        runtime.apply_action(0)
        before = runtime.state.state_hash()
        delta = session.synchronize(runtime.state)
        mappings = [item.to_mapping() for item in delta.commands]

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertTrue(any(item["op"] == "set_transform" for item in mappings))
        values = [item["payload"].get("value") for item in mappings if item["op"] == "set_property"]
        self.assertIn("changed", values)
        self.assertIn("Score: 1", values)
        self.assertIn("moved", values)
        self.assertIn(True, values)

        runtime.apply_action(1)
        removed = [item.to_mapping() for item in session.synchronize(runtime.state).commands]
        self.assertTrue(any(item["op"] == "destroy_node" for item in removed))

    def test_failed_projection_does_not_advance_incremental_session_cache(self):
        rule = rule_fixture()
        runtime = compile_rule_ir(rule)
        scene = compile_scene_ir(scene_fixture(rule), rule_document=rule)
        session = scene.create_projection_session()
        session.synchronize(runtime.state)
        runtime.state.entities["entity:1"]["components"]["position"] = [1, 1]
        runtime.state.grids["rule:state.cell"][0, 0] = 99

        with self.assertRaisesRegex(SceneProjectionError, "no case"):
            session.synchronize(runtime.state)

        runtime.state.grids["rule:state.cell"][0, 0] = 1
        retry = [item.to_mapping() for item in session.synchronize(runtime.state).commands]
        self.assertTrue(any(item["op"] == "set_transform" for item in retry))


if __name__ == "__main__":
    unittest.main()
