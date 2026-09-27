"""Per-stage cost allowances, no-progress retry stops and frozen-stage reuse."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from srtp.llm_compiler_v1.budget import CostBudget, STAGED_SOURCE_WEIGHTS, budget_stage, made_progress
from srtp.llm_compiler_v1.client import LLMCostBudgetExceeded, LLMTransportError

ROOT = Path(__file__).resolve().parents[1]


def fontless_tictactoe(directory):
    """Renamed, text-free Tic Tac Toe (this environment has no pygame default font)."""
    text = (ROOT / 'srtp/reference_games/pygame_tictactoe/main.py').read_text(encoding='utf-8')
    game = Path(directory) / 'noughts'
    game.mkdir()
    (game / 'noughts.py').write_text('\n'.join(line for line in text.splitlines()
                                               if 'font' not in line and 'blit' not in line), encoding='utf-8')
    return game / 'noughts.py'


class CostBudgetTests(unittest.TestCase):
    def test_shares_flow_forward_and_repairs_use_the_reserve(self):
        budget = CostBudget(1.0, reserve=0.25)
        budget.plan(list(STAGED_SOURCE_WEIGHTS), STAGED_SOURCE_WEIGHTS)
        budget.begin('rule_ir')
        self.assertAlmostEqual(budget.allowance['rule_ir'], 0.75 * 0.35)
        budget.before_request(1000)  # nothing priced yet: allowed
        budget.record(0.1, input_tokens=900, output_tokens=100)
        budget.before_request(900)
        budget.record(0.1, input_tokens=900, output_tokens=100)
        with self.assertRaisesRegex(LLMCostBudgetExceeded, 'rule_ir has US\\$0.0625 left'):
            budget.before_request(900)
        budget.begin('rule_ir', repair=True)
        budget.before_request(900)  # the repair may draw on the reserve
        budget.begin('asset_ir')
        self.assertAlmostEqual(budget.allowance['asset_ir'], (0.75 - 0.2) * 0.1 / 0.65)
        budget.before_request(50)  # small request: priced per token, not by the largest request
        with self.assertRaises(LLMCostBudgetExceeded):
            budget.before_request(2000)
        self.assertEqual(len(budget.stops), 2)

    def test_no_cap_enforces_nothing_and_unstaged_calls_use_the_job_cap(self):
        budget = CostBudget()
        budget.record(5.0)
        budget.before_request()
        capped = CostBudget(0.1)
        capped.record(0.06)
        with self.assertRaises(LLMCostBudgetExceeded):
            capped.before_request()

    def test_budget_stage_is_scoped(self):
        client = SimpleNamespace(budget=CostBudget(1.0))
        with budget_stage(client, 'rule_ir', repair=True):
            self.assertEqual((client.budget.stage, client.budget.repair), ('rule_ir', True))
        self.assertIsNone(client.budget.stage)

    def test_progress_ignores_indexes_but_not_new_or_fixed_errors(self):
        first = ['rule_ir invalid at /actions/0/effects/1: state.set requires target']
        self.assertFalse(made_progress(first, ['rule_ir invalid at /actions/2/effects/4: state.set requires target']))
        self.assertFalse(made_progress(first, first + ['another problem']))
        self.assertTrue(made_progress(first + ['another problem'], ['another problem']))
        self.assertTrue(made_progress([], first))


class StagedBudgetTests(unittest.TestCase):
    def setUp(self):
        from srtp.source_importer import SourceGameImporter
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.package = SourceGameImporter().import_path(fontless_tictactoe(self.tmp))
        self.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def chat(self, messages, **kwargs):
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        payload = json.loads(messages[-1]['content'])
        self.calls.append(payload['stage'])
        content = json.dumps({'definition': reference_definition(payload['stage']),
                              'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                              'behavior_tests': tests_for_board() if payload['stage'] == 'rule_ir' else []})
        tokens = len(json.dumps(messages)) // 4
        return {'content': content, 'usage': {'cost': tokens * 1e-6, 'input_tokens': tokens, 'output_tokens': 500}}

    def test_stage_stops_before_exceeding_its_share_and_rerun_keeps_accepted_stages(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        out = self.tmp / 'source'
        with mock.patch.dict(os.environ, {'CUBEENGINE_LLM_MAX_COST_USD': '0.02', 'CUBEENGINE_LLM_REPAIR_RESERVE': '0.25'}):
            report = SourceToIRCompiler(chat_fn=self.chat, max_repairs=0).compile(self.package, out_dir=out)
        self.assertFalse(report.ok)
        self.assertEqual(self.calls, ['rule_ir'])
        self.assertTrue(report.diagnostics[0].startswith('asset_ir: Stage budget: asset_ir'), report.diagnostics)
        self.assertIn('budget', report.compilation_trace['api_usage'])
        rerun = SourceToIRCompiler(chat_fn=self.chat, max_repairs=0).compile(self.package, out_dir=out)
        self.assertTrue(rerun.ok, rerun.diagnostics)
        self.assertEqual(self.calls, ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir'],
                         'the accepted Rule stage is reused, not regenerated')

    def test_repair_that_fixes_nothing_stops_early(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        replies = []

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            replies.append(payload['stage'])
            broken = {'types': [], 'actions': [{'id': 'rule:action.place'}]}
            if len(replies) > 1:
                broken['state'] = {'variables': 'not-a-list'}  # the old error stays and a new one appears
            return json.dumps({'definition': broken, 'evidence': [], 'behavior_tests': []})

        report = SourceToIRCompiler(chat_fn=chat, max_repairs=3).compile(self.package, out_dir=self.tmp / 'broken')
        self.assertFalse(report.ok)
        self.assertEqual(replies, ['rule_ir', 'rule_ir'], 'stopped after the first repair, not after four attempts')
        self.assertIn('fixed none of the previous diagnostics', report.compilation_trace['stages'][-1]['stopped_reason'])


class AgenticRetryTests(unittest.TestCase):
    def setUp(self):
        from tests.test_llm_agentic_compiler import FILE, TICTACTOE
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_dir = self.tmp / 'source'
        self.source_dir.mkdir()
        shutil.copy(TICTACTOE, self.source_dir / FILE)

    def tearDown(self):
        self._tmp.cleanup()

    def compile(self, payloads, **options):
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        from tests.test_llm_agentic_compiler import _ScriptedChat
        chat = _ScriptedChat(payloads)
        compiler = AgenticSourceToIRCompiler(chat_fn=chat, **options)
        report = compiler.compile_path(self.source_dir, out_dir=self.tmp / 'out', title='Tic Tac Toe 2D')
        return report, chat, compiler.last_job

    def test_worker_repair_without_progress_stops(self):
        from tests.test_llm_agentic_compiler import _spec
        bad = {'operations': [{'op': 'replace', 'path': '/flow', 'value': {'model': 'nonsense'}}],
               'evidence': ['ev:agent.1'], 'assumptions': [], 'unresolved': []}
        report, chat, job = self.compile([{'action': 'finish', 'spec': _spec()}, bad, bad, bad, bad])
        self.assertFalse(report.ok)
        self.assertEqual(len(chat.requests), 3, 'analyst + draft + one repair')
        self.assertEqual(len(chat.payloads), 2)
        self.assertTrue(any(step['kind'] == 'stopped_no_progress' for step in job.trace.steps))

    def test_rerun_resumes_accepted_stages_by_default(self):
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        from tests.test_llm_agentic_compiler import (_PASS, _ScriptedChat, _asset_patch, _input_patch, _rule_patch,
                                                     _scene_patch, _spec)
        first, _, _ = self.compile([{'action': 'finish', 'spec': _spec()}, _rule_patch(), _asset_patch(),
                                    LLMTransportError('provider unavailable')])
        self.assertEqual(first.stage, 'agent_transport')
        chat = _ScriptedChat([_scene_patch(), _input_patch(), _PASS])
        report = AgenticSourceToIRCompiler(chat_fn=chat).compile_path(self.source_dir, out_dir=self.tmp / 'out',
                                                                       title='Tic Tac Toe 2D')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(report.attempts, 3, 'spec, Rule and Asset were not paid for again')
        fresh = _ScriptedChat([{'action': 'finish', 'spec': _spec()}, _rule_patch(), _asset_patch(), _scene_patch(),
                               _input_patch(), _PASS])
        again = AgenticSourceToIRCompiler(chat_fn=fresh).compile_path(self.source_dir, out_dir=self.tmp / 'out',
                                                                       title='Tic Tac Toe 2D', resume=False)
        self.assertTrue(again.ok, again.diagnostics)
        self.assertFalse(fresh.payloads, '--fresh regenerates every stage')

    def test_agentic_cost_budget_stops_as_agent_budget(self):
        from tests.test_llm_agentic_compiler import _rule_patch, _spec
        replies = [{'action': 'finish', 'spec': _spec()}, _rule_patch()]
        calls = []

        def chat(**kwargs):
            calls.append(kwargs['messages'])
            tokens = len(json.dumps(kwargs['messages'])) // 4
            return {'content': json.dumps(replies[len(calls) - 1]),
                    'usage': {'cost': tokens * 1e-5, 'input_tokens': tokens, 'output_tokens': 400}}

        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        with mock.patch.dict(os.environ, {'CUBEENGINE_LLM_MAX_COST_USD': '0.05'}):
            report = AgenticSourceToIRCompiler(chat_fn=chat).compile_path(self.source_dir, out_dir=self.tmp / 'out',
                                                                           title='Tic Tac Toe 2D')
        self.assertEqual(report.stage, 'agent_budget')
        self.assertTrue(report.diagnostics[0].startswith('Stage budget: rule_ir has'), report.diagnostics)
        self.assertEqual(len(calls), 1, 'the Rule draft was not sent after the analyst used most of the cap')
        state = json.loads((self.tmp / 'out' / 'agent_state.json').read_text(encoding='utf-8'))
        self.assertIsNotNone(state['spec'], 'the accepted Game Spec is kept for the next run')


if __name__ == '__main__':
    unittest.main()
