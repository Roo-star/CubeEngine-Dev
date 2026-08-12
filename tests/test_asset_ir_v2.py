import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from PIL import Image

from srtp.asset_ir_v2 import (
    ASSET_COMPILER_CAPABILITIES,
    ASSET_COMPILER_CAPABILITY_ID,
    ASSET_IR_PATCH_SCHEMA_PATH,
    ASSET_IR_SCHEMA_PATH,
    ASSET_IR_VERSION,
    AssetCompileError,
    AssetIRPatchError,
    apply_asset_ir_patch,
    assess_asset_ir_conformance,
    canonical_asset_ir_hash,
    compile_asset_ir,
    is_asset_ir_compile_ready,
    new_asset_ir,
    seal_asset_ir,
    validate_asset_ir,
)
from srtp.scene_ir_v2 import (
    SceneCompileError,
    compile_scene_ir,
    identity_transform,
    new_scene_ir,
    seal_scene_ir,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def _license():
    return {
        "spdx_id": "MIT",
        "attribution": "Asset fixture authors",
        "source_uri": "https://example.invalid/asset-fixture",
        "redistribution": "allowed",
    }


def asset_fixture(root):
    image_path = root / "Images" / "atlas.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    for x in range(8):
        for y in range(8):
            image.putpixel((x, y), (220, 40, 40, 255))
    for x in range(8, 16):
        for y in range(8):
            image.putpixel((x, y), (40, 80, 220, 255))
    image.save(str(image_path), format="PNG")
    payload = image_path.read_bytes()

    document = new_asset_ir("asset:game.fixture", "Asset Fixture")
    document["assets"] = [{
        "id": "asset:image.tile_atlas",
        "name": "Tile Atlas",
        "kind": "image",
        "media_type": "image/png",
        "source": {
            "uri": "project://Images/atlas.png",
            "content_hash": _sha256(payload),
            "byte_size": len(payload),
        },
        "license": _license(),
        "importer": {
            "capability": "cubeengine.image", "version": "1.0",
            "settings": {"color_space": "srgb", "alpha": "straight"},
        },
        "metadata": {"source_role": "tile atlas"},
    }]
    document["derivations"] = [
        {
            "id": "asset:image.tile_covered",
            "name": "Covered Tile",
            "kind": "image", "media_type": "image/png",
            "strategy": "atlas_region", "inputs": ["asset:image.tile_atlas"],
            "settings": {"x": 0, "y": 0, "width": 8, "height": 8},
            "expected_content_hash": "", "license_policy": "inherit",
        },
        {
            "id": "asset:material.tile_covered_cube",
            "name": "Covered Tile Cube Surface",
            "kind": "material",
            "media_type": "application/vnd.cubeengine.presentation+json",
            "strategy": "cube_face_projection", "inputs": ["asset:image.tile_covered"],
            "settings": {"faces": "all", "uv_policy": "stretch"},
            "expected_content_hash": "", "license_policy": "inherit",
        },
    ]
    document["roles"] = [{
        "id": "asset:role.tile_covered", "name": "Covered Tile",
        "semantic": "tile.covered", "resource": "asset:image.tile_covered",
        "usage": "world_texture", "required": True,
    }]
    document["presentation_mappings"] = [{
        "id": "asset:mapping.tile_covered_3d", "name": "Covered Tile 3D",
        "source_role": "asset:role.tile_covered",
        "target_resource": "asset:material.tile_covered_cube",
        "strategy": "cube_face_projection", "fidelity": "source_derived",
        "settings": {"meaning": "same source tile on every cube face"},
    }]
    document["unresolved"] = []
    return seal_asset_ir(document), image_path


def scene_fixture(asset_document):
    document = new_scene_ir("scene:game.asset_fixture", "Asset Scene")
    document["dependencies"]["asset_ir"] = {
        "document_id": asset_document["document_id"],
        "content_hash": asset_document["content_hash"],
    }
    document["nodes"] = [{
        "id": "scene:node.tile", "name": "Tile", "parent": None,
        "active": True, "layer": "scene:layer.runtime",
        "transform": identity_transform(),
        "components": [{
            "id": "renderer", "type": "renderer", "enabled": True,
            "properties": {
                "geometry": "builtin:cube", "visible": True,
                "material": "asset:material.tile_covered_cube", "opacity": 1.0,
            },
        }],
    }]
    document["unresolved"] = []
    return seal_scene_ir(document)


class AssetIRContractTests(unittest.TestCase):
    def test_schema_capabilities_and_honest_draft(self):
        schema = json.loads(ASSET_IR_SCHEMA_PATH.read_text(encoding="utf-8"))
        patch_schema = json.loads(ASSET_IR_PATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
        draft = new_asset_ir("asset:game.empty", "Empty")

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["ir_version"]["const"], ASSET_IR_VERSION)
        self.assertEqual(ASSET_COMPILER_CAPABILITIES["security"]["executes_source_files"], False)
        self.assertIn("cube_face_projection", ASSET_COMPILER_CAPABILITIES["derivation_strategies"])
        self.assertEqual(set(patch_schema["properties"]["operations"]["items"]["properties"]["op"]["enum"]), {
            "add", "remove", "replace", "move", "copy", "test",
        })
        self.assertFalse(is_asset_ir_compile_ready(draft))
        self.assertFalse(validate_asset_ir(draft))

    def test_sealing_is_stable_and_semantic_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            document, _ = asset_fixture(Path(directory))
            changed = deepcopy(document)
            changed["roles"][0]["semantic"] = "tile.hidden"

            self.assertEqual(document["content_hash"], canonical_asset_ir_hash(document))
            self.assertEqual(document, seal_asset_ir(document))
            self.assertNotEqual(document["content_hash"], canonical_asset_ir_hash(changed))

    def test_semantics_reject_traversal_case_collisions_and_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            document, _ = asset_fixture(Path(directory))
            unsafe = deepcopy(document)
            unsafe["assets"][0]["source"]["uri"] = "project://../outside.png"
            collision = deepcopy(document)
            duplicate = deepcopy(collision["assets"][0])
            duplicate["id"] = "asset:image.duplicate"
            duplicate["source"]["uri"] = "project://images/ATLAS.png"
            collision["assets"].append(duplicate)
            cycle = deepcopy(document)
            cycle["derivations"][0]["inputs"] = ["asset:material.tile_covered_cube"]

            unsafe_codes = {item.code for item in validate_asset_ir(unsafe)}
            collision_codes = {item.code for item in validate_asset_ir(collision)}
            cycle_codes = {item.code for item in validate_asset_ir(cycle)}

            self.assertIn("source.uri", unsafe_codes)
            self.assertIn("source.case_collision", collision_codes)
            self.assertIn("derivation.cycle", cycle_codes)


class AssetCompilerTests(unittest.TestCase):
    def test_checked_in_minesweeper_example_uses_real_source_bytes(self):
        document = json.loads((
            ROOT / "srtp" / "examples" / "asset_ir_v2" /
            "minesweeper_tiles.asset-ir.json"
        ).read_text(encoding="utf-8"))
        source_root = ROOT / "srtp" / "reference_games" / "pygame_minesweeper"

        report = assess_asset_ir_conformance(document, source_root)
        catalog = compile_asset_ir(document, source_root)

        self.assertTrue(report.compile_ready)
        self.assertTrue(report.distribution_ready)
        self.assertEqual(
            catalog.resource("asset:image.minesweeper_atlas_2000").content_hash,
            "fe658c317f759c45cfedd3802bac9b2cf47e139c0670fe67214bac512e99e648",
        )
        self.assertEqual(catalog.resource("asset:image.minesweeper_covered").metadata["width"], 16)

    def test_real_bytes_compile_crop_map_materialize_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, _ = asset_fixture(root)
            first = compile_asset_ir(document, root)
            second = compile_asset_ir(document, root)
            cache = root / "Cache"
            paths = first.materialize(cache)
            reused = first.materialize(cache)

            crop = first.resource("asset:image.tile_covered")
            descriptor = first.resource("asset:material.tile_covered_cube")
            self.assertEqual(first.bundle_hash, second.bundle_hash)
            self.assertEqual(first.compiler_capability, ASSET_COMPILER_CAPABILITY_ID)
            self.assertEqual((crop.metadata["width"], crop.metadata["height"]), (8, 8))
            self.assertEqual(descriptor.derived_from, ("asset:image.tile_covered",))
            self.assertEqual(first.role("tile.covered").resource_id, crop.id)
            self.assertEqual(first.mappings_by_id["asset:mapping.tile_covered_3d"].target_resource, descriptor.id)
            self.assertTrue(first.distribution_ready)
            self.assertEqual(paths, reused)
            self.assertTrue(all(path.is_file() for path in paths.values()))
            self.assertTrue(all(_sha256(path.read_bytes()) == first.resource(identifier).content_hash for identifier, path in paths.items()))
            with self.assertRaises(TypeError):
                first.resources_by_id["asset:image.extra"] = crop
            with self.assertRaises(TypeError):
                crop.metadata["width"] = 99

    def test_source_mutation_and_custom_renderer_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, image_path = asset_fixture(root)
            image_path.write_bytes(image_path.read_bytes() + b"changed")
            with self.assertRaisesRegex(AssetCompileError, "byte size changed"):
                compile_asset_ir(document, root)

            document, _ = asset_fixture(root)
            custom = deepcopy(document)
            custom["derivations"][1]["strategy"] = "custom_renderer"
            custom["derivations"][1]["extension"] = "extension:renderer.custom/1"
            custom["presentation_mappings"][0]["strategy"] = "custom_renderer"
            custom = seal_asset_ir(custom)
            with self.assertRaisesRegex(AssetCompileError, "Extension Adapter SDK"):
                compile_asset_ir(custom, root)

    def test_license_unknown_compiles_locally_but_blocks_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, _ = asset_fixture(root)
            document["assets"][0]["license"].update({
                "spdx_id": "NOASSERTION", "redistribution": "unknown",
            })
            document = seal_asset_ir(document)
            report = assess_asset_ir_conformance(document, root)

            self.assertTrue(report.compile_ready)
            self.assertTrue(report.sources_verified)
            self.assertFalse(report.distribution_ready)
            self.assertIn("license.unresolved", {item.code for item in report.diagnostics})

    def test_conformance_requires_project_root_and_verifies_real_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, _ = asset_fixture(root)

            blocked = assess_asset_ir_conformance(document)
            verified = assess_asset_ir_conformance(document, root)

            self.assertFalse(blocked.compile_ready)
            self.assertIn("compiler.blocked", {item.code for item in blocked.diagnostics})
            self.assertTrue(verified.compile_ready)
            self.assertTrue(verified.bundle_hash)


class AssetSceneIntegrationTests(unittest.TestCase):
    def test_scene_requires_matching_catalog_and_registers_transitive_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_document, _ = asset_fixture(root)
            catalog = compile_asset_ir(asset_document, root)
            scene_document = scene_fixture(asset_document)

            with self.assertRaisesRegex(SceneCompileError, "Asset IR dependency"):
                compile_scene_ir(scene_document)
            wrong = deepcopy(scene_document)
            wrong["dependencies"]["asset_ir"]["content_hash"] = "0" * 64
            wrong = seal_scene_ir(wrong)
            with self.assertRaisesRegex(SceneCompileError, "hash"):
                compile_scene_ir(wrong, asset_catalog=catalog)

            scene = compile_scene_ir(scene_document, asset_catalog=catalog)
            commands = [item.to_mapping() for item in scene.build_commands()]
            registered = [item["payload"]["id"] for item in commands if item["op"] == "register_asset"]
            self.assertEqual(set(registered), {
                "asset:image.tile_atlas", "asset:image.tile_covered",
                "asset:material.tile_covered_cube",
            })
            self.assertEqual(scene.asset_dependency["document_id"], asset_document["document_id"])


class AssetPatchTests(unittest.TestCase):
    def test_patch_is_atomic_revisioned_and_recompilable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, _ = asset_fixture(root)
            proposal = {
                "document_id": base["document_id"],
                "base_revision": base["revision"],
                "base_content_hash": base["content_hash"],
                "operations": [{
                    "op": "replace", "path": "/derivations/1/settings/uv_policy",
                    "value": "contain",
                }],
                "evidence": [{"author": "designer", "reason": "preserve full tile"}],
                "assumptions": [], "unresolved": [],
            }

            updated = apply_asset_ir_patch(base, proposal)
            catalog = compile_asset_ir(updated, root)

            self.assertEqual(base["derivations"][1]["settings"]["uv_policy"], "stretch")
            self.assertEqual(updated["revision"], 1)
            self.assertEqual(updated["content_hash"], canonical_asset_ir_hash(updated))
            self.assertEqual(
                catalog.resource("asset:material.tile_covered_cube").metadata["recipe"]["settings"]["uv_policy"],
                "contain",
            )

    def test_patch_rejects_stale_and_protected_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            base, _ = asset_fixture(Path(directory))
            common = {
                "document_id": base["document_id"],
                "base_revision": base["revision"],
                "base_content_hash": base["content_hash"],
                "evidence": [{"author": "designer"}], "assumptions": [], "unresolved": [],
            }
            stale = dict(common, base_revision=99, operations=[{
                "op": "replace", "path": "/metadata/title", "value": "X",
            }])
            protected = dict(common, operations=[{
                "op": "replace", "path": "/document_id", "value": "asset:game.other",
            }])
            with self.assertRaisesRegex(AssetIRPatchError, "stale"):
                apply_asset_ir_patch(base, stale)
            with self.assertRaisesRegex(AssetIRPatchError, "protected"):
                apply_asset_ir_patch(base, protected)


if __name__ == "__main__":
    unittest.main()
