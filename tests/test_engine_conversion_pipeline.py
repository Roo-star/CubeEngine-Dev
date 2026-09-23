"""Integration tests use explicit mock model definitions, never claim live LLM acceptance."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.program_builder import expression
from srtp.llm_compiler_v1.staged import run_behavior_tests
from srtp.source_importer import SourceGameImporter
from srtp.ir_v2.expression import ExpressionEvaluator, EvaluationContext
from srtp.ir_v2.sequence_functions import merge_equal
from srtp.ir_v2 import compile_rule_ir

ROOT = Path(__file__).resolve().parents[1]


def reference_definition(slot, variant='source'):
    family = slot.replace('_ir','')
    doc = json.loads((ROOT / ('artifacts/tictactoe_' + variant) / (family + '.' + family + '-ir.json')).read_text())
    if slot == 'scene_ir':
        renderer = doc['prefabs'][0]['root']['components'][0]['properties']
        renderer['variants'] = {'empty':{'color':[.2,.3,.4,1],'opacity':.3,'text':''},
                               'positive':{'color':[.1,.5,.9,1],'text':'X'},
                               'negative':{'color':[.9,.3,.1,1],'text':'O'}}
    return {k:v for k,v in doc.items() if k not in ('ir_version','document_id','revision','content_hash','metadata','provenance','dependencies')}


def tests_for_board(rank=2):
    coordinate = [0]*rank
    return [{'name':'placement and occupied rejection', 'steps':[
        {'action':'rule:action.place','parameters':{'target':coordinate},'accepted':True},
        {'action':'rule:action.place','parameters':{'target':coordinate},'accepted':False}],
        'expect':{'cells':[{'state':'rule:state.board_cell','coordinate':coordinate,'value':1}], 'terminal':False}}]


class ProgramBuilderTests(unittest.TestCase):
    def test_compact_expressions_execute_without_python_eval(self):
        evaluator = ExpressionEvaluator()
        compiled = expression('param.x > 1 and (param.x * 2 == 6)')
        self.assertTrue(evaluator.evaluate(compiled, EvaluationContext(parameters={'x':3})))
        self.assertFalse(evaluator.evaluate(compiled, EvaluationContext(parameters={'x':2})))
        for text in ("__import__('os').system('x')", "open('file')", '[x for x in []]'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                expression(text)

    def test_merge_is_single_pass_and_parameterized(self):
        self.assertEqual(merge_equal([2,2,4,4],0,2), ((4,8,0,0),12))
        self.assertEqual(merge_equal([2,2,2,2],0,2), ((4,4,0,0),8))
        self.assertEqual(merge_equal([0,2,0,2],0,3), ((6,0,0,0),6))

    def test_flood_and_line_values_execute_through_runtime(self):
        doc = json.loads((ROOT/'artifacts/tictactoe_target/rule.rule-ir.json').read_text())
        runtime = compile_rule_ir(doc)
        self.addCleanup(runtime.close)
        grid = runtime.state.grids['rule:state.board_cell']
        grid[0,0,0] = -1
        query = expression("grid.flood_region('rule:state.board_cell', [1,1,1], [0], [-1], True, True, [[2,2,2]])")
        values = runtime.evaluator.evaluate(query, runtime._context({}))
        self.assertEqual(len(values),25)
        self.assertNotIn((0,0,0),values)
        self.assertNotIn((2,2,2),values)
        self.assertEqual(grid[0,0,0],-1)
        from srtp.ir_v2.expression import ExpressionError
        with self.assertRaises(ExpressionError):
            runtime.evaluator.evaluate(expression("grid.values('rule:state.board_cell', [[-1,0,0]])"), runtime._context({}))

    def test_behavior_gate_catches_semantically_wrong_action(self):
        from srtp.ir_v2 import seal_rule_ir
        doc = json.loads((ROOT/'artifacts/tictactoe_source/rule.rule-ir.json').read_text())
        doc['actions'][0]['precondition'] = {'op':'literal','value':True}
        with self.assertRaisesRegex(ValueError,'expected accepted=False'):
            run_behavior_tests(seal_rule_ir(doc), tests_for_board())

    def test_behavior_gate_does_not_ignore_unknown_assertions(self):
        doc = json.loads((ROOT/'artifacts/tictactoe_source/rule.rule-ir.json').read_text())
        cases = tests_for_board()
        cases[0]['expect'] = {'invented_assertion': True}
        with self.assertRaisesRegex(ValueError, 'unsupported assertions'):
            run_behavior_tests(doc, cases)


class StagedCompilerTests(unittest.TestCase):
    def test_manifest_input_does_not_require_original_python(self):
        from srtp.llm_compiler_v1.evidence import build_evidence_pack
        package = SourceGameImporter().import_path(ROOT/'artifacts/tictactoe_source/project.manifest.json')
        self.assertEqual(package.runtime.kind, 'project_ir')
        self.assertEqual(package.transformation.source_dimensions, {'x':3,'y':3})
        self.assertEqual(package.transformation.target_dimensions, {'x':3,'y':3,'z':3})
        self.assertTrue(build_evidence_pack(package)['evidence'])

    def test_unseen_visual_variant_is_validated_before_launch(self):
        from srtp.project_viewer import ProjectHost
        host = ProjectHost(ROOT/'artifacts/tictactoe_target/project.manifest.json')
        self.addCleanup(host.close)
        graph = host.presentation
        node = next(n for n in graph.nodes.values() if any(c['type']=='renderer' for c in n['components'].values()))
        properties = next(c['properties'] for c in node['components'].values() if c['type']=='renderer')
        properties['variants'] = {'empty':{}, 'positive':{}, 'negative':{}, 'future':{'geometry':'builtin:unsupported'}}
        before = deepcopy(properties)
        self.assertIn('unsupported', ' '.join(graph.diagnostics()))
        self.assertEqual(properties, before, 'Validation must not change the current displayed state')

    def test_source_to_inspector_lift_uses_same_staged_pipeline(self):
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.project_viewer import ProjectHost
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        calls=[]
        def chat(messages, **kwargs):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']
            target=payload['design_intent'] is not None
            calls.append((target,slot))
            response={'definition':reference_definition(slot,'target' if target else 'source'),
                      'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                      'behavior_tests':tests_for_board(3 if target else 2) if slot=='rule_ir' else []}
            if target and slot=='rule_ir':
                self.assertIn({'kind':'set_extent','axis':'z','value':3},payload['design_intent']['changes'])
                response['plan']={key:{} for key in ('topology','source_xy_policy','target_z','neighborhood','movement','outcomes','presentation','input')}
                response['plan'].update(z_equals_one_tests=[],z_gt_one_tests=[],alternatives=[],unresolved=[])
            return json.dumps(response)
        with tempfile.TemporaryDirectory() as tmp:
            compiler=SourceToIRCompiler(chat_fn=chat,max_repairs=0)
            source=compiler.compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(source.ok,source.diagnostics)
            approve_llm_manifest_file(Path(source.output_dir)/'project.manifest.json')
            target=compiler.compile_spatial_lift(package,source_bundle_dir=Path(source.output_dir),
                target_dimensions={'x':3,'y':3,'z':3},out_dir=Path(tmp)/'target')
            self.assertTrue(target.ok,target.diagnostics)
            path=Path(target.output_dir)/'project.manifest.json'
            approve_llm_manifest_file(path)
            with self.subTest('generated bundle is consumable by the production host'):
                host=ProjectHost(path)
                try:
                    self.assertEqual(host.snapshot.dimensions,(3,3,3))
                    self.assertTrue(host.click((1,1,1)).accepted)
                    host.refresh_scene()
                    styles=[host.presentation.renderer(n,k) for n,node in host.presentation.nodes.items()
                            for k,c in node['components'].items() if c['type']=='renderer']
                    self.assertEqual(sum(s.get('text')=='X' for s in styles),1)
                finally:
                    host.close()
            self.assertEqual(len(calls),8,'Inspector dimensions must not incur an extra intent-generation call')

    def test_default_compiler_executes_all_stages_and_retains_source_trace(self):
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        calls = []
        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            slot = payload['stage']
            calls.append(slot)
            self.assertTrue(payload['source_workspace']['source'][0]['text'])
            definition = reference_definition(slot)
            if slot == 'rule_ir':
                definition['actions'][0]['precondition'] = {'expr':"grid.equals('rule:state.board_cell', param.target, 0)"}
            return json.dumps({'definition':definition, 'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests':tests_for_board() if slot == 'rule_ir' else []})
        with tempfile.TemporaryDirectory() as tmp:
            report = SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(report.ok,report.diagnostics)
            self.assertEqual(calls,['rule_ir','asset_ir','scene_ir','input_ir'])
            self.assertFalse(report.compile_ready, 'Generation does not forge designer approval')
            self.assertTrue(all(row['passed'] for row in report.compilation_trace['stages']))
            saved = json.loads((Path(report.output_dir)/'report.json').read_text())
            self.assertTrue(saved['compilation_trace']['source_inspection']['reads'])
            again=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(again.ok,again.diagnostics)
            self.assertEqual(len(calls),4,'A rerun should revalidate the accepted stage cache without new API calls')
            self.assertTrue(all(row['cached'] for row in again.compilation_trace['stages']))

    def test_failed_rule_is_repaired_without_regenerating_accepted_stages(self):
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        counts = {}
        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content']); slot=payload['stage']
            counts[slot] = counts.get(slot,0)+1
            definition = reference_definition(slot)
            if slot == 'rule_ir' and counts[slot] == 1:
                definition['actions'][0]['precondition'] = {'op':'literal','value':True}
            if slot == 'rule_ir' and counts[slot] == 2:
                self.assertIn('expected accepted=False',str(payload['repair_diagnostics']))
            return json.dumps({'definition':definition,'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        report=SourceToIRCompiler(chat_fn=chat,max_repairs=1).compile(package)
        self.assertTrue(report.ok,report.diagnostics)
        self.assertEqual(counts,{'rule_ir':2,'asset_ir':1,'scene_ir':1,'input_ir':1})


if __name__ == '__main__':
    unittest.main()
