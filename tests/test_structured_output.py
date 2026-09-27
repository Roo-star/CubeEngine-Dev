"""Structured-output requests, provider fallback and local reply-envelope checks (offline)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from srtp.llm_compiler_v1.client import LLMTransportError, OpenRouterLLMClient
from srtp.llm_compiler_v1.reply_schemas import CRITIC_REPLY, STAGE_REPLY, WORKER_REPLY, reply_errors
from tests.test_openrouter_transport import completed


class StructuredTransportTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'offline-test-key',
                                              'CUBEENGINE_LLM_OPENROUTER_MODEL': 'openai/gpt-6-sol',
                                              'CUBEENGINE_LLM_CHAT_RETRIES': '0'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        loader = patch('srtp.llm_compiler_v1.client.load_compiler_env')
        loader.start()
        self.addCleanup(loader.stop)
        self.formats = []

    def client(self, respond):
        def handle(request):
            self.formats.append(json.loads(request.content)['text']['format'])
            return respond(len(self.formats))
        return OpenRouterLLMClient(transport=httpx.MockTransport(handle))

    def test_schema_is_sent_as_non_strict_json_schema(self):
        with self.client(lambda n: completed({'operations': []})) as client:
            client.chat_json([], schema=WORKER_REPLY, schema_name='cubeengine_rule_ir')
            client.chat_json([])
        self.assertEqual(self.formats[0], {'type': 'json_schema', 'name': 'cubeengine_rule_ir',
                                           'schema': WORKER_REPLY, 'strict': False})
        self.assertEqual(self.formats[1], {'type': 'json_object'}, 'no schema keeps JSON mode')

    def test_rejected_schema_falls_back_once_and_is_remembered(self):
        def respond(number):
            if number == 1:
                return httpx.Response(400, json={'error': {'code': 400, 'message': 'response_format not supported'}})
            return completed({'definition': {}})
        with self.client(respond) as client:
            self.assertEqual(client.chat_json([], schema=STAGE_REPLY).parsed, {'definition': {}})
            client.chat_json([], schema=STAGE_REPLY)
            self.assertIn('json_schema rejected', client.usage_summary['output_format'])
        self.assertEqual([f['type'] for f in self.formats], ['json_schema', 'json_object', 'json_object'])

    def test_explicit_formats_and_other_errors_do_not_fall_back(self):
        rejected = lambda n: httpx.Response(400, json={'error': {'code': 400}})
        with patch.dict(os.environ, {'CUBEENGINE_LLM_OUTPUT_FORMAT': 'json_schema'}):
            with self.client(rejected) as client, self.assertRaises(LLMTransportError):
                client.chat_json([], schema=STAGE_REPLY)
        with patch.dict(os.environ, {'CUBEENGINE_LLM_OUTPUT_FORMAT': 'json_object'}):
            with self.client(lambda n: completed({})) as client:
                client.chat_json([], schema=STAGE_REPLY)
        with self.client(lambda n: httpx.Response(401, json={'error': {'code': 401}})) as client:
            with self.assertRaises(LLMTransportError):
                client.chat_json([], schema=STAGE_REPLY)
        self.assertEqual([f['type'] for f in self.formats], ['json_schema', 'json_object', 'json_schema'])
        with patch.dict(os.environ, {'CUBEENGINE_LLM_OUTPUT_FORMAT': 'yaml'}):
            with self.assertRaises(LLMTransportError), self.client(lambda n: completed({})):
                pass


class ReplyEnvelopeTests(unittest.TestCase):
    def test_envelope_errors_are_precise(self):
        self.assertEqual(reply_errors({'operations': [{'op': 'replace', 'path': '/x', 'value': 1}],
                                       'evidence': ['ev:agent.1']}, WORKER_REPLY), [])
        self.assertIn('reply/operations: expected array',
                      reply_errors({'operations': {'op': 'replace'}}, WORKER_REPLY))
        errors = reply_errors({'operations': [{'op': 'rewrite'}]}, WORKER_REPLY)
        self.assertTrue(any('/operations/0/op' in e for e in errors) and any('/operations/0/path' in e for e in errors), errors)
        self.assertTrue(reply_errors({'verdict': 'maybe', 'issues': []}, CRITIC_REPLY))

    def test_agentic_worker_gets_envelope_diagnostics_and_schemas(self):
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        from tests.test_llm_agentic_compiler import (FILE, TICTACTOE, _PASS, _ScriptedChat, _asset_patch, _input_patch,
                                                     _rule_patch, _scene_patch, _spec)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            shutil.copy(TICTACTOE, source / FILE)
            chat = _ScriptedChat([{'action': 'finish', 'spec': _spec()},
                                  {'operations': {'op': 'replace', 'path': '/flow'}, 'evidence': 'ev:agent.1'},
                                  _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS])
            compiler = AgenticSourceToIRCompiler(chat_fn=chat)
            seen = []
            original = compiler.client.chat_json

            def spy(messages, **kwargs):
                seen.append(kwargs.get('schema_name'))
                return original(messages, **kwargs)

            compiler.client.chat_json = spy
            report = compiler.compile_path(source, out_dir=Path(tmp) / 'out', title='Tic Tac Toe 2D')
            self.assertTrue(report.ok, report.diagnostics)
            repair = json.loads(chat.requests[2][-1]['content'])
            self.assertEqual(repair['diagnostics'], ['reply envelope: reply/operations: expected array'])
            self.assertEqual(seen[:3], ['cubeengine_analyst', 'cubeengine_rule_ir', 'cubeengine_rule_ir'])
            self.assertEqual(seen[-1], 'cubeengine_critic')

    def test_staged_workspace_passes_the_stage_schema(self):
        from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
        root = Path(__file__).resolve().parents[1] / 'srtp/reference_games/pygame_snake'
        workspace = SourceWorkspace(root, root / 'snake.py')
        seen = []

        class Client:
            def chat_json(self, messages, **kwargs):
                seen.append(kwargs.get('schema'))
                from srtp.llm_compiler_v1.client import LLMChatResult
                return LLMChatResult('{"definition":{}}', 'mock', 'mock', {'definition': {}})

        workspace.chat(Client(), [{'role': 'system', 'content': 'x'}, {'role': 'user', 'content': '{}'}], schema=STAGE_REPLY)
        self.assertEqual(seen, [STAGE_REPLY])


if __name__ == '__main__':
    unittest.main()
