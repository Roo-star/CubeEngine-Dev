"""User failures: SysFont evidence and serializable runtime repair feedback."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageFont
from srtp.asset_ir_v2 import new_asset_ir,seal_asset_ir,compile_asset_ir
from srtp.bundle_assets import copy_bundle_assets,asset_root
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.llm_compiler_v1.staged import diagnostic_messages
from srtp.ir_v2 import InvariantDiagnostic,InvariantViolation
from srtp.source_importer import SourceGameImporter
from srtp.system_fonts import font_request,system_font_path

ROOT=Path(__file__).resolve().parents[1]


class FontDiagnosticFailureTests(unittest.TestCase):
    def workspace(self):
        root=ROOT/'srtp/reference_games/pygame_2048'
        return SourceWorkspace(root,root/'main.py')

    def test_real_2048_configuration_indexes_exact_bold_verdana(self):
        workspace=self.workspace()
        self.assertEqual(workspace.preflight_errors,[])
        font=next(a for a in workspace.runtime_assets if a['metadata'].get('requested_family')=='Verdana')
        self.assertTrue(font['metadata']['bold'])
        self.assertEqual(font['source']['uri'],'runtime://pygame/sysfont/verdana/1/0')
        path=system_font_path(font['source']['uri'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),font['source']['content_hash'])
        self.assertIn('bold',ImageFont.truetype(str(path),20).getname()[1].lower())
        self.assertTrue(any(r['configuration'] for r in font['metadata']['source_references']))

    def test_system_font_survives_bundle_move_without_installed_font(self):
        workspace=self.workspace();doc=new_asset_ir('asset:test.system_font')
        doc.update(assets=workspace.runtime_assets,unresolved=[]);doc=seal_asset_ir(doc)
        with tempfile.TemporaryDirectory() as tmp:
            bundle=Path(tmp)/'bundle';bundle.mkdir()
            copy_bundle_assets(doc,workspace.root,bundle)
            moved=Path(tmp)/'moved';bundle.rename(moved)
            with patch('srtp.system_fonts.system_font_path',side_effect=AssertionError('Must read bundled bytes')):
                assets=compile_asset_ir(doc,asset_root(moved))
                resource=assets.resource(doc['assets'][0]['id'])
                self.assertGreater(ImageFont.truetype(io.BytesIO(resource._payload),20).getlength('2048'),0)

    def test_missing_system_font_stops_before_paid_call(self):
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_2048/main.py')
        calls=[]
        with patch('srtp.system_fonts.system_font_path',side_effect=ValueError('Required system font missing')):
            report=SourceToIRCompiler(chat_fn=lambda **kw:calls.append(kw)).compile(package)
        self.assertFalse(report.ok);self.assertEqual(calls,[])
        self.assertIn('preflight',report.diagnostics[0])

    def test_font_uri_cannot_select_arbitrary_paths(self):
        from srtp.runtime_assets import resource_relative_path
        for uri in ('runtime://pygame/sysfont/../../secret','runtime://pygame/sysfont/C%3A%2Fsecret/1/0',
                    'runtime://pygame/sysfont/verdana/2/0','file:///C:/secret'):
            with self.subTest(uri=uri),self.assertRaises(ValueError):resource_relative_path(uri)

    def test_invariant_objects_are_json_safe_and_keep_identity(self):
        error=InvariantViolation([InvariantDiagnostic('rule:invariant.mines','Exactly ten mines','error')])
        messages=diagnostic_messages(error)
        self.assertIn('rule:invariant.mines',messages[0])
        self.assertIn('Exactly ten mines',json.dumps({'repair_diagnostics':messages}))

    def test_actual_minesweeper_failure_is_saved_and_repaired_without_json_crash(self):
        payload=json.loads((ROOT/'tests/fixtures/field_runs_20260924/minesweeper_rejected_rule.json').read_text(encoding='utf-8'))
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_minesweeper/run_game.py')
        received=[]
        def chat(messages,**kw):
            received.append(json.loads(messages[-1]['content']))
            return json.dumps(payload)
        with tempfile.TemporaryDirectory() as tmp:
            report=SourceToIRCompiler(chat_fn=chat,max_repairs=1).compile(package,out_dir=Path(tmp)/'source')
            self.assertFalse(report.ok)
            self.assertTrue((Path(report.output_dir)/'report.json').is_file())
            self.assertIn('fixture violates',' '.join(report.diagnostics))
            self.assertIn("'1': 11",' '.join(report.diagnostics))
            self.assertTrue(received[-1]['repair_diagnostics'])
            self.assertNotIn('not JSON serializable',json.dumps(report.to_mapping()))

    def test_runtime_invariant_exception_reaches_next_model_request_and_report(self):
        payload=json.loads((ROOT/'tests/fixtures/field_runs_20260924/minesweeper_rejected_rule.json').read_text(encoding='utf-8'))
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_minesweeper/run_game.py')
        received=[]
        def chat(messages,**kw):
            received.append(json.loads(messages[-1]['content']));return json.dumps(payload)
        violation=InvariantViolation([InvariantDiagnostic('rule:invariant.mines','Exactly ten mines','error')])
        with tempfile.TemporaryDirectory() as tmp,patch('srtp.llm_compiler_v1.staged._execute_stage',side_effect=violation):
            report=SourceToIRCompiler(chat_fn=chat,max_repairs=1).compile(package,out_dir=Path(tmp)/'source')
            self.assertFalse(report.ok)
            self.assertEqual(len(received),2)
            self.assertIn('rule:invariant.mines',received[-1]['repair_diagnostics'][0])
            self.assertTrue((Path(report.output_dir)/'diagnostics.json').is_file())


if __name__=='__main__':unittest.main()
