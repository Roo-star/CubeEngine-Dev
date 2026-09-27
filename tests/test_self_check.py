"""Worker self-check tools: free local checks before a patch is submitted."""
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.llm_compiler_v1.self_check import check_entry, check_expression

RULE = json.loads((Path(__file__).resolve().parents[1] /
                   'tests/fixtures/field_runs_20260925/tictactoe_agentic_source/rule.json').read_text(encoding='utf-8'))


class SelfCheckTests(unittest.TestCase):
    def test_expression_types_follow_the_rule_compiler(self):
        ok = check_expression(RULE, {'expr': "grid.equals('rule:state.board_cell', param.target, 0)",
                                     'as': 'condition', 'parameters': {'target': 'core:coord'}})
        self.assertEqual((ok['ok'], ok['type']), (True, 'core:bool'))
        self.assertEqual(ok['lowered']['function'], 'core:grid.equals')
        bad = check_expression(RULE, {'expr': "state.get('rule:state.current_player')", 'as': 'condition'})
        self.assertFalse(bad['ok'])
        self.assertIn('must be core:bool', bad['diagnostics'][0])
        self.assertIn('No runtime implementation for function nosuch',
                      check_expression(RULE, {'expr': 'nosuch(1)'})['diagnostics'][0])

    def test_entry_check_reports_only_that_entry(self):
        base = dict(RULE, actions=[])
        action = deepcopy(RULE['actions'][0])
        self.assertTrue(check_entry('rule_ir', base, {'path': '/actions/-', 'value': action})['ok'])
        action['precondition'] = {'expr': '1 + 2'}
        result = check_entry('rule_ir', base, {'path': '/actions/-', 'value': action})
        self.assertEqual(result['diagnostics'], ['precondition must type-check as core:bool; it is core:int'])
        broken = deepcopy(RULE['actions'][0])
        broken['effects'].append({'op': 'state.set', 'value': {'op': 'literal', 'value': 1}})
        result = check_entry('rule_ir', base, {'path': '/actions/-', 'value': broken})
        self.assertTrue(result['diagnostics'][0].startswith('/actions/0/effects/'), result)
        self.assertIn("path '/<array>/-'", check_entry('rule_ir', base, {'path': '/actions/0', 'value': action})['diagnostics'][0])

    def test_agentic_worker_checks_before_submitting(self):
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        from tests.test_llm_agentic_compiler import (FILE, TICTACTOE, _PASS, _ScriptedChat, _asset_patch, _input_patch,
                                                     _rule_patch, _scene_patch, _spec)
        tools = {'tool_requests': [
            {'tool': 'check_expression', 'expr': "grid.equals('rule:state.board', param.coordinate, 0)",
             'as': 'condition', 'parameters': {'coordinate': 'core:coord'}},
            {'tool': 'check_entry', 'path': '/outcomes/-', 'value': {
                'id': 'rule:outcome.x', 'name': 'X', 'priority': 1, 'condition': {'expr': '3'},
                'result': {'status': 'draw', 'terminal': True, 'winners': [], 'losers': []}}}]}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            shutil.copy(TICTACTOE, source / FILE)
            chat = _ScriptedChat([{'action': 'finish', 'spec': _spec()}, tools, _rule_patch(),
                                  _asset_patch(), _scene_patch(), _input_patch(), _PASS])
            compiler = AgenticSourceToIRCompiler(chat_fn=chat)
            report = compiler.compile_path(source, out_dir=Path(tmp) / 'out', title='Tic Tac Toe 2D')
        self.assertTrue(report.ok, report.diagnostics)
        answer = json.loads(chat.requests[2][-1]['content'])['tool_result']
        self.assertEqual([r['tool'] for r in answer['tool_results']], ['check_expression', 'check_entry'])
        self.assertEqual(answer['tool_results'][1]['diagnostics'],
                         ['condition must type-check as core:bool; it is core:int'])
        steps = compiler.last_job.trace.steps
        self.assertTrue(any(step['kind'] == 'self_check' for step in steps))
        validations = [step for step in steps if step['role'] == 'rule_ir' and step['kind'] == 'validate']
        self.assertEqual([(step['attempt'], step['ok']) for step in validations], [(0, True)],
                         'the tool round did not use a repair attempt')


if __name__ == '__main__':
    unittest.main()
