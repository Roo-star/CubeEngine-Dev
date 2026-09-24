"""Saved paid output and neutral runtime patterns; no model/network calls."""
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from srtp.llm_compiler_v1.compiler import (
    CompileReport, SourceToIRCompiler, _target_documents, _pin_cross_ir_dependencies,
)
from srtp.llm_compiler_v1.validation import validate_working_documents, validate_and_apply_proposal
from srtp.llm_compiler_v1.program_builder import definition_proposal, expression
from srtp.llm_compiler_v1.staged import _execute_stage, run_stages, _legacy_target_checkpoint_matches, resume_design_intent
from srtp.llm_compiler_v1.client import LLMTransportError
from srtp.source_importer import SourceGameImporter
from srtp.llm_compiler_v1.behavior_runtime import test_runtime as scenario_runtime
from srtp.ir_v2 import compile_rule_ir, RuleRuntimeError, replay_rule_ir
from srtp.ir_v2.expression import ExpressionEvaluator, EvaluationContext
from tests.test_rule_ir_v2_event_time import _event_document, _system, _increment

ROOT=Path(__file__).resolve().parents[1]
FIELD=ROOT/'tests/fixtures/field_runs_20260924/minesweeper_lift.json'


class SpatialLiftBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.field=json.loads(FIELD.read_text(encoding='utf-8'))

    def test_target_bootstrap_preserves_approved_semantics_and_shared_pointer_controls(self):
        for renamed in (False,True):
            source=deepcopy(self.field['source_documents'])
            if renamed:
                # No game title/ID dispatch may affect transformation bootstrap.
                source=json.loads(json.dumps(source).replace('minesweeper','neutral.puzzle')
                                  .replace('face_restart','neutral.restart').replace('clear_question','neutral.alternative'))
                for slot in source: source[slot]['metadata']['title']='Unrelated geometry editor'
            before=deepcopy(source)
            target=_target_documents(source)
            self.assertEqual(source,before)
            self.assertEqual(validate_working_documents(target),[])
            for slot in source:
                semantic=lambda doc:{k:v for k,v in doc.items() if k not in {'document_id','revision','content_hash','dependencies'}}
                self.assertEqual(semantic(source[slot]),semantic(target[slot]))
                self.assertNotEqual(source[slot]['document_id'],target[slot]['document_id'])
            for binding in target['input_ir']['bindings']:
                trigger=binding['trigger']
                self.assertTrue(trigger['control'].startswith(trigger['device']+'.'))

    def test_bad_baseline_stops_before_intent_or_any_stage_model_request(self):
        source=deepcopy(self.field['source_documents'])
        source['input_ir']['bindings'][2]['trigger']['control']='keyboard.key.a'
        client=Mock()
        compiler=SourceToIRCompiler(client=client,max_repairs=0)
        report=CompileReport(ok=True,compile_ready=True,stage='source_four_ir',job_id='job:test',
            project_id='project:test',source_package_hash=self.field['evidence']['source_package_hash'],
            documents=source,manifest=self.field['source_manifest'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'main.py').write_text('pass')
            package=SimpleNamespace(root=str(root),entrypoint=str(root/'main.py'))
            stopped=compiler._compile_lift_stage(package=package,evidence=self.field['evidence'],
                source_report=report,intent_text='Make a cube',language='en',out_dir=None)
            self.assertFalse(stopped.ok)
            self.assertIn('before model call',stopped.diagnostics[0])
            stages=run_stages(compiler,package=package,evidence=self.field['evidence'],
                documents=source,job_id='job:test')
            self.assertEqual(stages.attempts,0)
            self.assertIn('before model call',stages.diagnostics[0])
        self.assertEqual(client.mock_calls,[])

    def test_original_paid_rule_response_passes_all_five_behavior_cases(self):
        docs=_target_documents(self.field['source_documents'])
        payload=self.field['payloads'][-1]
        proposal=definition_proposal(payload,slot='rule_ir',documents=docs,
            evidence_pack=self.field['evidence'],job_id='job:field-replay')
        applied=validate_and_apply_proposal(proposal,docs)
        self.assertTrue(applied.ok,applied.diagnostics)
        results=_execute_stage('rule_ir',applied.documents,ROOT,payload['behavior_tests'],spatial=True)
        self.assertEqual(len(results),5)
        self.assertTrue(all(r['passed'] for r in results))

    def test_checkpoint_recovery_requires_exact_source_intent_model_and_no_accepted_stages(self):
        docs=_target_documents(self.field['source_documents'])
        old=_pin_cross_ir_dependencies(deepcopy(docs))
        identity={'source':'package-hash','files':{'main.py':'file-hash'},'model_configuration':{'model':'test'},
            'intent':{'original_text':'Make a cube'},'source_manifest':'source-pin',
            'base':{k:d['content_hash'] for k,d in docs.items()}}
        old_identity=dict(identity,base={k:d['content_hash'] for k,d in old.items()})
        stored={'input_signature':hashlib.sha256(json.dumps(old_identity,sort_keys=True).encode()).hexdigest(),
            'stages':{},'rejected_stages':{'rule_ir':self.field['payloads'][-1]}}
        self.assertTrue(_legacy_target_checkpoint_matches(stored,identity,docs))
        for field in ('source','files','model_configuration','intent','source_manifest'):
            changed=dict(identity); changed[field]='different'
            self.assertFalse(_legacy_target_checkpoint_matches(stored,changed,docs),field)
        self.assertFalse(_legacy_target_checkpoint_matches(dict(stored,stages={'asset_ir':{}}),identity,docs))

    def test_unobserved_bulk_changes_do_not_exhaust_cascade_budget_and_replay_matches(self):
        doc=_event_document()
        doc['actions'][0]['effects']=[_increment('rule:state.delayed',1)]*2048+[_increment('rule:state.counter',1)]
        runtime=compile_rule_ir(doc); self.addCleanup(runtime.close)
        runtime.apply_action(next(a for a in runtime.all_actions() if a.action_id==doc['actions'][0]['id']))
        self.assertEqual(runtime.state.globals['rule:state.delayed'],2048)
        self.assertEqual(runtime.state.globals['rule:state.changes'],1)
        replayed=replay_rule_ir(doc,runtime.export_replay_trace()); self.addCleanup(replayed.close)
        self.assertEqual(runtime.state.state_hash(),replayed.state.state_hash())

    def test_original_checkpoint_resumes_intent_and_rule_before_any_new_model_request(self):
        field=self.field
        class OfflineClient:
            provider='openrouter'; model=field['model_configuration']['model']
            cache_identity=deepcopy(field['model_configuration'])
            calls=[]
            def chat_json(self,messages):
                task=json.loads(messages[1]['content'])['task']
                self.calls.append(task)
                raise LLMTransportError('offline stop at uncached '+task)
        client=OfflineClient()
        compiler=SourceToIRCompiler(client=client,max_repairs=0)
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_minesweeper/run_game.py')
        docs=_target_documents(field['source_documents']); intent=field['design_intent']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); failed=root/'target.failed'/'saved'; failed.mkdir(parents=True)
            (failed/'design_intent.json').write_text(json.dumps(intent),encoding='utf-8')
            compiler.checkpoint_path=root/'target.stages.json'
            compiler.checkpoint_path.write_text(json.dumps({'input_signature':field['legacy_input_signature'],
                'stages':{},'rejected_stages':{'rule_ir':field['payloads'][-1]},
                'provider':client.provider,'model':client.model}),encoding='utf-8')
            def resume(text=None):
                return resume_design_intent(compiler,package,field['evidence'],docs,
                    field['source_manifest']['content_hash'],text or intent['original_text'],intent['language'],root/'target')
            self.assertEqual(resume(),intent)
            self.assertIsNone(resume('Different requested geometry'))
            client.cache_identity['model']='different'
            self.assertIsNone(resume())
            client.cache_identity=deepcopy(field['model_configuration'])
            outcome=run_stages(compiler,package=package,evidence=field['evidence'],documents=docs,
                job_id='job:resume',design_intent=resume(),source_manifest_hash=field['source_manifest']['content_hash'])
            self.assertEqual(client.calls,['build_asset_ir'])
            self.assertTrue(outcome.trace[0]['passed'])
            self.assertTrue(outcome.trace[0]['cached'])
            self.assertEqual(len(outcome.trace[0]['checks']),5)
            self.assertIn('cache_recovery',outcome.trace[0])
            # The accepted Rule and intent are now pinned to the corrected
            # baseline, so subsequent resume needs no old failure directory.
            (failed/'design_intent.json').unlink()
            self.assertEqual(resume(),intent)

    def test_real_state_cascade_still_fails_and_rolls_back_for_specific_and_wildcard_listeners(self):
        for trigger in ({'kind':'state_changed','state':'rule:state.counter'},{'kind':'state_changed'}):
            doc=_event_document(); doc['systems']=[_system('rule:system.loop',trigger,[_increment('rule:state.counter',1)])]
            doc['actions'][0]['effects']=[_increment('rule:state.counter',1)]
            runtime=compile_rule_ir(doc); self.addCleanup(runtime.close)
            before=runtime.state.state_hash()
            with self.assertRaisesRegex(RuleRuntimeError,'cascade exceeded'):
                runtime.apply_action(next(iter(runtime.all_actions())))
            self.assertEqual(runtime.state.state_hash(),before)
            self.assertEqual(runtime.export_replay_trace(),())

    def test_typed_per_state_fixture_fill_is_explicit_and_precedes_overrides(self):
        rule=json.loads((ROOT/'artifacts/tictactoe_target/rule.rule-ir.json').read_text())
        cells=[{'state':'rule:state.board_cell','coordinate':[0,0,0],'value':1},
               {'state':'rule:state.board_cell','otherwise':0}]
        runtime=scenario_runtime(rule,{'fixture':{'cells':cells}}); self.addCleanup(runtime.close)
        self.assertEqual(int(runtime.state.grids['rule:state.board_cell'].sum()),1)
        for fixture in ({'cells':cells,'otherwise':0},{'cells':cells+[cells[-1]]},
                        {'cells':[dict(cells[-1],otherwise='bad')]},
                        {'cells':[{'state':'rule:state.board_cell','otherwise':0,'value':2}]}):
            with self.subTest(fixture=fixture),self.assertRaises((ValueError,RuntimeError)):
                scenario_runtime(rule,{'fixture':fixture})

    def test_compact_comparison_chains_and_membership_are_pure_and_short_circuit(self):
        evaluator=ExpressionEvaluator()
        for x in (-1,0,2,3):
            result=evaluator.evaluate(expression('0 <= param.x < 3'),EvaluationContext(parameters={'x':x}))
            self.assertEqual(result,0 <= x < 3)
        for text,value in (('2 in [1,2,3]',True),('4 not in [1,2,3]',True),
                           ('4 in [1,2,3]',False),('1 > 2 > (1 // 0)',False)):
            self.assertEqual(evaluator.evaluate(expression(text)),value)
        with self.assertRaises(ValueError): expression('1 is 1')


if __name__=='__main__': unittest.main()
