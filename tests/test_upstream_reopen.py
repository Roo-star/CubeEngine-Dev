"""A later stage can have an earlier IR re-opened instead of failing on a gap it cannot fill."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.llm_compiler_v1.upstream import document_diff, upstream_requests

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / 'tests/fixtures/field_runs_20260927/scene_needs_rule_replies.json'
GAP = {'path': '/nodes/scene:hud.status', 'reason': 'The Scene must show the result but the Rule has no flag for it.',
       'required': True, 'owner': 'rule_ir'}
FLAG = {'id': 'rule:state.result_flag', 'name': 'Result flag', 'type': 'core:bool', 'scope': 'global',
        'initial': {'op': 'literal', 'value': False}}


class DetectionTests(unittest.TestCase):
    def test_paid_scene_replies_that_declared_a_rule_gap(self):
        replies = json.loads(SAVED.read_text(encoding='utf-8'))['scene_replies']
        for reply in replies:
            requests = upstream_requests('scene_ir', reply)
            self.assertEqual({r['ir'] for r in requests}, {'rule_ir'})
            self.assertIn('winner', ' '.join(r['requirement'] for r in requests).lower())

    def test_only_earlier_irs_and_required_items_count(self):
        reply = {'upstream_requests': [{'ir': 'input_ir', 'requirement': 'later IR'},
                                       {'ir': 'asset_ir', 'requirement': 'a font'}],
                 'unresolved': [dict(GAP, required=False), dict(GAP, owner='importer_or_llm'), GAP]}
        requests = upstream_requests('scene_ir', reply)
        self.assertEqual([(r['ir'], r['declared_as']) for r in requests],
                         [('rule_ir', 'unresolved owner rule_ir'), ('asset_ir', 'upstream_requests')])
        self.assertEqual(upstream_requests('rule_ir', {'unresolved': [GAP]}), [], 'nothing is earlier than the Rule')

    def test_diff_names_what_the_reopened_ir_added(self):
        before = {'state': {'variables': [{'id': 'a'}]}, 'actions': [{'id': 'x', 'n': 1}]}
        after = {'state': {'variables': [{'id': 'a'}, {'id': 'b'}]}, 'actions': [{'id': 'x', 'n': 2}]}
        self.assertEqual(document_diff(before, after),
                         {'actions': {'changed': ['x']}, 'state.variables': {'added': ['b']}})


class StagedRoundTests(unittest.TestCase):
    def setUp(self):
        from srtp.source_importer import SourceGameImporter
        from tests.test_llm_budget import fontless_tictactoe
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.package = SourceGameImporter().import_path(fontless_tictactoe(self.tmp))
        self.requests = []

    def tearDown(self):
        self._tmp.cleanup()

    def _compile(self, rule_replies, scene_replies, out='out'):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            self.requests.append(payload)
            stage = payload['stage']
            definition = reference_definition(stage)
            reply = {'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                     'behavior_tests': tests_for_board() if stage == 'rule_ir' else []}
            if stage == 'rule_ir':
                rule_replies.pop(0)(definition)
            if stage == 'scene_ir':
                scene_replies.pop(0)(definition, reply)
            reply['definition'] = definition
            return json.dumps(reply)
        compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=2)
        compiler.use_source_oracle = False
        compiler.scene_draft_mode = 'off'  # the re-open mechanism itself is under test
        return compiler.compile(self.package, out_dir=self.tmp / out)

    @staticmethod
    def keep(definition, reply=None):
        return None

    @staticmethod
    def declare_gap(definition, reply):
        definition['unresolved'] = [GAP]

    @staticmethod
    def add_flag(definition):
        definition['state']['variables'].append(deepcopy(FLAG))

    @staticmethod
    def broken(definition):
        definition['actions'][0]['effects'] = 'not a list'

    def stages(self):
        return [payload['stage'] for payload in self.requests]

    def test_scene_gap_reopens_the_rule_once_and_resumes_with_the_diff(self):
        report = self._compile([self.keep, self.add_flag], [self.declare_gap, self.keep])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(self.stages(), ['rule_ir', 'asset_ir', 'scene_ir', 'rule_ir', 'scene_ir', 'input_ir'],
                         'Asset is re-checked from its accepted reply, not regenerated')
        reopen, resume = self.requests[3], self.requests[4]
        self.assertIn('Stage scene_ir', reopen['repair_diagnostics'][0])
        self.assertIn('no flag for it', reopen['repair_diagnostics'][0])
        self.assertEqual(reopen['previous_definition']['definition']['state']['variables'][0]['id'],
                         'rule:state.board_cell')
        self.assertIn('rule:state.result_flag', resume['repair_diagnostics'][0])
        self.assertEqual(resume['previous_definition']['definition']['unresolved'], [GAP])
        self.assertIn('upstream_requests_instruction', resume)
        rounds = report.compilation_trace['upstream_rounds']
        self.assertEqual((rounds[0]['kept'], rounds[0]['requested_by'], rounds[0]['reopened']),
                         (True, 'scene_ir', 'rule_ir'))
        self.assertEqual(rounds[0]['diff'], {'state.variables': {'added': ['rule:state.result_flag']}})
        self.assertIn('rule:state.result_flag', [v['id'] for v in report.documents['rule_ir']['state']['variables']])

    def test_failed_reopen_restores_the_accepted_rule_and_is_not_paid_again(self):
        report = self._compile([self.keep, self.broken, self.broken], [self.declare_gap])
        self.assertFalse(report.ok)
        self.assertEqual(self.stages(), ['rule_ir', 'asset_ir', 'scene_ir', 'rule_ir', 'rule_ir'],
                         'at most two calls for the re-opened Rule')
        self.assertTrue(report.diagnostics[0].startswith('scene_ir: '), report.diagnostics)
        self.assertTrue(any('re-opened for this requirement' in item for item in report.diagnostics))
        self.assertEqual(report.compilation_trace['upstream_rounds'][0]['kept'], False)
        stored = json.loads((self.tmp / 'out.stages.json').read_text(encoding='utf-8'))
        self.assertIn('rule_ir', stored['stages'], 'the earlier accepted Rule stays in the checkpoint')
        (record,) = stored['upstream_rounds'].values()
        self.assertEqual(record['reply']['definition']['actions'][0]['effects'], 'not a list',
                         'the paid re-open reply is kept for replay')
        self.requests.clear()
        self._compile([], [self.declare_gap, self.declare_gap, self.declare_gap])
        self.assertNotIn('rule_ir', self.stages(), 'the same request does not buy another round')


if __name__ == '__main__':
    unittest.main()
