"""No network: resource discovery, citations, replay gates and cost protection."""
import hashlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from srtp.asset_ir_v2 import new_asset_ir, seal_asset_ir, compile_asset_ir, is_asset_ir_compile_ready, AssetCompileError
from srtp.bundle_assets import copy_bundle_assets, asset_root
from srtp.llm_compiler_v1.client import LLMTransportError
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler, bootstrap_documents
from srtp.llm_compiler_v1.evidence import build_evidence_pack
from srtp.llm_compiler_v1.program_builder import definition_proposal
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.llm_compiler_v1.validation import validate_and_apply_proposal
from srtp.runtime_assets import discover_runtime_assets, resolve_resource, PYGAME_FONT_URI
from srtp.source_importer import SourceGameImporter
from tests.test_engine_conversion_pipeline import ROOT, reference_definition, tests_for_board


class AssetPreflightTests(unittest.TestCase):
    def package(self):
        return SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')

    def test_resolved_empty_catalog_is_valid_but_required_gaps_still_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = new_asset_ir('asset:test.empty')
            self.assertFalse(is_asset_ir_compile_ready(doc))
            with self.assertRaisesRegex(AssetCompileError, '/assets: No source asset inventory'):
                compile_asset_ir(seal_asset_ir(doc),Path(tmp))
            doc['unresolved'] = []
            self.assertTrue(is_asset_ir_compile_ready(doc))
            self.assertFalse(compile_asset_ir(seal_asset_ir(doc),Path(tmp)).resources_by_id)
            doc['unresolved']=[{'path':'/assets/font','reason':'Exact font missing','owner':'importer','required':True}]
            with self.assertRaisesRegex(AssetCompileError,'/assets/font: Exact font missing'):
                compile_asset_ir(seal_asset_ir(doc),Path(tmp))

    def test_runtime_font_is_discovered_without_running_source_and_is_portable(self):
        from PIL import ImageFont
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); game=root/'game'; game.mkdir()
            source=game/'main.py'
            source.write_text("from pygame.font import Font as F\nraise RuntimeError('never execute source')\nF(None, 36)\n",encoding='utf-8')
            assets=discover_runtime_assets({'main.py':source})
            self.assertEqual(len(assets),1)
            self.assertEqual(assets[0]['source']['uri'],PYGAME_FONT_URI)
            doc=new_asset_ir('asset:test.font'); doc.update(assets=assets,unresolved=[])
            doc=seal_asset_ir(doc)
            compiled=compile_asset_ir(doc,game)
            resource=compiled.resource('asset:font.pygame_default')
            font=ImageFont.truetype(io.BytesIO(resource._payload),36)
            self.assertGreater(font.getlength('Tic Tac Toe'),0)
            bundle=root/'bundle'; bundle.mkdir(); copy_bundle_assets(doc,game,bundle)
            source.unlink(); moved=root/'moved'; bundle.rename(moved)
            with patch('srtp.runtime_assets.pygame_default_font',side_effect=AssertionError('Portable bundle must not use installed font')):
                copied=compile_asset_ir(doc,asset_root(moved))
                self.assertEqual(copied.resource(resource.id).content_hash,resource.content_hash)
                (moved/'resources/_runtime/pygame/default-font.ttf').unlink()
                with self.assertRaisesRegex(AssetCompileError,'does not exist'):
                    compile_asset_ir(doc,asset_root(moved))

    def test_runtime_resource_allowlist_and_path_security(self):
        with tempfile.TemporaryDirectory() as tmp:
            for uri in ('runtime://pygame/../../secret','runtime://other/font','project://../secret','file:///secret',{},[],None):
                with self.subTest(uri=uri),self.assertRaises(ValueError): resolve_resource(Path(tmp),uri)

    def test_missing_installed_font_blocks_before_any_model_request(self):
        package=self.package(); calls=[]
        with patch('srtp.runtime_assets.pygame_default_font',side_effect=ValueError('Required pygame default font missing')):
            report=SourceToIRCompiler(chat_fn=lambda **kw:calls.append(kw),max_repairs=2).compile(package)
        self.assertFalse(report.ok); self.assertEqual(calls,[]); self.assertEqual(report.attempts,0)
        self.assertIn('preflight',report.diagnostics[0])

    def test_compact_citations_are_verified_not_blindly_accepted(self):
        package=self.package(); evidence=build_evidence_pack(package)
        docs=bootstrap_documents(title=package.title,source_package_hash=evidence['source_package_hash']).documents
        workspace=SourceWorkspace(Path(package.root),Path(package.entrypoint))
        read=workspace.read({'path':'main.py','start_line':48,'end_line':49})
        for citation in (read['citation'],read['citation']['evidence_id']):
            payload={'definition':{'assets':[],'unresolved':[]},'evidence':[citation]}
            proposal=definition_proposal(payload,slot='asset_ir',documents=docs,evidence_pack=evidence,job_id='job:test')
            result=validate_and_apply_proposal(proposal,docs,evidence_pack=evidence,source_root=Path(package.root))
            self.assertTrue(result.ok,result.diagnostics)
        for wrong in ('main.py:48-49@'+'0'*64,'main.py:99999-99999@'+read['file_sha256'],
                      '../outside.py:1-2@'+read['file_sha256']):
            payload['evidence']=[wrong]
            proposal=definition_proposal(payload,slot='asset_ir',documents=docs,evidence_pack=evidence,job_id='job:test')
            self.assertFalse(validate_and_apply_proposal(proposal,docs,evidence_pack=evidence,source_root=Path(package.root)).ok)

    def test_repeated_asset_blocker_stops_and_reports_actual_candidate_gap(self):
        calls=[]
        def chat(messages,**kw):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']; calls.append(slot)
            definition=reference_definition(slot)
            if slot=='asset_ir':
                definition.update(assets=[],derivations=[],roles=[],presentation_mappings=[],unresolved=[
                    {'path':'/assets/font','reason':'Exact font missing','owner':'importer','required':True}])
            return json.dumps({'definition':definition,'evidence':[payload['source_workspace']['source'][0]['citation']],
                'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        report=SourceToIRCompiler(chat_fn=chat,max_repairs=5).compile(self.package())
        self.assertEqual(calls,['rule_ir','asset_ir','asset_ir'])
        self.assertEqual([item['path'] for item in report.unresolved_summary],['/assets/font'])
        self.assertIn('stopped_reason',report.compilation_trace['stages'][-1])

    def test_engine_upgrade_revalidates_paid_stages_without_regeneration(self):
        calls=[]
        def chat(messages,**kw):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']; calls.append(slot)
            return json.dumps({'definition':reference_definition(slot),
                'evidence':[payload['source_workspace']['source'][0]['citation']],
                'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'source'; compiler=SourceToIRCompiler(chat_fn=chat,max_repairs=0)
            report=compiler.compile(self.package(),out_dir=destination); self.assertTrue(report.ok,report.diagnostics)
            checkpoint=destination.with_name('source.stages.json'); saved=json.loads(checkpoint.read_text())
            saved['signature']='different-engine-build'; checkpoint.write_text(json.dumps(saved),encoding='utf-8')
            again=compiler.compile(self.package(),out_dir=destination)
            self.assertTrue(again.ok,again.diagnostics); self.assertEqual(len(calls),4)
            self.assertTrue(all(t['cached'] for t in again.compilation_trace['stages']))
            saved=json.loads(checkpoint.read_text()); saved['signature']='different-engine-build'
            saved['stages']['asset_ir']['definition']['unresolved']=[
                {'path':'/assets','reason':'Invalidated resource','required':True,'owner':'importer'}]
            checkpoint.write_text(json.dumps(saved),encoding='utf-8')
            changed=compiler.compile(self.package(),out_dir=destination)
            self.assertTrue(changed.ok,changed.diagnostics)
            self.assertEqual(calls[4:],['asset_ir'])
            self.assertFalse(changed.compilation_trace['stages'][1]['passed'])

    def test_runtime_font_resource_enters_full_source_bundle(self):
        def chat(messages,**kw):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']
            definition=reference_definition(slot)
            if slot=='asset_ir':
                definition['assets']=deepcopy(payload['source_workspace']['runtime_assets'])
                self.assertTrue(definition['assets'])
            if slot=='scene_ir':
                definition['prefabs'][0]['root']['components'][0]['properties']['font']='asset:font.pygame_default'
            return json.dumps({'definition':definition,'evidence':[payload['source_workspace']['source'][0]['citation']],
                'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        with tempfile.TemporaryDirectory() as tmp:
            report=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(self.package(),out_dir=Path(tmp)/'source')
            self.assertTrue(report.ok,report.diagnostics)
            self.assertTrue((Path(report.output_dir)/'resources/_runtime/pygame/default-font.ttf').is_file())
            from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
            from srtp.project_viewer import ProjectHost
            manifest=Path(report.output_dir)/'project.manifest.json'
            approve_llm_manifest_file(manifest)
            with patch('srtp.runtime_assets.pygame_default_font',side_effect=AssertionError('Read bundled font')):
                host=ProjectHost(manifest)
                try:
                    self.assertFalse(host.presentation.diagnostics())
                    self.assertTrue(host.mouse('mouse.button.primary',{'coordinate':(0,0)}).accepted)
                finally:
                    host.close()


if __name__=='__main__': unittest.main()
