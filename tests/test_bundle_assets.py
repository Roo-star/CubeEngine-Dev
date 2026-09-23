import tempfile
import unittest
from pathlib import Path
from srtp.bundle_assets import asset_root, copy_bundle_assets
from srtp.asset_ir_v2 import compile_asset_ir, AssetCompileError
from tests.test_asset_ir_v2 import asset_fixture


class BundleAssetsTests(unittest.TestCase):
    def test_published_project_opens_after_source_deleted_without_root_override(self):
        from tests.test_project_manifest_v2 import four_ir_fixture, manifest_fixture
        from srtp.asset_ir_v2 import seal_asset_ir
        from srtp.scene_ir_v2 import seal_scene_ir
        from srtp.llm_compiler_v1.compiler import CompileReport
        from srtp.llm_compiler_v1.artifacts import write_compile_artifacts
        from srtp.project_viewer import ProjectHost
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source'; source.mkdir()
            image_document,image_path=asset_fixture(source)
            manifest,rule,scene,asset,controls=four_ir_fixture()
            asset['assets'].extend(image_document['assets']); asset=seal_asset_ir(asset)
            scene['dependencies']['asset_ir']['content_hash']=asset['content_hash']
            renderer=scene['prefabs'][0]['root']['components'][0]['properties']
            renderer['texture']='asset:image.tile_atlas'
            renderer['variants']={'empty':{'opacity':.4},'occupied':{'opacity':1}}
            scene=seal_scene_ir(scene); manifest=manifest_fixture(rule,scene,asset,controls)
            report=CompileReport(True,'source_four_ir','test','project:game.fixture','test',compile_ready=True,
                documents={'rule_ir':rule,'scene_ir':scene,'asset_ir':asset,'input_ir':controls},manifest=manifest)
            result=write_compile_artifacts(root/'bundle',report,asset_project_root=source)
            image_path.unlink(); moved=root/'moved'; result.rename(moved)
            host=ProjectHost(moved/'project.manifest.json')
            try:
                self.assertFalse(host.presentation.diagnostics())
                self.assertTrue(host.mouse('mouse.button.primary',{'coordinate':(0,)*len(host.snapshot.dimensions)}).accepted)
            finally: host.close()
            resource=moved/'resources/Images/atlas.png'; resource.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'hash|size'):
                ProjectHost(moved/'project.manifest.json',source)

    def test_original_removed_bundle_relocated_and_resources_recompile(self):
        with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as tmp:
            root=Path(original); document,_=asset_fixture(root)
            bundle=Path(tmp)/'bundle'; bundle.mkdir()
            expected=compile_asset_ir(document,project_root=root)
            copy_bundle_assets(document,root,bundle)
            (root/'Images/atlas.png').unlink()
            moved=Path(tmp)/'moved'; bundle.rename(moved)
            actual=compile_asset_ir(document,project_root=asset_root(moved))
            self.assertEqual({k:r.content_hash for k,r in expected.resources_by_id.items()},
                             {k:r.content_hash for k,r in actual.resources_by_id.items()})
            second=Path(tmp)/'second'; second.mkdir()
            copy_bundle_assets(document,moved,second)
            compile_asset_ir(document,project_root=asset_root(second))
            (moved/'resources/Images/atlas.png').write_bytes(b'corrupted')
            with self.assertRaises(AssetCompileError):
                compile_asset_ir(document,project_root=asset_root(moved))

    def test_changed_original_and_path_escape_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); document,_=asset_fixture(root)
            (root/'Images/atlas.png').write_bytes(b'changed')
            destination=root/'out'; destination.mkdir()
            with self.assertRaisesRegex(ValueError,'changed before publication'):
                copy_bundle_assets(document,root,destination)
            document['assets'][0]['source']['uri']='project://../outside.png'
            another=root/'another'; another.mkdir()
            with self.assertRaises(ValueError):
                copy_bundle_assets(document,root,another)


if __name__=='__main__': unittest.main()
