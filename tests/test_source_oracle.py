"""Differential oracle: a compiled Rule replayed against the original pygame game."""
import importlib.util
import json
import re
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import seal_rule_ir
from srtp.llm_compiler_v1.source_oracle import oracle_diagnostics, run_source_oracle

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source'
SOURCE = ROOT / 'srtp/reference_games/pygame_tictactoe/main.py'
HAS_PYGAME = importlib.util.find_spec('pygame') is not None


def _documents():
    return {'rule_ir': json.loads((FIXTURE / 'rule.json').read_text(encoding='utf-8')),
            'input_ir': json.loads((FIXTURE / 'input.json').read_text(encoding='utf-8'))}


def _package():
    from srtp.source_importer import SourceGameImporter
    return SourceGameImporter().import_path(SOURCE)


def _mutated(change):
    documents = _documents()
    rule = deepcopy(documents['rule_ir'])
    change(rule)
    documents['rule_ir'] = seal_rule_ir(rule, revision=int(rule.get('revision') or 0))
    return documents


def _place_anywhere(rule):
    place = next(a for a in rule['actions'] if a['id'] == 'rule:action.place')
    place['precondition'] = place['precondition']['args'][0]  # keep "running", drop "cell is empty"


def _line_of_four(rule):
    # Every use (outcomes and the place precondition): the Rule keeps accepting
    # moves after the source has ended the game with three in a row.
    three = '"core:grid.has_line", "args": [{"op": "literal", "value": "rule:state.board_cell"}, {"op": "literal", "value": 3}]'
    text = json.dumps(rule)
    assert three in text
    rule.update(json.loads(text.replace(three, three.replace('"value": 3}', '"value": 4}'))))


@unittest.skipUnless(HAS_PYGAME, 'pygame is needed to run the original game')
class SourceOracleTests(unittest.TestCase):
    def test_paid_tictactoe_rule_matches_the_original_game(self):
        report = run_source_oracle(_package(), _documents(), games=2, max_steps=12)
        self.assertEqual(report['status'], 'passed', report)
        self.assertEqual(report['grid'], 'rule:state.board_cell')
        self.assertTrue(report['source_board'].endswith('.board'), report['source_board'])
        self.assertGreater(report['checked_steps'], 8)
        json.dumps(report)  # traces keep the report
        self.assertEqual(oracle_diagnostics(report), [])

    def test_overwriting_a_taken_cell_is_a_concrete_counterexample(self):
        report = run_source_oracle(_package(), _mutated(_place_anywhere), games=2, max_steps=12)
        self.assertEqual(report['status'], 'diverged', report)
        example = report['counterexample']
        self.assertTrue(example['actions'][-1]['action'].endswith('place'))
        self.assertRegex(example['differences'][0], r'^cell \[\d, \d\]: source \d+, Rule \d+$')
        diagnostic = oracle_diagnostics(report)[0]
        self.assertIn('the original game and the Rule IR disagree', diagnostic)
        self.assertIn('Replay:', diagnostic)

    def test_wrong_line_length_diverges_after_a_source_win(self):
        report = run_source_oracle(_package(), _mutated(_line_of_four))
        self.assertEqual(report['status'], 'diverged', report)
        self.assertGreater(report['checked_steps'], 3)


def _renamed_source(directory):
    """Same game, other names, and the loop draws before it handles input."""
    text = SOURCE.read_text(encoding='utf-8')
    for old, new in (('class TicTacToe', 'class Noughts'), ('TicTacToe()', 'Noughts()'), ('self.board', 'self.cells'),
                     ('game.board[y][x]', 'session.cells[row][col]'), ('game = ', 'session = '),
                     ('for y in range(3):\n            for x in range(3):', 'for row in range(3):\n            for col in range(3):'),
                     ('+x*CELL_SIZE', '+col*CELL_SIZE'), ('+y*CELL_SIZE', '+row*CELL_SIZE')):
        assert old in text, old
        text = text.replace(old, new)
    text = re.sub(r'\bgame\.', 'session.', text)
    handle_start = text.index('        for event in pygame.event.get():')
    draw_start = text.index('        screen.fill(')
    draw_end = text.index('        clock.tick(60)')
    text = text[:handle_start] + text[draw_start:draw_end] + text[handle_start:draw_start] + text[draw_end:]
    path = Path(directory) / 'noughts.py'
    path.write_text(text, encoding='utf-8')
    from srtp.source_importer import SourceGameImporter
    return SourceGameImporter().import_path(path)


