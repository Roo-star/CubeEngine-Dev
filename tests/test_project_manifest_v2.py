import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.asset_ir_v2 import new_asset_ir, seal_asset_ir
from srtp.input_ir_v2 import (
    PhysicalInputEvent,
    default_processing,
    new_input_ir,
    seal_input_ir,
)
from srtp.ir_v2 import canonical_rule_ir_hash, seal_rule_ir
from srtp.project_manifest_v2 import (
    PROJECT_COMPILER_CAPABILITIES,
    PROJECT_COMPILER_CAPABILITY_ID,
    PROJECT_MANIFEST_PATCH_SCHEMA_PATH,
    PROJECT_MANIFEST_SCHEMA_PATH,
    PROJECT_MANIFEST_VERSION,
    ProjectCompileError,
    ProjectManifestPatchError,
    apply_project_manifest_patch,
    assess_project_conformance,
    canonical_project_manifest_hash,
    compile_project_manifest,
    is_project_manifest_compile_ready,
    new_project_manifest,
    seal_project_manifest,
    validate_project_manifest,
)
from srtp.scene_ir_v2 import identity_matrix, identity_transform, new_scene_ir, seal_scene_ir


ROOT = Path(__file__).resolve().parents[1]


def rule_fixture():
    document = json.loads((
        ROOT / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"
    ).read_text(encoding="utf-8"))
    return seal_rule_ir(document)


def asset_fixture():
    document = new_asset_ir("asset:game.project_fixture", "Project Fixture Assets")
    document["derivations"] = [{
        "id": "asset:model.cell_cube",
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
        "id": "asset:role.board_cell",
        "name": "Board Cell",
        "semantic": "board.cell",
        "resource": "asset:model.cell_cube",
        "usage": "world_mesh",
        "required": True,
    }]
    document["unresolved"] = []
    return seal_asset_ir(document)


def _component(identifier, kind, properties):
    return {"id": identifier, "type": kind, "enabled": True, "properties": properties}


def scene_fixture(rule, asset):
    document = new_scene_ir("scene:game.project_fixture", "Project Fixture Scene")
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
            "local_id": "root", "name": "Cell", "active": True,
            "transform": identity_transform(),
            "components": [
                _component("renderer", "renderer", {
                    "geometry": "asset:model.cell_cube", "visible": True,
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
        "id": "scene:binding.cell_variant",
        "name": "Cell Variant",
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
                {"equals": 1, "value": "occupied"},
            ],
        },
    }]
    document["unresolved"] = []
    return seal_scene_ir(document)


def input_fixture(rule):
    document = new_input_ir("input:game.project_fixture", "Project Fixture Input")
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


def manifest_fixture(rule, scene, asset, input_document, project_id="project:game.fixture"):
    document = new_project_manifest(project_id, "Project Fixture")
    source = {
        "rule_ir": rule, "scene_ir": scene,
        "asset_ir": asset, "input_ir": input_document,
    }
    for slot, item in source.items():
        document["documents"][slot] = {
            "document_id": item["document_id"],
            "ir_version": item["ir_version"],
            "content_hash": item["content_hash"],
        }
    document["unresolved"] = []
    return seal_project_manifest(document)


def four_ir_fixture():
    rule = rule_fixture()
    asset = asset_fixture()
    scene = scene_fixture(rule, asset)
    input_document = input_fixture(rule)
    manifest = manifest_fixture(rule, scene, asset, input_document)
    return manifest, rule, scene, asset, input_document


