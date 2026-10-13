"""AlphaZero adapter 1.1: manifests derived from Rules as authored, verified on the Rule Runtime.

The tictactoe Rules are the unmodified outputs of the 2026-09-27 paid
conversion (tests/fixtures/ai_adapter). Renamed and mutated variants are
authored here as engine tests only.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from srtp.alphazero_v1 import (AlphaZeroAdapterError, assess_alphazero_conformance, compile_alphazero_game,
                               derive_alphazero_adapter)
from srtp.ir_v2.rule_ir import seal_rule_ir

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/ai_adapter'
FAST = dict(rollouts=16, transition_checks=16)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def walk(value, visit):
    if isinstance(value, dict):
        visit(value)
        for item in value.values():
            walk(item, visit)
    elif isinstance(value, list):
        for item in value:
            walk(item, visit)


class PaidTicTacToeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule = load('tictactoe3d_target_paid_20260927/ir/game.rule-ir.json')
        cls.derivation = derive_alphazero_adapter(cls.rule, **FAST)
        cls.game = compile_alphazero_game(cls.rule, cls.derivation.manifest) if cls.derivation.eligible else None

    def test_the_converted_3d_rule_is_eligible_as_authored(self):
        self.assertTrue(self.derivation.eligible, [r.message for r in self.derivation.reasons])
        manifest = self.derivation.manifest
        self.assertEqual(manifest['players']['positive'], 'rule:participant.x', 'the first mover is player 1')
        self.assertEqual(manifest['players']['turn'], 'actor')
        owners = {item['owner']: item['rule_value'] for item in manifest['tensor']['value_map']}
        self.assertEqual((owners['positive'], owners['negative']), (1, 2))
        swaps = {item['state']: item['swap'] for item in manifest['tensor']['globals']}
        self.assertTrue(swaps['rule:state.current_player'] and swaps['rule:state.winner'])
        self.assertEqual(swaps['rule:state.draw'], [])
        self.assertEqual(len(manifest['symmetries']), 48)
        self.assertTrue(assess_alphazero_conformance(self.game).passed)

    def test_the_network_sees_a_player_relative_grid(self):
        game = self.game
        self.assertEqual(game.getBoardSize(), (3, 3, 3))
        board, player = game.getNextState(game.getInitBoard(), 1, 0)
        self.assertEqual(player, -1)
        seen_by_o = game.observation(game.getCanonicalForm(board, player))
        self.assertEqual(int(seen_by_o.reshape(-1)[0]), -1, "X's piece is the opponent's for O")
        self.assertEqual(int(np.count_nonzero(seen_by_o)), 1)

    def test_a_board_is_exactly_the_runtime_state(self):
        game = self.game
        board, player = game.getInitBoard(), 1
        for _ in range(4):
            action = int(np.flatnonzero(game.getValidMoves(board, player))[-1])
            board, player = game.getNextState(board, player, action)
        runtime = game.runtime_for(board, player)
        self.assertTrue(np.array_equal(game.board_for(runtime), board))
        occupied = next(i for i, v in enumerate(game.getValidMoves(board, player)) if v == 0)
        with self.assertRaises(AlphaZeroAdapterError):
            game.getNextState(board, player, occupied)

    def test_the_manifest_is_pinned_to_the_rule(self):
        changed = deepcopy(self.rule)
        changed['metadata']['title'] = 'another game'
        with self.assertRaises(AlphaZeroAdapterError):
            compile_alphazero_game(seal_rule_ir(changed), self.derivation.manifest)

    def test_the_2d_source_rule_is_eligible_with_eight_symmetries(self):
        derivation = derive_alphazero_adapter(load('tictactoe_source_paid_20260927.rule-ir.json'), **FAST)
        self.assertTrue(derivation.eligible, [r.message for r in derivation.reasons])
        self.assertEqual(len(derivation.manifest['symmetries']), 8)
        self.assertEqual(derivation.facts['grid_shape'], [3, 3])


class NeutralAndNegativeTests(unittest.TestCase):
    def setUp(self):
        self.rule = load('tictactoe3d_target_paid_20260927/ir/game.rule-ir.json')

    def test_renamed_participants_states_and_parameters(self):
        text = json.dumps(self.rule)
        for old, new in (('rule:participant.x', 'rule:participant.red'), ('rule:participant.o', 'rule:participant.blue'),
                         ('rule:state.board', 'rule:state.cells'), ('rule:state.current_player', 'rule:state.turn_mark')):
            text = text.replace(old, new)
        rule = json.loads(text)
        rule['participants'].reverse()  # list order must not decide who is player 1

        def rename(node):
            if node.get('op') == 'param' and node.get('name') == 'coordinate':
                node['name'] = 'cell'
        for action in rule['actions']:
            for parameter in action['parameters']:
                if parameter['name'] == 'coordinate':
                    parameter['name'] = 'cell'
            for key in ('precondition', 'effects', 'actor'):
                walk(action.get(key), rename)
            action['encoding'] = dict(action['encoding'], parameters=['cell']) if 'parameters' in action['encoding'] \
                else action['encoding']
        derivation = derive_alphazero_adapter(seal_rule_ir(rule), **FAST)
        self.assertTrue(derivation.eligible, [r.message for r in derivation.reasons])
        self.assertEqual(derivation.manifest['players']['positive'], 'rule:participant.red')
        self.assertEqual(set(derivation.manifest['actions']['coordinate_parameters'].values()), {'cell'})
        self.assertEqual(len(derivation.manifest['symmetries']), 48)

    def test_role_specific_state_fails_canonical_verification(self):
        rule = deepcopy(self.rule)
        rule['state']['variables'].append({'id': 'rule:state.first_player_moves', 'name': 'Moves by the first player',
                                           'type': 'core:int', 'scope': 'global',
                                           'initial': {'op': 'literal', 'value': 0}})
        current = {'op': 'call', 'function': 'core:state.get',
                   'args': [{'op': 'literal', 'value': 'rule:state.current_player'}]}
        rule['actions'][0]['effects'].insert(0, {
            'op': 'state.increment', 'target': {'op': 'literal', 'value': 'rule:state.first_player_moves'},
            'value': {'op': 'if', 'condition': {'op': 'eq', 'args': [current, {'op': 'literal', 'value': 1}]},
                      'then': {'op': 'literal', 'value': 1}, 'else': {'op': 'literal', 'value': 0}}})
        derivation = derive_alphazero_adapter(seal_rule_ir(rule), **FAST)
        self.assertFalse(derivation.eligible)
        self.assertEqual(derivation.reasons[0].code, 'eligibility.canonical')

    def test_hidden_information_and_single_player_chance_games_are_refused_with_reasons(self):
        hidden = deepcopy(self.rule)
        hidden['state']['information_model'] = 'hidden'
        codes = [r.code for r in derive_alphazero_adapter(seal_rule_ir(hidden), **FAST).reasons]
        self.assertEqual(codes, ['eligibility.information'])
        game_2048 = json.loads((ROOT / 'tests/fixtures/field_runs_20260930/2048_source_input_failure.json')
                               .read_text(encoding='utf-8'))['rule_ir']
        derivation = derive_alphazero_adapter(game_2048, **FAST)
        codes = [r.code for r in derivation.reasons]
        self.assertFalse(derivation.eligible)
        self.assertEqual(codes[:2], ['eligibility.players', 'eligibility.random'])
        self.assertEqual(len(codes), len(set(codes)), 'one reason per kind, paths merged')


class ReferenceFixtureTests(unittest.TestCase):
    def test_derivation_reproduces_the_hand_written_manifest(self):
        from srtp.integration_gate_v1.reference_fixture import build_reference_gate
        fixture = build_reference_gate()
        derivation = derive_alphazero_adapter(fixture.target.rule, **FAST)
        self.assertTrue(derivation.eligible, [r.message for r in derivation.reasons])
        hand = fixture.ai_manifest
        self.assertEqual({k: derivation.manifest['players'][k] for k in ('positive', 'negative')},
                         {k: hand['players'][k] for k in ('positive', 'negative')})
        as_set = lambda items: sorted((json.dumps(i['rule_value']), i['tensor_value'], i['owner']) for i in items)
        self.assertEqual(as_set(derivation.manifest['tensor']['value_map']), as_set(hand['tensor']['value_map']))
        self.assertEqual(derivation.manifest['players']['turn'], 'flow')
        # The hand manifest lists the 24 rotations; reflections are verified symmetries too.
        self.assertEqual(len(derivation.manifest['symmetries']), 48)


if __name__ == '__main__':
    unittest.main()
