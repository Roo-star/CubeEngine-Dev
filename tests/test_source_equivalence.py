"""Function-level source equivalence on saved real model Rules; no network.

Saved model outputs are used unchanged. The renamed and biased cases are
authored in memory to prove the check is name-independent and statistical;
they are engine tests, never live-model acceptance.
"""
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import seal_rule_ir
from srtp.llm_compiler_v1.source_equivalence import _compare, check_source_equivalence, equivalence_diagnostics
from tests.test_current_conversion_failures import actual_2048

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'srtp/reference_games/pygame_2048'
FIELD_0930 = ROOT / 'tests/fixtures/field_runs_20260930/2048_source_input_failure.json'
FAST = dict(walks=4, steps=30, rule_seeds=4, source_seeds=60, samples_per_action=10)
DIRECTIONS = (('left', 'a'), ('right', 'd'), ('up', 'w'), ('down', 's'))


def moves(directions=DIRECTIONS, *, actions='rule:action.', grid='rule:state.board', file='logic.py',
          move='move', fill='fillTwoOrFour'):
    return [{'action': actions + name, 'grid': grid,
             'function': {'file': file, 'name': move, 'args': [key, '$grid']},
             'random_fill': {'file': file, 'name': fill, 'args': ['$grid'], 'when': 'changed'}}
            for name, key in directions]


def status(*, file='logic.py', name='checkGameStatus', grid='rule:state.board', target='rule:state.target',
           win='rule:outcome.win', loss='rule:outcome.loss'):
    return {'status': {'file': file, 'name': name, 'args': ['$grid', {'state': target}]}, 'grid': grid,
            'cases': {'WIN': {'outcome': win}, 'LOSE': {'outcome': loss}, 'PLAY': {'outcome': None}}}


def rule_0930():
    return json.loads(FIELD_0930.read_text(encoding='utf-8'))['rule_ir']


class SavedModelRuleTests(unittest.TestCase):
    def test_saved_multiplier_four_rule_is_caught_without_model_tests(self):
        rule, _ = actual_2048()
        report = check_source_equivalence(SOURCE, rule, moves(), **FAST)
        self.assertEqual(report['status'], 'diverged', report)
        self.assertRegex(report['counterexample']['message'], r'is 8 after the Rule action, but every run of the original gives 4')
        self.assertTrue(equivalence_diagnostics(report)[0].startswith('source_equivalence: rule:action.'))

    def test_saved_rule_down_merges_differently_from_the_original_code(self):
        report = check_source_equivalence(SOURCE, rule_0930(), moves(), **FAST)
        self.assertEqual(report['status'], 'diverged', report)
        self.assertEqual(report['counterexample']['action'], 'rule:action.down')

    def test_saved_rule_left_right_up_spawns_and_status_match_the_original(self):
        report = check_source_equivalence(SOURCE, rule_0930(), moves(DIRECTIONS[:3]) + [status()], **FAST)
        self.assertEqual(report['status'], 'passed', report)
        self.assertEqual(report['actions'], ['rule:action.left', 'rule:action.right', 'rule:action.up'])
        self.assertEqual(report['mapping'], 'source[i][j] = rule[j][i]')


