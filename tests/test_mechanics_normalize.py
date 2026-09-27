"""Engine mechanics library, lossless normalization and session seeds in agentic checks."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import seal_rule_ir, validate_rule_ir
from srtp.ir_v2.runtime import RuleRuntime
from srtp.llm_compiler_v1.mechanics import MechanicError, expand_mechanics, mechanics_operations, merge_mechanics
from srtp.llm_compiler_v1.normalize import normalize, normalize_definition, authoring_schema_for

ROOT = Path(__file__).resolve().parents[1]
TICTACTOE_RULE = ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source/rule.json'
MINESWEEPER = ROOT / 'tests/fixtures/field_runs_20260924/minesweeper_lift.json'


def skeleton(participants, extents=(3, 3), board='rule:state.board_cell'):
    rule = json.loads(TICTACTOE_RULE.read_text(encoding='utf-8'))
    rule['participants'] = [{'id': p, 'name': p.rsplit('.', 1)[-1], 'kind': 'human'} for p in participants]
    rule['topologies'][0]['axes'] = [{'name': n, 'extent': e, 'boundary': 'bounded'} for n, e in zip('xyz', extents)]
    rule['types'][0]['values'] = dict({'empty': 0}, **{p.rsplit('.', 1)[-1]: i + 1 for i, p in enumerate(participants)})
    rule['state']['variables'] = [dict(rule['state']['variables'][0], id=board)]
    rule['state']['entity_types'] = []
    return dict(rule, actions=[], outcomes=[])


def mechanics(participants, length, board='rule:state.board_cell', turn='rule:state.turn'):
    marks = {p: i + 1 for i, p in enumerate(participants)}
    return [{'pattern': 'turn_state', 'state': turn, 'order': participants},
            {'pattern': 'place_on_empty', 'id': 'rule:action.place', 'board': board, 'topology': 'rule:topology.board',
             'marks': marks, 'turn_state': turn, 'order': participants},
            {'pattern': 'line_win', 'id': 'rule:outcome.line', 'board': board, 'length': length, 'marks': marks},
            {'pattern': 'full_board_draw', 'id': 'rule:outcome.full', 'board': board}]


def play(rule, moves):
    runtime = RuleRuntime(rule)
    try:
        outcome = None
        for move in moves:
            action = next(a for a in runtime.legal_actions() if tuple(a.parameters['target']) == tuple(move))
            outcome = runtime.apply_action(action).outcome
        return outcome, runtime.state.globals.get('rule:state.turn')
    finally:
        runtime.close()


class MechanicsTests(unittest.TestCase):
    def build(self, participants, length, extents=(3, 3), **kw):
        base = skeleton(participants, extents, **kw)
        rule = seal_rule_ir(dict(base, **merge_mechanics({}, base, expand_mechanics(mechanics(participants, length, **kw)))))
        self.assertEqual([d.message for d in validate_rule_ir(rule) if d.severity == 'error'], [])
        return rule

    def test_two_player_line_and_draw(self):
        players = ['rule:participant.p1', 'rule:participant.p2']
        rule = self.build(players, 3)
        outcome, _ = play(rule, [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)])
        self.assertEqual((outcome.status, outcome.winners), ('win', ('rule:participant.p1',)))
        outcome, _ = play(rule, [(0, 0), (1, 0), (2, 0), (1, 1), (0, 1), (0, 2), (1, 2), (2, 1), (2, 2)])
        self.assertEqual(outcome.status, 'draw')

    def test_renamed_three_player_board_and_lift_stay_rank_generic(self):
        players = ['rule:participant.red', 'rule:participant.green', 'rule:participant.blue']
        rule = self.build(players, 3, extents=(4, 4), board='rule:state.cells')
        outcome, turn = play(rule, [(0, 0), (3, 3), (1, 1)])
        self.assertEqual((outcome.status, turn), ('ongoing', 'rule:participant.red'), 'turns rotate over three players')
        from srtp.llm_compiler_v1.lift_templates import apply_lift_template, template_from_changes
        template, _ = template_from_changes([{'kind': 'set_extent', 'axis': 'z', 'value': 3}], rule)
        lifted = apply_lift_template(rule, template)
        self.assertTrue(lifted.complete, lifted.residual)
        outcome, _ = play(lifted.rule, [(0, 0, 0), (3, 3, 0), (3, 2, 0), (0, 0, 1), (3, 3, 1), (3, 2, 1), (0, 0, 2)])
        self.assertEqual((outcome.status, outcome.winners), ('win', ('rule:participant.red',)), 'a pillar across layers wins')

    def test_errors_name_the_problem(self):
        players = ['rule:participant.p1', 'rule:participant.p2']
        with self.assertRaisesRegex(MechanicError, 'pattern must be one of'):
            expand_mechanics([{'pattern': 'gravity'}])
        with self.assertRaisesRegex(MechanicError, 'marks has no value'):
            expand_mechanics([dict(mechanics(players, 3)[1], marks={players[0]: 1})])
        with self.assertRaisesRegex(MechanicError, '/mechanics/0/length'):
            expand_mechanics([dict(mechanics(players, 3)[2], length=1)])
        base = skeleton(players)
        base['actions'] = [{'id': 'rule:action.place'}]
        with self.assertRaisesRegex(MechanicError, 'duplicate actions id rule:action.place'):
            merge_mechanics({}, base, expand_mechanics(mechanics(players, 3)))
        ops = mechanics_operations(expand_mechanics(mechanics(players, 3)))
        self.assertEqual([op['path'] for op in ops], ['/state/variables/-', '/actions/-', '/outcomes/-', '/outcomes/-', '/outcomes/-'])


class NormalizationTests(unittest.TestCase):
    def test_lossless_repairs_are_logged_and_ambiguous_values_are_kept(self):
        definition = {'outcomes': [{'id': 'rule:outcome.win', 'name': 'Win', 'priority': '200',
                                    'condition': True, 'result': {'status': 'win', 'terminal': 'true',
                                                                  'winners': {'op': 'literal', 'value': 'rule:participant.p1'},
                                                                  'losers': []}}],
                      'random_streams': [{'id': 'rule:random.r', 'name': 'R', 'algorithm': 'cubeengine.pcg32/1',
                                          'seed_policy': 'Session', 'seed': 7.0}]}
        result, log = normalize_definition('rule_ir', definition)
        outcome = result['outcomes'][0]
        self.assertEqual(outcome['priority'], 200)
        self.assertEqual(outcome['condition'], {'op': 'literal', 'value': True})
        self.assertIs(outcome['result']['terminal'], True)
        self.assertEqual(result['random_streams'][0]['seed_policy'], 'session')
        self.assertEqual(result['random_streams'][0]['seed'], 7)
        self.assertEqual(outcome['result']['winners'], [{'op': 'literal', 'value': 'rule:participant.p1'}])
        self.assertEqual(len(log), 6, log)
        schema = authoring_schema_for('rule_ir')
        kept = normalize('param.x + 1', {'$ref': '#/$defs/expression'}, schema, '/x', log)
        self.assertEqual(kept, 'param.x + 1', 'a string that could be an expression is never guessed')
        self.assertEqual(normalize('rule:state.board', {'$ref': '#/$defs/expression'}, schema, '/y', log),
                         {'op': 'literal', 'value': 'rule:state.board'})

    def test_staged_builder_and_agentic_patches_normalize(self):
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler, _Job
        from srtp.llm_compiler_v1.bootstrap import bootstrap_documents
        from srtp.llm_compiler_v1.evidence import build_evidence_pack
        from srtp.llm_compiler_v1.program_builder import definition_proposal
        from srtp.source_importer import SourceGameImporter
        package = SourceGameImporter().import_path(ROOT / 'srtp/reference_games/pygame_tictactoe/main.py')
        evidence = build_evidence_pack(package)
        docs = bootstrap_documents(title=package.title, source_package_hash=evidence['source_package_hash']).documents
        players = ['rule:participant.p1', 'rule:participant.p2']
        base = skeleton(players)
        definition = {k: deepcopy(base[k]) for k in ('types', 'participants', 'topologies', 'state', 'flow')}
        definition['mechanics'] = mechanics(players, 3)
        definition['state']['variables'][0]['initial'] = 0  # bare literal: lossless repair
        proposal = definition_proposal({'definition': definition, 'evidence': [evidence['evidence'][0]['evidence_id']]},
                                       slot='rule_ir', documents=docs, evidence_pack=evidence, job_id='job:m')
        entry = proposal['patches']['rule_ir'][0]
        values = {op['path']: op['value'] for op in entry['operations']}
        self.assertEqual([a['id'] for a in values['/actions']], ['rule:action.place'])
        self.assertEqual(len(values['/outcomes']), 3)
        self.assertIn('engine normalized /state/variables/0/initial: 0 -> literal expression', entry['assumptions'])
        job = _Job(compiler=AgenticSourceToIRCompiler(chat_fn=lambda **_: None), package=package, evidence=evidence,
                   job_id='job:m', bootstrap=bootstrap_documents(title=package.title,
                                                                 source_package_hash=evidence['source_package_hash']))
        envelope, _ = job._envelope('rule_ir', {'operations': [{'op': 'replace', 'path': '/outcomes', 'value': [
            {'id': 'rule:outcome.x', 'name': 'X', 'priority': '5', 'condition': False,
             'result': {'status': 'draw', 'terminal': True, 'winners': [], 'losers': []}}]}],
            'mechanics': [mechanics(players, 3)[3]]})
        self.assertEqual(envelope['operations'][0]['value'][0]['priority'], 5)
        self.assertEqual(envelope['operations'][-1]['path'], '/outcomes/-')
        self.assertTrue(any(a.startswith('engine normalized /operations/0/value/0/priority') for a in envelope['assumptions']))


class SessionSeedTests(unittest.TestCase):
    def test_agentic_probe_and_gate_supply_session_seeds(self):
        from srtp.llm_compiler_v1.agent_tools import behavior_probe, compile_gate
        rule = json.loads(MINESWEEPER.read_text(encoding='utf-8'))['source_documents']['rule_ir']
        with self.assertRaisesRegex(Exception, 'seed must be an integer'):
            RuleRuntime(rule)  # what the probe and gate used to do
        self.assertEqual(behavior_probe(rule, playouts=3).errors, [])
        self.assertEqual(compile_gate({'rule_ir': rule}, keys=('rule_ir',), asset_root=ROOT), {})


if __name__ == '__main__':
    unittest.main()
