"""Run provenance, request archive, pre-payment check and pipeline capability consistency."""
import json
import tempfile
import unittest
from pathlib import Path


def _chat(broken_stage=None, calls=None):
    from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board

    def chat(messages, **kwargs):
        payload = json.loads(messages[-1]['content'])
        stage = payload['stage']
        if calls is not None:
            calls.append(stage)
        definition = reference_definition(stage)
        if stage == broken_stage:
            definition['bindings'] = 'not a list'
        return json.dumps({'definition': definition, 'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                           'behavior_tests': tests_for_board() if stage == 'rule_ir' else []})
    return chat


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        from srtp.source_importer import SourceGameImporter
        from tests.test_llm_budget import fontless_tictactoe
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = fontless_tictactoe(self.tmp)
        self.package = SourceGameImporter().import_path(self.source)

    def tearDown(self):
        self._tmp.cleanup()

    def compile(self, out, broken_stage=None, calls=None):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        compiler = SourceToIRCompiler(chat_fn=_chat(broken_stage, calls), max_repairs=0)
        compiler.use_source_oracle = False
        return compiler.compile(self.package, out_dir=self.tmp / out)

    def read(self, report):
        out = Path(report.output_dir)
        provenance = json.loads((out / 'provenance.json').read_text(encoding='utf-8'))
        requests = [json.loads(line) for line in (out / 'requests.jsonl').read_text(encoding='utf-8').splitlines()]
        return provenance, requests

    def test_every_run_records_code_contracts_cache_and_exact_requests(self):
        report = self.compile('ok')
        provenance, requests = self.read(report)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual([r['stage'] for r in requests], ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir'])
        self.assertEqual([m['role'] for m in requests[0]['messages']], ['system', 'user'])
        self.assertIn('"stage":"rule_ir"', requests[0]['messages'][1]['content'].replace(' ', ''))
        self.assertTrue(requests[0]['response_text'])
        self.assertTrue(provenance['code']['srtp_imported_from'].endswith('srtp'))
        self.assertEqual(len(provenance['contracts']['staged_system_prompt_sha256']), 64)
        self.assertIn('backend_profile_version', provenance['contracts'])
        self.assertEqual(provenance['requests']['count'], 4)
        self.assertNotIn('OPENROUTER_API_KEY', json.dumps(provenance))
        self.assertTrue(provenance['repair_summary']['stages']['input_ir']['passed'])

    def test_failed_runs_keep_the_record_and_a_rerun_shows_its_cache_source(self):
        failed = self.compile('run', broken_stage='input_ir')
        self.assertFalse(failed.ok)
        provenance, requests = self.read(failed)
        self.assertEqual(len(requests), 4)
        self.assertEqual(provenance['repair_summary']['stages']['input_ir']['passed'], False)
        self.assertTrue(provenance['repair_summary']['stages']['input_ir']['failures'])
        calls = []
        rerun = self.compile('run', calls=calls)
        self.assertTrue(rerun.ok, rerun.diagnostics)
        provenance, requests = self.read(rerun)
        self.assertEqual(calls, ['input_ir'], 'accepted stages came from the checkpoint')
        self.assertEqual(provenance['cache']['matched_by'], 'signature')
        self.assertEqual(provenance['cache']['stages_from_cache'], ['asset_ir', 'rule_ir', 'scene_ir'])
        self.assertEqual([r['stage'] for r in requests], ['input_ir'])

    def test_preflight_names_what_a_run_would_reuse_and_pay_for(self):
        from srtp.llm_compiler_v1.client import OpenRouterLLMClient
        from srtp.llm_compiler_v1.preflight import source_preflight
        self.compile('run', broken_stage='input_ir')
        same_kind = OpenRouterLLMClient(chat_fn=_chat())  # the checkpoint identity includes the provider
        report = source_preflight(self.source, checkpoint=self.tmp / 'run.stages.json', load_env=False, client=same_kind)
        self.assertEqual(report['checkpoint']['matched_by'], 'signature')
        self.assertTrue(report['stages']['rule_ir'].startswith('reuse'))
        self.assertTrue(report['stages']['input_ir'].startswith('re-validate'))
        self.assertEqual(report['estimate']['paid_stages'], ['input_ir'])
        fresh = source_preflight(self.source, checkpoint=self.tmp / 'none.stages.json', load_env=False, client=same_kind)
        self.assertEqual(fresh['estimate']['first_attempt_calls'], 4)


class CapabilityConsistencyTests(unittest.TestCase):
    def test_every_backend_capability_reaches_both_pipelines(self):
        from srtp.llm_compiler_v1.backend_contract import profile, profile_for, unassigned_profile_sections
        self.assertEqual(unassigned_profile_sections(), [], 'assign new profile sections to an IR author')
        full = profile()
        for ir_key in ('rule_ir', 'asset_ir', 'scene_ir', 'input_ir'):
            for key, value in profile_for(ir_key).items():
                self.assertEqual(value, full[key])

    def test_agentic_workers_receive_the_live_profile(self):
        from srtp.llm_compiler_v1.agent_prompts import worker_messages
        messages = worker_messages(ir_key='input_ir', spec={}, base_document={}, evidence_menu=[])
        payload = json.loads(messages[-1]['content'])
        self.assertIn('background_click', json.dumps(payload['backend_profile']['pointer_routing']))
        self.assertIn('when', payload['backend_profile']['host_command_target'])

    def test_review_issues_are_attributed_to_their_origin(self):
        from srtp.llm_compiler_v1.agentic import _issue_origin
        self.assertEqual(_issue_origin('runtime probe error: x'), 'behavior_probe')
        self.assertEqual(_issue_origin('compile gate: Visual check: board'), 'visual_check')
        self.assertEqual(_issue_origin('needed by scene_ir (it cannot change rule_ir; add it here): g'),
                         'downstream_requirement')
        self.assertEqual(_issue_origin('reviewer: wrong win length'), 'critic')


if __name__ == '__main__':
    unittest.main()
