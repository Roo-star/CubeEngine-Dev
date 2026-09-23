"""Production HTTP adapter through compilation and play, with labelled fixtures.

MockTransport replaces the network, not the compiler/validators/runtime. These
tests prove integration behavior, never live model quality or visual fidelity.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from srtp.llm_compiler_v1.client import OpenRouterLLMClient
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.project_viewer import ProjectHost
from srtp.source_importer import SourceGameImporter
from tests.test_engine_conversion_pipeline import ROOT, reference_definition, tests_for_board
from tests.test_openrouter_transport import completed


class HTTPPipelineTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline-test-key',
            'CUBEENGINE_LLM_OPENROUTER_MODEL':'openai/gpt-6-sol','CUBEENGINE_LLM_MAX_REQUESTS':'12',
            'CUBEENGINE_LLM_CHAT_RETRIES':'0','CUBEENGINE_LLM_MAX_TOKENS':'32768',
            'CUBEENGINE_LLM_REASONING_EFFORT':'medium'})
        environment.start(); self.addCleanup(environment.stop)
        loader=patch('srtp.llm_compiler_v1.client.load_compiler_env')
        loader.start(); self.addCleanup(loader.stop)
        self.package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')

    def response(self,payload):
        slot=payload['stage']; target=payload['design_intent'] is not None
        response={'definition':reference_definition(slot,'target' if target else 'source'),
            'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
            'behavior_tests':tests_for_board(3 if target else 2) if slot=='rule_ir' else []}
        if target and slot=='rule_ir':
            response['plan']={key:{} for key in ('topology','source_xy_policy','target_z','neighborhood',
                'movement','outcomes','presentation','input')}
            response['plan'].update(z_equals_one_tests=[],z_gt_one_tests=[],alternatives=[],unresolved=[])
        return response

    def test_http_repair_cache_approval_lift_and_physical_three_dimensional_win(self):
        calls=[]; scene_calls=0
        def handle(request):
            nonlocal scene_calls
            self.assertEqual(str(request.url),'https://openrouter.ai/api/v1/responses')
            body=json.loads(request.content)
            self.assertEqual(body['text']['format']['type'],'json_object')
            self.assertEqual(body['model'],'openai/gpt-6-sol')
            payload=json.loads(body['input'][-1]['content']); slot=payload['stage']
            target=payload['design_intent'] is not None
            calls.append((target,slot))
            self.assertIn('backend_profile',payload)
            self.assertIn('reference_catalog',payload)
            response=self.response(payload)
            if not target and slot=='scene_ir':
                scene_calls+=1
                if scene_calls==1:
                    response['definition']['prefabs'][0]['root']['components'][1]['properties']['shape']='cube'
                else:
                    self.assertIn('/shape','\n'.join(payload['repair_diagnostics']))
                    self.assertEqual(payload['previous_definition']['definition']['prefabs'][0]['root']['components'][1]['properties']['shape'],'cube')
            return completed(response,usage={'input_tokens':10,'output_tokens':10,'cost':0})
        client=OpenRouterLLMClient(transport=httpx.MockTransport(handle))
        compiler=SourceToIRCompiler(client=client,max_repairs=1)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=compiler.compile(self.package,out_dir=root/'source')
            self.assertTrue(source.ok,source.diagnostics)
            self.assertFalse(source.compile_ready,'Approval cannot be forged by model or compiler')
            self.assertEqual(source.compilation_trace['api_usage']['http_requests'],5)
            self.assertEqual([slot for _,slot in calls],['rule_ir','asset_ir','scene_ir','scene_ir','input_ir'])
            cached=compiler.compile(self.package,out_dir=root/'source')
            self.assertTrue(cached.ok,cached.diagnostics)
            self.assertEqual(len(calls),5)
            self.assertEqual(cached.compilation_trace['api_usage']['http_requests'],0)
            source_path=Path(source.output_dir)/'project.manifest.json'
            with self.assertRaisesRegex(ValueError,'Approve'): ProjectHost(source_path)
            approve_llm_manifest_file(source_path)
            target=compiler.compile_spatial_lift(self.package,source_bundle_dir=source_path.parent,
                target_dimensions={'x':3,'y':3,'z':3},out_dir=root/'target')
            self.assertTrue(target.ok,target.diagnostics)
            self.assertFalse(target.compile_ready)
            self.assertEqual(target.compilation_trace['api_usage']['http_requests'],4)
            manifest=Path(target.output_dir)/'project.manifest.json'; approve_llm_manifest_file(manifest)
            host=ProjectHost(manifest)
            try:
                self.assertEqual(host.snapshot.dimensions,(3,3,3))
                for coordinate in [(0,0,0),(0,1,0),(1,1,1),(0,2,0),(2,2,2)]:
                    self.assertTrue(host.mouse('mouse.button.primary',{'coordinate':coordinate}).accepted)
                    host.refresh_scene()
                    self.assertFalse(host.presentation.diagnostics())
                self.assertTrue(host.controller.snapshot().terminal)
                self.assertFalse(host.mouse('mouse.button.primary',{'coordinate':(2,0,0)}).accepted)
                self.assertTrue(host.controller.verify_replay()['passed'])
                host.controller.reset(); host.refresh_scene()
                self.assertFalse(host.controller.snapshot().terminal)
            finally: host.close()

    def test_repeated_invalid_http_response_stops_without_approvable_manifest(self):
        calls=[]
        def handle(request):
            payload=json.loads(json.loads(request.content)['input'][-1]['content'])
            calls.append(payload['stage']); response=self.response(payload)
            if payload['stage']=='scene_ir':
                response['definition']['prefabs'][0]['root']['components'][1]['properties']['shape']='cube'
            return completed(response)
        compiler=SourceToIRCompiler(client=OpenRouterLLMClient(transport=httpx.MockTransport(handle)),max_repairs=8)
        with tempfile.TemporaryDirectory() as temporary:
            destination=Path(temporary)/'source'
            result=compiler.compile(self.package,out_dir=destination)
            self.assertFalse(result.ok); self.assertFalse(result.compile_ready)
            self.assertEqual(calls,['rule_ir','asset_ir','scene_ir','scene_ir'])
            self.assertIn('stopped_reason',result.compilation_trace['stages'][-1])
            checkpoint=json.loads(destination.with_name('source.stages.json').read_text())
            self.assertEqual(set(checkpoint['stages']),{'rule_ir','asset_ir'})
            self.assertEqual(set(checkpoint['rejected_stages']),{'scene_ir'})
            self.assertFalse((destination/'project.manifest.json').exists())


if __name__=='__main__': unittest.main()
