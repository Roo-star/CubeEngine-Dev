"""Offline HTTP-contract tests; never access the network or a real API key."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from srtp.llm_compiler_v1.client import OpenRouterLLMClient, LLMClientError, LLMTransportError


def completed(value, **updates):
    body = {'status':'completed', 'model':'openai/gpt-6-sol', 'output':[
        {'type':'reasoning','summary':[]},
        {'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}]}
    body.update(updates)
    return httpx.Response(200, json=body)


class OpenRouterTransportTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'OPENROUTER_API_KEY':'offline-test-key',
            'CUBEENGINE_LLM_OPENROUTER_MODEL':'openai/gpt-6-sol','CUBEENGINE_LLM_CHAT_RETRIES':'2'}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        loader = patch('srtp.llm_compiler_v1.client.load_compiler_env')
        loader.start(); self.addCleanup(loader.stop)

    def client(self, handler):
        return OpenRouterLLMClient(transport=httpx.MockTransport(handler))

    def test_request_uses_official_responses_only_and_preserves_conversation(self):
        messages = [{'role':'system','content':'Build JSON'}, {'role':'user','content':'source'},
                    {'role':'assistant','content':'{"source_requests":[]}'},
                    {'role':'user','content':'{"source_results":[]}'}]
        def handle(request):
            self.assertEqual(str(request.url),'https://openrouter.ai/api/v1/responses')
            self.assertEqual(request.headers['authorization'],'Bearer offline-test-key')
            self.assertEqual(request.headers['x-openrouter-title'],'CubeEngine')
            body = json.loads(request.content)
            self.assertEqual(body['input'],messages)
            self.assertEqual(body['model'],'openai/gpt-6-sol')
            self.assertEqual(body['reasoning'],{'effort':'medium'})
            self.assertEqual(body['text']['format']['type'],'json_object')
            self.assertEqual(body['max_output_tokens'],32768)
            self.assertFalse(body['store'])
            self.assertFalse(body['stream'])
            self.assertEqual(body['provider'],{'require_parameters':True})
            self.assertNotIn('models',body)
            self.assertNotIn('temperature',body)
            self.assertNotIn('max_tokens',body)
            return completed({'definition':{}})
        with self.client(handle) as client:
            result = client.chat_json(messages)
        self.assertEqual(result.provider,'openrouter')
        self.assertEqual(result.parsed,{'definition':{}})
        self.assertIsNone(client._client)

    def test_missing_key_never_falls_back_to_legacy_providers(self):
        os.environ.pop('OPENROUTER_API_KEY')
        os.environ.update(OPENAI_API_KEY='direct-key',GEMINI_API_KEY='old-key',GROQ_API_KEY='other-old-key')
        with self.assertRaisesRegex(LLMTransportError,'OPENROUTER_API_KEY'):
            with self.client(lambda r:self.fail('No request is allowed')): pass

    def test_placeholder_and_key_lists_fail_locally(self):
        for key in ('PASTE_YOUR_OPENROUTER_API_KEY_HERE','["one","two"]','one,two',''):
            with self.subTest(key=key), patch.dict(os.environ,{'OPENROUTER_API_KEY':key}):
                with self.assertRaises(LLMTransportError):
                    with self.client(lambda r:self.fail('No request is allowed')): pass

    def test_config_is_resolved_after_env_reload(self):
        client = self.client(lambda r:completed({}))
        with patch('srtp.llm_compiler_v1.client.load_compiler_env', side_effect=lambda **kw:
                   os.environ.update(CUBEENGINE_LLM_OPENROUTER_MODEL='openai/gpt-6-astra',
                                     CUBEENGINE_LLM_MAX_TOKENS='40000',CUBEENGINE_LLM_REASONING_EFFORT='high')):
            with client:
                self.assertEqual(client.cache_identity['model'],'openai/gpt-6-astra')
                self.assertEqual(client.max_tokens,40000)
                self.assertEqual(client.reasoning_effort,'high')

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_busy_and_rate_limit_retry_then_succeed(self, sleep):
        replies = iter([httpx.Response(503),httpx.Response(429,headers={'retry-after':'7'}),completed({})])
        with self.client(lambda r:next(replies)) as client:
            self.assertEqual(client.chat_json([]).parsed,{})
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[2.0,7.0])

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_retries_stop_and_do_not_leak_provider_body(self, sleep):
        seen=[]
        def handle(request):
            seen.append(request)
            return httpx.Response(503,json={'error':{'message':'offline-test-key secret'}})
        with self.client(handle) as client, self.assertRaises(LLMTransportError) as error:
            client.chat_json([])
        self.assertEqual(len(seen),3)
        self.assertNotIn('offline-test-key',str(error.exception))
        self.assertNotIn('secret',str(error.exception))

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_account_errors_and_long_retry_after_do_not_spin(self, sleep):
        for status, code, headers, expected in [
            (401,'invalid_api_key',{},'API key'),(403,'',{},'Access denied'),
            (404,'model_not_found',{},'Model unavailable'),
            (429,'insufficient_quota',{},'credits or key spending limit'),
            (402,402,{},'credits or key spending limit'),
            (429,'rate_limit_exceeded',{'retry-after':'60'},'Rate limit'),
            (400,'invalid_parameter',{},'Request rejected')]:
            seen=[]
            def handle(request):
                seen.append(request)
                return httpx.Response(status,json={'error':{'code':code}},headers=headers)
            with self.subTest(status=status,code=code), self.client(handle) as client:
                with self.assertRaisesRegex(LLMTransportError,expected): client.chat_json([])
                self.assertEqual(len(seen),1)
        sleep.assert_not_called()

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_network_timeout_has_bounded_retries(self, sleep):
        seen=[]
        def handle(request):
            seen.append(request); raise httpx.ReadTimeout('secret details',request=request)
        with self.client(handle) as client, self.assertRaisesRegex(LLMTransportError,'3 attempt'):
            client.chat_json([])
        self.assertEqual(len(seen),3)

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_openrouter_credit_limits_are_classified_and_never_retry(self, sleep):
        for metadata, expected in [
            ({},'credits or key spending limit'),
            ({'limit_source':'openrouter_key_limit'},'key spending limit reached'),
            ({'limit_source':'openrouter_credits','reason':'weight_exceeds_budget'},'estimated cost exceeds'),
            ({'limit_source':'openrouter_in_flight_budget'},'temporarily occupied')]:
            seen=[]
            def handle(request):
                seen.append(request)
                return httpx.Response(402, json={'error':{'code':402,'metadata':metadata,
                    'message':'offline-test-key must never be displayed'}})
            with self.subTest(metadata=metadata), self.client(handle) as client:
                with self.assertRaisesRegex(LLMTransportError,expected) as error:
                    client.chat_json([])
                self.assertEqual(len(seen),1)
                self.assertIn('HTTP attempts: 1',str(error.exception))
                self.assertNotIn('offline-test-key',str(error.exception))
                self.assertNotIn('Rate limit reached',str(error.exception))
                self.assertNotIn('Wait for the API limit',str(error.exception))
        sleep.assert_not_called()

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_inflight_budget_honors_retry_after_only_when_explicit(self, sleep):
        error={'error':{'code':402,'metadata':{'limit_source':'openrouter_in_flight_budget'}}}
        replies=iter([httpx.Response(402,json=error,headers={'retry-after':'7'}),completed({})])
        with self.client(lambda r:next(replies)) as client:
            self.assertEqual(client.chat_json([]).parsed,{})
        sleep.assert_called_once_with(7.0)
        sleep.reset_mock()
        with self.client(lambda r:httpx.Response(402,json=error,headers={'retry-after':'60'})) as client:
            with self.assertRaisesRegex(LLMTransportError,'Retry-After: 60'): client.chat_json([])
        sleep.assert_not_called()

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_http_200_error_and_canonical_error_type_never_become_ir(self, sleep):
        for status, kind, expected in [(200,'authentication','API key'),
                (200,'payment_required','credits'),(200,'provider_overloaded','overloaded'),
                (503,'permission_denied','Access denied')]:
            seen=[]
            def handle(request):
                seen.append(request)
                return httpx.Response(status,json={'status':'failed','error_type':kind,
                    'id':'gen-test123','error':{'code':'server_error','message':'offline-test-key'},
                    'output':[{'type':'message','content':[{'type':'output_text','text':'{}'}]}]})
            with self.subTest(status=status,kind=kind),self.client(handle) as client:
                with self.assertRaisesRegex(LLMTransportError,expected) as error: client.chat_json([])
                self.assertEqual(len(seen),1)
                self.assertIn('gen-test123',str(error.exception))
                self.assertNotIn('offline-test-key',str(error.exception))
        sleep.assert_not_called()

    def test_bare_direct_openai_model_name_is_rejected_before_network(self):
        with patch.dict(os.environ,{'CUBEENGINE_LLM_OPENROUTER_MODEL':'gpt-6-sol'}):
            with self.assertRaisesRegex(LLMTransportError,'openai/gpt-6-sol'):
                with self.client(lambda r:self.fail('No network allowed')): pass

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_job_request_cap_includes_retries_and_tracks_returned_usage(self,sleep):
        with patch.dict(os.environ,{'CUBEENGINE_LLM_MAX_REQUESTS':'2'}):
            replies=iter([httpx.Response(503),completed({},usage={'input_tokens':100,'output_tokens':20,'cost':0.012})])
            with self.client(lambda r:next(replies)) as client:
                client.chat_json([])
                with self.assertRaisesRegex(LLMTransportError,'MAX_REQUESTS=2'): client.chat_json([])
                self.assertEqual(client.usage_summary,{'http_requests':2,'input_tokens':100,'output_tokens':20,'reported_cost_usd':0.012})

    def test_incomplete_refused_or_malformed_json_never_becomes_ir(self):
        for response in [
            completed({},status='incomplete',incomplete_details={'reason':'max_output_tokens'}),
            completed({},output=[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]),
            completed({},output=[{'type':'message','content':[{'type':'output_text','text':'{} trailing'}]}]),
            completed({},status='failed'),httpx.Response(200,text='<html>proxy error</html>')]:
            with self.subTest(response=response), self.client(lambda r:response) as client:
                with self.assertRaises(LLMClientError): client.chat_json([])

    def test_content_filter_stops_instead_of_spending_semantic_repairs(self):
        with self.client(lambda r:completed({},status='incomplete',incomplete_details={'reason':'content_filter'})) as client:
            with self.assertRaises(LLMTransportError): client.chat_json([])

    def test_source_and_lift_use_same_http_transport_and_model_cache_is_separate(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import ROOT, reference_definition, tests_for_board
        calls=[]
        def handle(request):
            body=json.loads(request.content); payload=json.loads(body['input'][-1]['content'])
            if payload.get('task') == 'design_intent':
                calls.append((body['model'],True,'design_intent'))
                return completed({'operation':'transform','scope':['rule','scene','asset','input'],
                    'changes':[{'kind':'set_extent','axis':'z','value':3}],
                    'requires_confirmation':False},model=body['model'])
            slot=payload['stage']; target=payload['design_intent'] is not None
            calls.append((body['model'],target,slot))
            response={'definition':reference_definition(slot,'target' if target else 'source'),
                'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                'behavior_tests':tests_for_board(3 if target else 2) if slot=='rule_ir' else []}
            if target and slot=='rule_ir':
                response['plan']={key:{} for key in ('topology','source_xy_policy','target_z','neighborhood','movement','outcomes','presentation','input')}
                response['plan'].update(z_equals_one_tests=[],z_gt_one_tests=[],alternatives=[],unresolved=[])
            return completed(response,model=body['model'])
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        with tempfile.TemporaryDirectory() as tmp:
            compiler=SourceToIRCompiler(client=self.client(handle),max_repairs=0)
            source=compiler.compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(source.ok,source.diagnostics)
            self.assertEqual(source.provider,'openrouter')
            self.assertFalse(source.compile_ready)
            again=compiler.compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(again.ok,again.diagnostics); self.assertEqual(len(calls),4)
            os.environ['CUBEENGINE_LLM_OPENROUTER_MODEL']='openai/gpt-6-astra'
            changed=compiler.compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(changed.ok,changed.diagnostics); self.assertEqual(len(calls),8)
            self.assertEqual(changed.model,'openai/gpt-6-astra')
            approve_llm_manifest_file(Path(changed.output_dir)/'project.manifest.json')
            target=compiler.compile_spatial_lift(package,source_bundle_dir=Path(changed.output_dir),
                target_dimensions={'x':3,'y':3,'z':3},out_dir=Path(tmp)/'target')
            self.assertTrue(target.ok,target.diagnostics)
            self.assertEqual(target.provider,'openrouter')
            self.assertEqual(len(calls),12)
            self.assertTrue(all(row[1] for row in calls[-4:]))
            natural=compiler.compile_spatial_lift(package,source_bundle_dir=Path(changed.output_dir),
                intent_text='Preserve XY and extend Z to three layers',out_dir=Path(tmp)/'natural')
            self.assertTrue(natural.ok,natural.diagnostics)
            self.assertEqual(natural.provider,'openrouter')
            self.assertEqual(len(calls),17)
            self.assertEqual(calls[12][2],'design_intent')


if __name__ == '__main__': unittest.main()
