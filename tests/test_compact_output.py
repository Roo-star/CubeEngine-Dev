"""Compact model output: expr shorthand, cut-off salvage/continuation and parted definitions."""
import json
import os
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler, _salvage_operations
from srtp.llm_compiler_v1.client import LLMClientError, OpenRouterLLMClient
from tests.test_llm_agentic_compiler import (FILE, TICTACTOE, _PASS, _asset_patch, _input_patch, _rule_patch,
                                             _scene_patch, _spec)
from tests.test_openrouter_transport import completed


def cut(value, keep_operations):
    """A reply cut off inside operation number keep_operations (0-based)."""
    ops = value['operations']
    prefix = json.dumps({'operations': ops[:keep_operations + 1]})
    marker = json.dumps(ops[keep_operations])
    return prefix[:prefix.rindex(marker) + len(marker) // 2]


class Chat:
    """Scripted replies; a ('cut', text) item simulates finish_reason=length."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, **kwargs):
        self.requests.append(deepcopy(kwargs['messages']))
        reply = self.replies.pop(0)
        if isinstance(reply, tuple):
            return SimpleNamespace(content=reply[1], choices=[SimpleNamespace(finish_reason='length')])
        return SimpleNamespace(content=json.dumps(reply), provider='mock', model='mock-model')


class TruncationTests(unittest.TestCase):
    def test_client_keeps_partial_text_of_cut_off_replies(self):
        environment = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'offline-test-key',
                                              'CUBEENGINE_LLM_OPENROUTER_MODEL': 'openai/gpt-6-sol'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        loader = patch('srtp.llm_compiler_v1.client.load_compiler_env')
        loader.start()
        self.addCleanup(loader.stop)
        body = completed({}, status='incomplete', incomplete_details={'reason': 'max_output_tokens'},
                         output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"operations":[{"op":'}]}])
        with OpenRouterLLMClient(transport=httpx.MockTransport(lambda r: body)) as client:
            with self.assertRaises(LLMClientError) as raised:
                client.chat_json([])
        self.assertTrue(raised.exception.truncated)
        self.assertEqual(raised.exception.content, '{"operations":[{"op":')
        mock = OpenRouterLLMClient(chat_fn=lambda **k: SimpleNamespace(content='{"a":', choices=[SimpleNamespace(finish_reason='length')]))
        with mock, self.assertRaises(LLMClientError) as raised:
            mock.chat_json([])
        self.assertEqual((raised.exception.truncated, raised.exception.content), (True, '{"a":'))

    def test_salvage_keeps_only_complete_operations(self):
        patch_ = _rule_patch()
        salvaged = _salvage_operations(cut(patch_, 2))
        self.assertEqual(salvaged, patch_['operations'][:2])
        self.assertEqual(_salvage_operations('{"evidence":[]}'), [])


class AgenticContinuationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = self.tmp / 'source'
        self.source.mkdir()
        shutil.copy(TICTACTOE, self.source / FILE)

    def tearDown(self):
        self._tmp.cleanup()

    def run_with(self, rule_replies):
        chat = Chat([{'action': 'finish', 'spec': _spec()}] + rule_replies
                    + [_asset_patch(), _scene_patch(), _input_patch(), _PASS])
        compiler = AgenticSourceToIRCompiler(chat_fn=chat)
        report = compiler.compile_path(self.source, out_dir=self.tmp / 'out', title='Tic Tac Toe 2D')
        return report, chat, compiler.last_job

    def test_cut_off_rule_patch_is_continued_not_regenerated(self):
        full = _rule_patch()
        rest = dict(full, operations=full['operations'][2:])
        report, chat, job = self.run_with([('cut', cut(full, 2)), rest])
        self.assertTrue(report.ok, report.diagnostics)
        request = json.loads(chat.requests[2][-1]['content'])
        self.assertEqual((request['task'], request['received_operations']), ('rule_ir_continue', 2))
        self.assertIn('cut off', request['instruction'])
        self.assertTrue(any(step['kind'] == 'continuation' for step in job.trace.steps))
        self.assertEqual(len(job.workers['rule_ir'].envelope['operations']), len(full['operations']))

    def test_model_may_send_a_long_patch_in_parts(self):
        full = _rule_patch()
        first = {'operations': full['operations'][:3], 'continue': True}
        rest = dict(full, operations=full['operations'][3:])
        report, chat, job = self.run_with([first, rest])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(json.loads(chat.requests[2][-1]['content'])['received_operations'], 3)

    def test_expr_shorthand_is_lowered_and_bad_expr_names_its_pointer(self):
        full = _rule_patch()
        compact = deepcopy(full)
        actions = next(op for op in compact['operations'] if op['path'] == '/actions')
        actions['value'][0]['precondition'] = {'expr': "grid.equals('rule:state.board_cell', param.target, 0)"}
        broken = deepcopy(compact)
        next(op for op in broken['operations'] if op['path'] == '/actions')['value'][0]['precondition'] = {'expr': 'open(1)'}
        index = next(i for i, op in enumerate(compact['operations']) if op['path'] == '/actions')
        report, chat, job = self.run_with([broken, compact])
        self.assertTrue(report.ok, report.diagnostics)
        repair = json.loads(chat.requests[2][-1]['content'])
        self.assertIn('/operations/{0}/value/0/precondition'.format(index), repair['diagnostics'][0])
        stored = report.documents['rule_ir']['actions'][0]['precondition']
        self.assertEqual(stored['function'], 'core:grid.equals')


class StagedPartsTests(unittest.TestCase):
    def test_cut_off_definition_is_requested_in_root_field_parts(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe
        requests = []

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            requests.append((payload['stage'], payload.get('definition_fields')))
            definition = reference_definition(payload['stage'])
            if payload['stage'] == 'rule_ir' and len(requests) == 1:
                return SimpleNamespace(content=json.dumps({'definition': definition})[:500],
                                       choices=[SimpleNamespace(finish_reason='length')])
            if payload.get('definition_fields'):
                definition = {k: v for k, v in definition.items() if k in payload['definition_fields']}
            has_actions = 'actions' in definition
            return json.dumps({'definition': definition, 'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests': tests_for_board() if payload['stage'] == 'rule_ir' and has_actions else []})

        with tempfile.TemporaryDirectory() as tmp:
            package = SourceGameImporter().import_path(fontless_tictactoe(tmp))
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=1).compile(package, out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        rule_requests = [fields for stage, fields in requests if stage == 'rule_ir']
        self.assertEqual(rule_requests[0], None)
        self.assertEqual([len(fields) for fields in rule_requests[1:]], [8, 3, 7])
        self.assertIn('definition requested in parts', json.dumps(report.compilation_trace['stages'][0]))


if __name__ == '__main__':
    unittest.main()