@unittest.skipUnless(HAS_PYGAME, 'pygame is needed to run the original game')
class RenamedSourceOracleTests(unittest.TestCase):
    def test_renamed_draw_first_source_is_aligned_by_search(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            report = run_source_oracle(_renamed_source(tmp), _documents(), games=2, max_steps=12)
            self.assertEqual(report['status'], 'passed', report)
            self.assertTrue(report['source_board'].endswith('.cells'), report['source_board'])
            mutated = run_source_oracle(_renamed_source(tmp), _mutated(_place_anywhere), games=2, max_steps=12)
            self.assertEqual(mutated['status'], 'diverged', mutated)


class SourceOracleScopeTests(unittest.TestCase):
    """What the oracle refuses to judge (no process is started)."""

    def _package(self, framework='pygame', entry='main.py'):
        runtime = type('Runtime', (), {'framework': framework})()
        return type('Package', (), {'runtime': runtime, 'entrypoint': entry, 'root': ROOT, 'files': []})()

    def test_random_timed_and_non_pygame_sources_are_unsupported(self):
        documents = _documents()
        self.assertIn('pygame', run_source_oracle(self._package('tkinter'), documents)['reason'])
        random_rule = dict(documents['rule_ir'], random_streams=[{'id': 'rule:random.spawn'}])
        self.assertIn('random', run_source_oracle(self._package(), dict(documents, rule_ir=random_rule))['reason'])
        timed = dict(documents['rule_ir'], systems=[{'id': 'rule:system.step', 'trigger': {'kind': 'tick'}}])
        self.assertIn('time-driven', run_source_oracle(self._package(), dict(documents, rule_ir=timed))['reason'])
        unbound = dict(documents, input_ir=dict(documents['input_ir'], bindings=[]))
        self.assertEqual(run_source_oracle(self._package(), unbound)['status'], 'unsupported')

    def test_only_diverged_reports_become_diagnostics(self):
        for status in ('passed', 'unsupported', 'inconclusive', 'error'):
            self.assertEqual(oracle_diagnostics({'status': status}), [])


class AgenticOracleRoutingTests(unittest.TestCase):
    """A divergence is a Rule repair input during review, not a new model call of its own."""

    def test_counterexample_reaches_the_rule_worker(self):
        import shutil
        import tempfile
        from unittest import mock
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        from tests.test_llm_agentic_compiler import (FILE, TICTACTOE, _PASS, _ScriptedChat, _asset_patch, _input_patch,
                                                     _rule_patch, _scene_patch, _spec)
        diverged = {'status': 'diverged', 'checked_steps': 3, 'counterexample': {
            'step_description': 'step 3 (rule:action.place {"coordinate": [0, 0]})',
            'actions': [{'action': 'rule:action.place', 'parameters': {'coordinate': [0, 0]}}],
            'differences': ['cell [0, 0]: source 1, Rule 2']}}
        calls = []

        def oracle(package, documents):
            calls.append(documents['rule_ir']['participants'][0]['name'])
            return diverged if len(calls) == 1 else {'status': 'passed', 'checked_steps': 9}

        repaired = _rule_patch()
        next(op for op in repaired['operations'] if op['path'] == '/participants')['value'][0]['name'] = 'First Player'
        with tempfile.TemporaryDirectory() as tmp, mock.patch('srtp.llm_compiler_v1.agentic.run_source_oracle', oracle):
            source = Path(tmp) / 'source'
            source.mkdir()
            shutil.copy(TICTACTOE, source / FILE)
            chat = _ScriptedChat([{'action': 'finish', 'spec': _spec()}, _rule_patch(), _asset_patch(), _scene_patch(),
                                  _input_patch(), _PASS, repaired, _PASS])
            compiler = AgenticSourceToIRCompiler(chat_fn=chat, use_source_oracle=True)
            report = compiler.compile_path(source, out_dir=Path(tmp) / 'out', title='Tic Tac Toe 2D')
            self.assertTrue(any((Path(tmp) / 'out').rglob('source_oracle.json')))
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(calls, ['Player 1', 'First Player'])
        repair = json.loads(chat.requests[6][-1]['content'])
        self.assertIn('cell [0, 0]: source 1, Rule 2', json.dumps(repair))
        steps = [step for step in compiler.last_job.trace.steps if step['kind'] == 'source_oracle']
        self.assertEqual([step['status'] for step in steps], ['diverged', 'passed'])
        self.assertFalse([item for item in report.diagnostics if 'source oracle' in item])

    def test_disabled_oracle_never_runs(self):
        from unittest import mock
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        with mock.patch.dict('os.environ', {'CUBEENGINE_SOURCE_ORACLE': '0'}):
            self.assertFalse(AgenticSourceToIRCompiler(chat_fn=lambda *a, **k: None).use_source_oracle)
        self.assertTrue(AgenticSourceToIRCompiler(chat_fn=lambda *a, **k: None, use_source_oracle=True).use_source_oracle)



@unittest.skipUnless(HAS_PYGAME, 'pygame is needed to run the original game')
class StagedOracleTests(unittest.TestCase):
    """Workbench (staged) Source compile: one oracle round, paid at most once per Rule."""

    def setUp(self):
        import tempfile
        from srtp.source_importer import SourceGameImporter
        from tests.test_llm_budget import fontless_tictactoe
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.package = SourceGameImporter().import_path(fontless_tictactoe(self.tmp))
        self.requests = []

    def tearDown(self):
        self._tmp.cleanup()

    def _chat(self, rules):
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            self.requests.append(payload)
            definition = reference_definition(payload['stage'])
            tests = tests_for_board() if payload['stage'] == 'rule_ir' else []
            if payload['stage'] == 'rule_ir':
                own = rules.pop(0)(definition)
                tests = tests if own is None else own
            return json.dumps({'definition': definition,
                               'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests': tests})
        return chat

    def _compile(self, rules, out='out', max_repairs=0):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        compiler = SourceToIRCompiler(chat_fn=self._chat(rules), max_repairs=max_repairs)
        compiler.use_source_oracle = True
        return compiler.compile(self.package, out_dir=self.tmp / out)

    @staticmethod
    def _overwrite(definition):
        # A model that misreads the source also writes tests for its misreading:
        # every gate passes, only the original game disagrees.
        next(a for a in definition['actions'] if a['id'] == 'rule:action.place')['precondition'] = {
            'op': 'literal', 'value': True}
        moves = [[0, 0], [0, 1], [1, 0], [1, 1], [2, 0]]  # the first player completes a row
        return [{'name': 'a row wins', 'expect': {'terminal': True},
                 'steps': [{'action': 'rule:action.place', 'parameters': {'target': move}, 'accepted': True}
                           for move in moves]}]

    def test_matching_rule_is_recorded_without_extra_calls(self):
        report = self._compile([lambda d: None])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual([r['stage'] for r in self.requests], ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir'])
        oracle = report.compilation_trace['source_oracle']
        self.assertEqual(oracle['status'], 'passed')
        check = next(c for c in report.compilation_trace['quality_assessment']['checks']
                     if c['id'] == 'source_behavior_equivalence')
        self.assertEqual(check['status'], 'pending', 'board agreement alone is not a product pass')
        self.assertEqual(check['evidence']['source_oracle']['status'], 'passed')

    def test_divergence_reopens_only_the_rule_stage_once(self):
        report = self._compile([self._overwrite, lambda d: None])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual([r['stage'] for r in self.requests], ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir', 'rule_ir'],
                         'Asset/Scene/Input are re-checked from their accepted replies, not regenerated')
        reopen = self.requests[-1]
        self.assertIn('source oracle', reopen['repair_diagnostics'][0])
        self.assertEqual(reopen['previous_definition']['definition']['actions'][0]['precondition'], {'op': 'literal', 'value': True})
        oracle = report.compilation_trace['source_oracle']
        self.assertEqual((oracle['status'], oracle['reopened']['kept']), ('passed', True))
        place = next(a for a in report.documents['rule_ir']['actions'] if a['id'] == 'rule:action.place')
        self.assertEqual(place['precondition']['function'], 'core:grid.equals')

    def test_unhelpful_round_keeps_the_earlier_pass_and_is_not_paid_again(self):
        report = self._compile([self._overwrite, self._overwrite])
        self.assertTrue(report.ok, 'the oracle never turns a passing compile into a failure')
        self.assertEqual(len(self.requests), 5)
        oracle = report.compilation_trace['source_oracle']
        self.assertEqual((oracle['status'], oracle['reopened']['kept']), ('diverged', False))
        check = next(c for c in report.compilation_trace['quality_assessment']['checks']
                     if c['id'] == 'source_behavior_equivalence')
        self.assertEqual(check['status'], 'fail')
        again = self._compile([])
        self.assertTrue(again.ok, again.diagnostics)
        self.assertEqual(len(self.requests), 5, 'the rerun reuses every accepted stage and the recorded oracle round')
        self.assertTrue(again.compilation_trace['source_oracle']['reopened']['reused'])
        stored = json.loads((self.tmp / 'out.stages.json').read_text(encoding='utf-8'))
        (round_,) = stored['source_oracle_rounds'].values()
        self.assertEqual(round_['rule_reply']['definition']['actions'][0]['precondition'],
                         {'op': 'literal', 'value': True}, 'the paid reopen reply is kept for replay')


    def test_oracle_round_is_bounded_and_failed_round_restores_the_pass(self):
        def broken(definition):
            definition['actions'][0]['effects'] = 'not a list'
        report = self._compile([self._overwrite, broken, broken, broken, broken], max_repairs=3)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual([r['stage'] for r in self.requests],
                         ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir', 'rule_ir', 'rule_ir'],
                         'at most two Rule calls in the oracle round, whatever max_repairs is')
        reopened = report.compilation_trace['source_oracle']['reopened']
        self.assertEqual((reopened['kept'], reopened['failed_stage']), (False, 'rule_ir'))
        place = next(a for a in report.documents['rule_ir']['actions'] if a['id'] == 'rule:action.place')
        self.assertEqual(place['precondition'], {'op': 'literal', 'value': True}, 'the earlier accepted Rule is kept')


if __name__ == '__main__':
    unittest.main()
