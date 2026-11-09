"""Pipeline stability: engine defects are not sent to the model, reply envelopes are normalized once,
unchanged Lift stages are accepted, and the run is checked against credits and request limits before paying."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_llm_budget import fontless_tictactoe


class _StagedCase(unittest.TestCase):
    def setUp(self):
        from srtp.source_importer import SourceGameImporter
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.package = SourceGameImporter().import_path(fontless_tictactoe(self.tmp))
        self.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def reply(self, payload):
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        return {'definition': reference_definition(payload['stage']),
                'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                'behavior_tests': tests_for_board() if payload['stage'] == 'rule_ir' else []}

    def chat(self, messages, **kwargs):
        payload = json.loads(messages[-1]['content'])
        self.calls.append(payload['stage'])
        return json.dumps(self.reply(payload))


class EngineFaultTests(_StagedCase):
    def test_engine_key_error_stops_without_a_repair_request_and_keeps_the_reply(self):
        from srtp.llm_compiler_v1 import staged
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        real = staged._execute_stage

        def broken(slot, *args, **kwargs):
            if slot == 'scene_ir':
                {}['key']  # an engine read of an optional field, like RuleState.parameter_value did
            return real(slot, *args, **kwargs)

        checkpoint = self.tmp / 'source.stages.json'
        with mock.patch.object(staged, '_execute_stage', broken):
            report = SourceToIRCompiler(chat_fn=self.chat, max_repairs=3).compile(
                self.package, out_dir=self.tmp / 'out', checkpoint_path=checkpoint)
        self.assertFalse(report.ok)
        self.assertEqual(self.calls, ['rule_ir', 'asset_ir', 'scene_ir'], 'no paid Scene repair for an engine bug')
        self.assertIn('engine error, not sent to the model: KeyError', report.diagnostics[0])
        fault = report.compilation_trace['engine_fault']
        self.assertEqual((fault['stage'], fault['type'], fault['kind']), ('scene_ir', 'KeyError', 'engine'))
        self.assertTrue(any('test_pipeline_stability.py' in item for item in fault['location']))
        self.assertIn('scene_ir', json.loads(checkpoint.read_text(encoding='utf-8'))['rejected_stages'])
        # After the engine fix, Continue revalidates the kept reply and pays only for the remaining stage.
        rerun = SourceToIRCompiler(chat_fn=self.chat, max_repairs=3).compile(
            self.package, out_dir=self.tmp / 'out2', checkpoint_path=checkpoint)
        self.assertTrue(rerun.ok, rerun.diagnostics)
        self.assertEqual(self.calls[3:], ['input_ir'])

    def test_model_errors_are_still_repaired(self):
        from srtp.llm_compiler_v1.engine_fault import is_engine_fault
        from srtp.llm_compiler_v1.client import LLMTransportError
        from srtp.llm_compiler_v1.program_builder import DefinitionValidationError
        from srtp.ir_v2.patching import RuleIRPatchError
        self.assertFalse(is_engine_fault(DefinitionValidationError(['bad'])))
        self.assertFalse(is_engine_fault(RuleIRPatchError('bad')))
        self.assertFalse(is_engine_fault(LLMTransportError('down')))
        self.assertTrue(is_engine_fault(KeyError('key')))
        self.assertTrue(is_engine_fault(TypeError('unhashable')))
        self.assertTrue(is_engine_fault(OSError('disk')))



class RunPlanTests(unittest.TestCase):
    """Before the first paid request: request limit, cost cap and OpenRouter credits cover the remaining stages."""

    def setUp(self):
        from unittest.mock import patch
        environment = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'offline-test-key',
                                              'CUBEENGINE_LLM_OPENROUTER_MODEL': 'openai/gpt-6-sol'}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        loader = patch('srtp.llm_compiler_v1.client.load_compiler_env')
        loader.start(); self.addCleanup(loader.stop)
        self.posts = []

    def handler(self, credits=None, limit_remaining=None):
        import httpx
        from tests.test_openrouter_transport import completed

        def handle(request):
            url = str(request.url)
            if url.endswith('/api/v1/key'):
                return httpx.Response(200, json={'data': {'limit_remaining': limit_remaining}})
            if url.endswith('/api/v1/credits'):
                if credits is None:
                    return httpx.Response(500, json={})
                return httpx.Response(200, json={'data': {'total_credits': credits, 'total_usage': 0}})
            self.posts.append(url)
            return completed({'definition': {}})
        return handle

    def run_plan(self, planned, env=None, **account):
        import httpx
        from unittest.mock import patch
        from srtp.llm_compiler_v1.client import OpenRouterLLMClient
        with patch.dict(os.environ, env or {}):
            with OpenRouterLLMClient(transport=httpx.MockTransport(self.handler(**account))) as client:
                client.planned_requests = planned
                client.chat_json([{'role': 'user', 'content': 'x' * 300000}])  # ~100k tokens upper bound
                return client.plan_check

    def test_request_limit_below_the_remaining_stages_sends_nothing(self):
        from srtp.llm_compiler_v1.client import LLMCostBudgetExceeded
        with self.assertRaisesRegex(LLMCostBudgetExceeded, 'at least 4 paid request.*MAX_REQUESTS=3'):
            self.run_plan(4, {'CUBEENGINE_LLM_MAX_REQUESTS': '3'})
        self.assertEqual(self.posts, [])

    def test_cost_cap_and_credits_are_checked_before_sending(self):
        from srtp.llm_compiler_v1.client import LLMCostBudgetExceeded
        with self.assertRaisesRegex(LLMCostBudgetExceeded, 'MAX_COST_USD leaves US\$1.00'):
            self.run_plan(4, {'CUBEENGINE_LLM_MAX_COST_USD': '1'})
        with self.assertRaisesRegex(LLMCostBudgetExceeded, 'key/credits allow US\$0.50'):
            self.run_plan(4, credits=5.0, limit_remaining=0.5)  # the lower of key limit and credits
        self.assertEqual(self.posts, [])
        check = self.run_plan(4, credits=5.0)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(check['account_headroom_usd'], 5.0)
        self.assertAlmostEqual(check['estimate_per_request_usd'], (100001 * 2.5 + 8000 * 10) / 1e6, places=3)

    def test_unknown_balance_never_blocks(self):
        check = self.run_plan(4)  # /credits fails and the key has no limit
        self.assertIsNone(check['account_headroom_usd'])
        self.assertEqual(len(self.posts), 1)


class StagedPlanTests(_StagedCase):
    def test_the_pipeline_plans_only_stages_without_a_kept_reply(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        checkpoint = self.tmp / 'source.stages.json'
        first = SourceToIRCompiler(chat_fn=self.chat, max_repairs=0)
        first.client.planned_requests = None
        report = first.compile(self.package, out_dir=self.tmp / 'a', checkpoint_path=checkpoint)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(first.client.planned_requests, 4)
        stored = json.loads(checkpoint.read_text(encoding='utf-8'))
        stored['stages'].pop('input_ir')
        checkpoint.write_text(json.dumps(stored), encoding='utf-8')
        second = SourceToIRCompiler(chat_fn=self.chat, max_repairs=0)
        self.assertTrue(second.compile(self.package, out_dir=self.tmp / 'b', checkpoint_path=checkpoint).ok)
        self.assertEqual(second.client.planned_requests, 1)


class PayloadAndHintTests(_StagedCase):
    def chat(self, messages, **kwargs):
        payload = json.loads(messages[-1]['content'])
        self.calls.append(payload['stage'])
        self.payloads.append(payload)
        reply = self.reply(payload)
        if payload['stage'] == 'scene_ir' and self.blocker and not payload.get('repair_diagnostics'):
            reply['definition'] = dict(reply['definition'], unresolved=[{
                'path': '/bindings', 'required': True, 'owner': 'llm',
                'reason': 'The source shows elapsed wall-clock seconds; the board only counts ticks.'}])
        return json.dumps(reply)

    def compile(self, blocker=False):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        self.payloads, self.blocker = [], blocker
        return SourceToIRCompiler(chat_fn=self.chat, max_repairs=1).compile(self.package, out_dir=self.tmp / 'out')

    def test_each_stage_gets_its_profile_sections_and_documents(self):
        from srtp.llm_compiler_v1.backend_contract import PROFILE_SECTIONS
        self.assertTrue(self.compile().ok)
        for payload in self.payloads:
            slot = payload['stage']
            self.assertEqual(set(payload['backend_profile']), set(PROFILE_SECTIONS[slot]), slot)
        input_docs = next(p for p in self.payloads if p['stage'] == 'input_ir')['current_documents']
        self.assertIn('omitted', input_docs['asset_ir'])
        self.assertIn('content_hash', input_docs['asset_ir'])
        self.assertIn('nodes', input_docs['scene_ir'])
        self.assertIn('wall_clock', PROFILE_SECTIONS['rule_ir'])

    def test_required_unresolved_repair_names_the_existing_capability(self):
        report = self.compile(blocker=True)
        self.assertTrue(report.ok, report.diagnostics)
        repair = [p for p in self.payloads if p['stage'] == 'scene_ir'][1]
        text = ' '.join(repair['repair_diagnostics'])
        self.assertIn('is required and blocks compilation', text)
        self.assertIn('backend_profile.wall_clock', text)

    def test_hints_are_generic(self):
        from srtp.llm_compiler_v1.backend_contract import capability_hints
        self.assertEqual([h[0] for h in capability_hints('Countdown clock in seconds is not modelled')], ['wall_clock'])
        self.assertEqual([h[0] for h in capability_hints('Score panel shows max(0, lives minus hits)')],
                         ['presentation_expression'])
        self.assertEqual(capability_hints('Network multiplayer lobby is absent'), [])


class CacheFriendlyMessagesTests(_StagedCase):
    def test_source_workspace_is_one_identical_message_after_the_system_prompt(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.llm_compiler_v1.source_workspace import stage_payload
        seen = []

        def chat(messages, **kwargs):
            seen.append([dict(m) for m in messages])
            return json.dumps(self.reply(stage_payload(messages)))
        self.assertTrue(SourceToIRCompiler(chat_fn=chat, max_repairs=0).compile(
            self.package, out_dir=self.tmp / 'out').ok)
        self.assertEqual(len(seen), 4)
        for messages in seen:
            self.assertEqual([m['role'] for m in messages], ['system', 'user', 'user'])
            self.assertEqual(list(json.loads(messages[1]['content'])), ['source_workspace'])
            self.assertNotIn('source_workspace', json.loads(messages[2]['content']))
        # The cacheable prefix (system prompt + workspace) is byte-identical across all four stages.
        self.assertEqual(len({(m[0]['content'], m[1]['content']) for m in seen}), 1)
        merged = stage_payload(seen[0])
        self.assertIn('source_workspace', merged)
        self.assertEqual(merged['stage'], 'rule_ir')


class CitationHashTests(unittest.TestCase):
    def test_a_clipped_hash_of_the_cited_file_is_completed_and_anything_else_is_not(self):
        import hashlib
        from srtp.llm_compiler_v1.program_builder import _complete_citation_hash
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pkg').mkdir()
            (root / 'pkg/frame.py').write_bytes(b'x = 1')
            real = hashlib.sha256(b'x = 1').hexdigest()
            notes = []
            self.assertEqual(_complete_citation_hash('pkg/frame.py:1-1@' + real[:63], root, notes),
                             'pkg/frame.py:1-1@' + real)
            self.assertEqual(_complete_citation_hash('pkg/frame.py:1-1@' + real + 'a', root, notes),
                             'pkg/frame.py:1-1@' + real)
            self.assertEqual(len(notes), 2)
            for wrong in ('pkg/frame.py:1-1@' + real[:40],                 # too short to be a clip
                          'pkg/frame.py:1-1@' + ('0' * 63),                # a different file's hash
                          '../outside.py:1-1@' + real[:63],                # outside the source root
                          'pkg/missing.py:1-1@' + real[:63]):
                self.assertEqual(_complete_citation_hash(wrong, root, []), wrong)


class PressActionHintTests(unittest.TestCase):
    def test_mouse_down_actions_are_documented_and_hinted(self):
        from srtp.llm_compiler_v1.backend_contract import capability_hints, profile_for
        self.assertIn('press_actions', profile_for('input_ir')['pointer_routing'])
        reason = 'The source toggles the marker on right mouse-down; this backend buffers phases until release.'
        self.assertIn('pointer_routing', [h[0] for h in capability_hints(reason)])


if __name__ == '__main__':
    unittest.main()