class ProjectManifestContractTests(unittest.TestCase):
    def test_schema_capability_and_honest_draft(self):
        schema = json.loads(PROJECT_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        patch_schema = json.loads(PROJECT_MANIFEST_PATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
        draft = new_project_manifest("project:game.empty", "Empty")

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["manifest_version"]["const"], PROJECT_MANIFEST_VERSION)
        self.assertEqual(set(PROJECT_COMPILER_CAPABILITIES["required_documents"]), {
            "rule_ir", "scene_ir", "asset_ir", "input_ir",
        })
        self.assertFalse(PROJECT_COMPILER_CAPABILITIES["input_may_write_rule_state"])
        self.assertIn("move", patch_schema["properties"]["operations"]["items"]["properties"]["op"]["enum"])
        self.assertFalse(is_project_manifest_compile_ready(draft))

    def test_fixture_is_valid_sealed_and_compile_ready(self):
        manifest, _, _, _, _ = four_ir_fixture()

        self.assertFalse(validate_project_manifest(manifest))
        self.assertTrue(is_project_manifest_compile_ready(manifest))
        self.assertEqual(manifest["content_hash"], canonical_project_manifest_hash(manifest))


class ProjectCompilerIntegrationTests(unittest.TestCase):
    def test_four_ir_bundle_runs_mouse_to_rule_to_scene_pipeline(self):
        manifest, rule, scene, asset, input_document = four_ir_fixture()
        with tempfile.TemporaryDirectory() as folder:
            bundle = compile_project_manifest(
                manifest, rule_document=rule, scene_document=scene,
                asset_document=asset, input_document=input_document,
                asset_project_root=Path(folder),
            )
            session = bundle.create_session()
            before = session.rule_runtime.state.state_hash()

            first = session.handle_input(PhysicalInputEvent(
                1, "mouse", "mouse.button.primary", "press",
                position=(320, 200), data={"rule_coordinate": [1, 1, 1]},
            ))
            first_mapping = first.to_mapping()

            self.assertNotEqual(session.rule_runtime.state.state_hash(), before)
            self.assertEqual(len(first.transitions), 1)
            self.assertFalse(first.rejections)
            self.assertEqual(first_mapping["transitions"][0]["action"]["action_id"], "rule:action.place")
            self.assertTrue(any(
                item.op == "set_property" and item.payload.get("value") == "occupied"
                for item in first.scene_delta.commands
            ))

            after_first = session.rule_runtime.state.state_hash()
            illegal = session.handle_input(PhysicalInputEvent(
                2, "mouse", "mouse.button.primary", "press",
                position=(320, 200), data={"rule_coordinate": [1, 1, 1]},
            ))
            self.assertEqual(session.rule_runtime.state.state_hash(), after_first)
            self.assertFalse(illegal.transitions)
            self.assertEqual(illegal.rejections[0].code, "rule_action_illegal")

    def test_hash_and_cross_ir_dependency_mismatches_are_blocked(self):
        manifest, rule, scene, asset, input_document = four_ir_fixture()
        wrong_manifest = deepcopy(manifest)
        wrong_manifest["documents"]["rule_ir"]["content_hash"] = "0" * 64
        wrong_manifest = seal_project_manifest(wrong_manifest)
        wrong_scene = deepcopy(scene)
        wrong_scene["dependencies"]["asset_ir"]["content_hash"] = "f" * 64
        wrong_scene = seal_scene_ir(wrong_scene)
        wrong_scene_manifest = deepcopy(manifest)
        wrong_scene_manifest["documents"]["scene_ir"]["content_hash"] = wrong_scene["content_hash"]
        wrong_scene_manifest = seal_project_manifest(wrong_scene_manifest)

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ProjectCompileError, "manifest rule_ir hash"):
                compile_project_manifest(
                    wrong_manifest, rule_document=rule, scene_document=scene,
                    asset_document=asset, input_document=input_document,
                    asset_project_root=Path(folder),
                )
            with self.assertRaisesRegex(ProjectCompileError, "Scene IR does not pin"):
                compile_project_manifest(
                    wrong_scene_manifest, rule_document=rule, scene_document=wrong_scene,
                    asset_document=asset, input_document=input_document,
                    asset_project_root=Path(folder),
                )

    def test_target_variant_requires_exact_source_manifest_lineage(self):
        source, rule, scene, asset, input_document = four_ir_fixture()
        target = manifest_fixture(
            rule, scene, asset, input_document, "project:game.fixture_3d",
        )
        target["variant"] = "target"
        target["source_manifest"] = {
            "project_id": source["project_id"], "content_hash": source["content_hash"],
        }
        target = seal_project_manifest(target)

        with tempfile.TemporaryDirectory() as folder:
            compiled = compile_project_manifest(
                target, rule_document=rule, scene_document=scene,
                asset_document=asset, input_document=input_document,
                asset_project_root=Path(folder), source_manifest=source,
            )
            self.assertEqual(compiled.variant, "target")
            corrupted = deepcopy(source)
            corrupted["metadata"]["description"] = "changed"
            with self.assertRaisesRegex(ProjectCompileError, "not sealed"):
                compile_project_manifest(
                    target, rule_document=rule, scene_document=scene,
                    asset_document=asset, input_document=input_document,
                    asset_project_root=Path(folder), source_manifest=corrupted,
                )


class ProjectManifestChangeProtocolTests(unittest.TestCase):
    def test_patch_is_atomic_revisioned_and_preserves_document_pins(self):
        manifest, _, _, _, _ = four_ir_fixture()
        proposal = {
            "project_id": manifest["project_id"],
            "base_revision": manifest["revision"],
            "base_content_hash": manifest["content_hash"],
            "operations": [{
                "op": "replace", "path": "/metadata/description",
                "value": "Designer-approved project description",
            }],
            "evidence": [{"author": "designer", "reason": "metadata correction"}],
            "assumptions": [], "unresolved": [],
        }

        updated = apply_project_manifest_patch(manifest, proposal)

        self.assertEqual(manifest["metadata"]["description"], "")
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["documents"], manifest["documents"])
        self.assertEqual(updated["content_hash"], canonical_project_manifest_hash(updated))

    def test_patch_rejects_stale_and_protected_project_identity(self):
        manifest, _, _, _, _ = four_ir_fixture()
        common = {
            "project_id": manifest["project_id"],
            "base_revision": manifest["revision"],
            "base_content_hash": manifest["content_hash"],
            "evidence": [{"author": "designer"}], "assumptions": [], "unresolved": [],
        }
        stale = dict(common, base_revision=99, operations=[{
            "op": "replace", "path": "/metadata/title", "value": "X",
        }])
        protected = dict(common, operations=[{
            "op": "replace", "path": "/project_id", "value": "project:game.other",
        }])
        with self.assertRaisesRegex(ProjectManifestPatchError, "stale"):
            apply_project_manifest_patch(manifest, stale)
        with self.assertRaisesRegex(ProjectManifestPatchError, "protected"):
            apply_project_manifest_patch(manifest, protected)


class ProjectManifestConformanceTests(unittest.TestCase):
    def test_conformance_requires_documents_and_reports_integrated_readiness(self):
        manifest, rule, scene, asset, input_document = four_ir_fixture()
        missing = assess_project_conformance(manifest)
        with tempfile.TemporaryDirectory() as folder:
            ready = assess_project_conformance(
                manifest, rule_document=rule, scene_document=scene,
                asset_document=asset, input_document=input_document,
                asset_project_root=Path(folder),
            )

        self.assertFalse(missing.compile_ready)
        self.assertIn("documents.not_supplied", {item.code for item in missing.diagnostics})
        self.assertTrue(ready.compile_ready)
        self.assertEqual(ready.compiler_capability, PROJECT_COMPILER_CAPABILITY_ID)


if __name__ == "__main__":
    unittest.main()
