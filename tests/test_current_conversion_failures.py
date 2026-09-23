"""Regressions from the user's actual three-game run; no network."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx

from srtp.llm_compiler_v1.client import OpenRouterLLMClient, LLMClientError
from srtp.llm_compiler_v1.program_builder import lower, expression
from srtp.llm_compiler_v1.staged import run_behavior_tests
from srtp.llm_compiler_v1.behavior_runtime import test_runtime
from srtp.ir_v2 import seal_rule_ir, compile_rule_ir, replay_rule_ir
from srtp.session_random import session_sources

FIXTURE=Path(__file__).parent/'fixtures/field_runs_20260923'


def actual_2048():
    root=FIXTURE/'game.2048.pygame'
    report=json.loads((root/'report.json').read_text(encoding='utf-8'))
    payload=report['compilation_trace']['stages'][-1]['rejected_definition']
    rule=seal_rule_ir(dict(json.loads((root/'ir/game.rule-ir.json').read_text(encoding='utf-8')),**lower(payload['definition'])))
    return rule,payload['behavior_tests']


class ActualFailureTests(unittest.TestCase):
    def test_actual_2048_session_seed_compiles_without_changing_definition(self):
        rule,cases=actual_2048(); before=json.dumps(rule,sort_keys=True)
        runtime=compile_rule_ir(rule,random_sources=session_sources(rule))
        try:
            legal=next(a for a in runtime.all_actions() if runtime.is_legal(a))
            runtime.apply_action(legal)
            replay=replay_rule_ir(rule,runtime.export_replay_trace(),random_sources=runtime.initial_random_sources)
            try: self.assertEqual(replay.state.state_hash(),runtime.state.state_hash())
            finally: replay.close()
            self.assertEqual(json.dumps(rule,sort_keys=True),before)
        finally: runtime.close()

    def test_actual_2048_wrong_merge_is_caught_instead_of_silently_ignoring_fixture(self):
        rule,cases=actual_2048()
        with self.assertRaisesRegex(ValueError,'was 8, expected 4'):
            run_behavior_tests(rule,cases)
        runtime=test_runtime(rule,cases[0])
        try: self.assertEqual(runtime.state.grids['rule:state.board'][0,0],2)
        finally: runtime.close()

    def test_initialization_and_fixture_use_shared_seed_policy(self):
        rule,cases=actual_2048()
        self.assertEqual(session_sources(rule),session_sources(rule))
        self.assertNotEqual(session_sources(rule,1),session_sources(rule,2))
        with self.assertRaisesRegex(ValueError,'Unsupported behavior test fields'):
            test_runtime(rule,{'ignored_setup':{}})

    def test_python_count_and_coalesce_have_explicit_typed_lowering(self):
        self.assertEqual(expression('(1 == 1) + (2 == 3)')['args'][0]['op'],'if')
        self.assertEqual(expression('coalesce(None, 2)')['op'],'coalesce')

    def test_actual_tictactoe_click_changes_state_and_keeps_success_message(self):
        from srtp.project_viewer import ProjectHost
        host=ProjectHost(FIXTURE/'tictactoe_target/project.manifest.json')
        try:
            result=host.mouse_click('mouse.button.primary',{'coordinate':[0,0,0]})
            self.assertTrue(result.accepted)
            self.assertEqual(result.code,'transition_committed')
            self.assertEqual(host.controller.snapshot().grid[0][0][0],1)
            self.assertTrue(host.controller.verify_replay()['passed'])
        finally:host.close()

    def test_responses_final_message_is_not_concatenated_with_commentary(self):
        messages=[{'type':'message','channel':'commentary','content':[{'type':'output_text','text':'{"source_requests":[]}'}]},
                  {'type':'message','channel':'final','content':[{'type':'output_text','text':'{"definition":{}}'}]}]
        with patch('srtp.llm_compiler_v1.client.load_compiler_env'), patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline-test-key'}):
            with OpenRouterLLMClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'status':'completed','output':messages}))) as client:
                self.assertEqual(client.chat_json([]).parsed,{'definition':{}})
                for m in messages:m.pop('channel')
                with self.assertRaises(LLMClientError) as failure:client.chat_json([])
                self.assertIn('response_evidence',failure.exception.__dict__)

    def test_adjacent_read_requests_are_batched_without_discarding_definitions(self):
        from srtp.llm_compiler_v1.client import _compiler_json
        value=_compiler_json('{"source_requests":[{"path":"a.py"}]} {"source_requests":[{"path":"b.py"}]}')
        self.assertEqual([r['path'] for r in value['source_requests']],['a.py','b.py'])
        with self.assertRaises(ValueError):_compiler_json('{"definition":{}} {"definition":{"actions":[]}}')

    def test_source_inspection_retains_dependencies_and_accepts_empty_final_requests(self):
        from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
        root=Path(__file__).resolve().parents[1]/'srtp/reference_games/pygame_2048'
        workspace=SourceWorkspace(root,root/'main.py');workspace.read({'path':'logic.py'})
        self.assertIn('logic.py',[s['path'] for s in workspace.initial_context()['source']])
        with OpenRouterLLMClient(chat_fn=lambda **kw:'{"source_requests":[],"definition":{}}') as client:
            result=workspace.chat(client,[{'role':'system','content':'Compile'},{'role':'user','content':'{}'}])
        self.assertIn('definition',result.parsed)


if __name__=='__main__':unittest.main()