class NeutralAndStatisticalTests(unittest.TestCase):
    def test_renamed_rule_ids_and_source_functions_give_the_same_verdicts(self):
        text = json.dumps(rule_0930())
        for old, new in (('rule:action.left', 'rule:action.west'), ('rule:action.right', 'rule:action.east'),
                         ('rule:action.up', 'rule:action.north'), ('rule:action.down', 'rule:action.south'),
                         ('rule:state.board', 'rule:state.cells')):
            text = text.replace(old, new)
        renamed = seal_rule_ir(json.loads(text))
        with tempfile.TemporaryDirectory() as folder:
            shutil.copy(SOURCE / 'logic.py', Path(folder) / 'core_logic.py')
            (Path(folder) / 'board_rules.py').write_text(
                'from core_logic import move as shift_tiles, fillTwoOrFour as spawn_tile, '
                'checkGameStatus as status_of\n', encoding='utf-8')
            names = (('west', 'a'), ('east', 'd'), ('north', 'w'), ('south', 's'))
            declared = moves(names, grid='rule:state.cells', file='board_rules.py', move='shift_tiles', fill='spawn_tile')
            final = status(file='board_rules.py', name='status_of', grid='rule:state.cells')
            passed = check_source_equivalence(folder, renamed, declared[:3] + [final], **FAST)
            self.assertEqual(passed['status'], 'passed', passed)
            diverged = check_source_equivalence(folder, renamed, declared, **FAST)
            self.assertEqual(diverged['counterexample']['action'], 'rule:action.south')

    def test_biased_spawn_frequency_is_reported(self):
        rule = rule_0930()
        system = next(s for s in rule['systems'] if s['id'] == 'rule:system.normal_tile')
        items = system['effects'][0]['distribution']['values']['items']
        items[:] = [{'op': 'literal', 'value': 2}] * 9 + [{'op': 'literal', 'value': 4}]
        report = check_source_equivalence(SOURCE, seal_rule_ir(rule), moves(DIRECTIONS[:3]), **FAST)
        self.assertEqual(report['status'], 'diverged', report)
        self.assertRegex(report['counterexample']['message'], r'random placements use value')

    def test_spawn_after_a_move_the_original_ignores_is_a_counterexample(self):
        board = [[2, 0], [0, 0]]
        sample = {'action': 'rule:action.left', 'parameters': {},
                  'after': [{'grid': [[2, 0], [0, 4]]}]}
        identity = {'label': 'identity', 'transpose': False, 'values': {}}
        problem = _compare(sample, identity, [deepcopy(board)], board, __import__('collections').Counter(),
                           __import__('collections').Counter())
        self.assertRegex(problem, r'cell \[1\]\[1\] is 4 after the Rule action, but every run of the original gives 0')


class StagedGateTests(unittest.TestCase):
    def test_counterexample_becomes_an_entry_repair_for_the_failing_action(self):
        from srtp.llm_compiler_v1.entry_repair import failing_entries
        from srtp.llm_compiler_v1.program_builder import DefinitionValidationError
        from srtp.llm_compiler_v1.staged import _source_equivalence_gate
        rule, _ = actual_2048()
        reply = {'definition': {'actions': [{'id': item['id']} for item in rule['actions']]},
                 'source_equivalence': moves()}
        with self.assertRaises(DefinitionValidationError) as caught:
            _source_equivalence_gate(SOURCE, rule, reply)
        diagnostic = caught.exception.diagnostics[0]
        self.assertRegex(diagnostic, r'^/actions/\d+: source_equivalence: rule:action\.')
        entries = failing_entries('rule_ir', reply['definition'], caught.exception.diagnostics)
        self.assertEqual([row['id'] for row in entries], [caught.exception.diagnostics[0].split('source_equivalence: ')[1].split()[0]])

    def test_passing_declarations_are_recorded_in_the_stage_checks(self):
        from srtp.llm_compiler_v1.staged import _source_equivalence_gate
        record = _source_equivalence_gate(SOURCE, rule_0930(), {'definition': {}, 'source_equivalence': moves(DIRECTIONS[:3])})
        self.assertEqual(record['source_equivalence']['status'], 'passed')


class PreviewTests(unittest.TestCase):
    def test_offline_preview_shows_the_next_stage_and_its_contract(self):
        from srtp.llm_compiler_v1.preview import preview
        report = preview(SOURCE / 'main.py')
        self.assertEqual(report['stage'], 'rule_ir')
        text = '\n'.join(item['content'] for item in report['messages'])
        for word in ('source_equivalence', 'rejection_sampling', 'background_click', 'swipe'):
            self.assertIn(word, text)
        self.assertEqual(report['provenance']['checkout'], str(ROOT))


class DeclarationTests(unittest.TestCase):
    def test_declaration_errors_are_model_fixable(self):
        rule = rule_0930()
        for declared, expected in (
                ([dict(moves()[0], action='rule:action.jump')], 'not a Rule action id'),
                ([dict(moves()[0], function={'file': '../outside.py', 'name': 'move', 'args': []})], 'relative to the source root'),
                ([dict(moves()[0], function={'file': 'logic.py', 'name': 'no_such', 'args': ['a', '$grid']})],
                 'could not be called as declared'),
                ([status()], 'at least one action')):
            report = check_source_equivalence(SOURCE, rule, declared, **FAST)
            self.assertEqual(report['status'], 'invalid', report)
            self.assertIn(expected, equivalence_diagnostics(report)[0])

    def test_without_declarations_nothing_is_claimed(self):
        report = check_source_equivalence(SOURCE, rule_0930(), [], **FAST)
        self.assertEqual(report['status'], 'unsupported')
        self.assertEqual(equivalence_diagnostics(report), [])


if __name__ == '__main__':
    unittest.main()
