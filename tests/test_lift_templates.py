"""Engine-owned Spatial Lift templates: transform, verification and pipeline use.

Saved planner tests from paid agentic runs (artifacts/agentic*) and the saved
minesweeper Source are replayed offline; the template never calls a model.
"""
import glob
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import seal_rule_ir
from srtp.llm_compiler_v1.lift_templates import (
    apply_lift_template, sample_behavior_tests, template_from_changes, template_from_plan, verify_lift,
)

ROOT = Path(__file__).resolve().parents[1]
TICTACTOE_RULE = ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source/rule.json'
MINESWEEPER = ROOT / 'tests/fixtures/field_runs_20260924/minesweeper_lift.json'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def extents(**axes):
    return [{'kind': 'set_extent', 'axis': axis, 'value': value} for axis, value in axes.items()]


class TicTacToeTemplateTests(unittest.TestCase):
    def test_saved_planner_tests_pass_on_the_engine_lift(self):
        source = load(TICTACTOE_RULE)
        plans = sorted(glob.glob(str(ROOT / 'artifacts/agentic*/tictactoe_target*/spatial_lift_plan.json'))
                       + glob.glob(str(ROOT / 'artifacts/agentic_oneshot_20260925/tictactoe_target.failed/*/spatial_lift_plan.json')))
        self.assertGreaterEqual(len(plans), 4)
        for path in plans:
            plan = load(path)
            template, problems = template_from_plan(plan, source)
            self.assertEqual(problems, [], path)
            result = apply_lift_template(source, template)
            self.assertTrue(result.complete, result.residual)
            report = verify_lift(source, result, plan['z_gt_one_tests'])
            self.assertEqual(report['errors'], [], path)
            self.assertTrue(all(item['passed'] for item in report['behavior_tests']['facts']['results']), path)
        restart = next(a for a in result.rule['actions'] if a['id'] == 'rule:action.restart')
        self.assertEqual([e['op'] for e in restart['effects']], ['foreach', 'state.set'],
                         'nine literal 2D resets become one reset over every Target site; the turn reset stays')

    def test_component_built_coordinates_are_residual_not_guessed(self):
        source = load(TICTACTOE_RULE)
        source['actions'][0]['effects'].append({'op': 'grid.set', 'state': 'rule:state.board_cell', 'coordinate': {
            'op': 'list', 'items': [{'op': 'param', 'name': 'target'}, {'op': 'literal', 'value': 0}]},
            'value': {'op': 'literal', 'value': 0}})
        template, _ = template_from_changes(extents(z=3), source)
        result = apply_lift_template(source, template)
        self.assertFalse(result.complete)
        self.assertIn('coordinate built component-wise', result.residual[0])

    def test_scene_decides_which_axis_is_world_x(self):
        source = load(TICTACTOE_RULE)
        source['topologies'][0]['axes'][0]['extent'] = 4  # axis "x" has 4, axis "y" has 3
        scene = {'nodes': [{'components': [{'properties': {'rule_topology': 'rule:topology.board', 'index_to_world': [
            0, 1, 0, 0,
            1, 0, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1]}}]}]}
        template, problems = template_from_changes(extents(x=5, z=2), source, scene)
        self.assertEqual(problems, [])
        self.assertEqual(template.set_extents, {'y': 5}, 'rule axis "y" is drawn along world x')
        self.assertEqual(template.add_axes[0]['name'], 'z')


class MovementTests(unittest.TestCase):
    def test_relative_movement_is_a_design_decision_unless_planar(self):
        source = load(TICTACTOE_RULE)
        source['state']['variables'].append({'id': 'rule:state.cursor', 'name': 'Cursor', 'type': 'core:coord',
                                             'scope': 'global', 'initial': {'op': 'literal', 'value': [1, 1]}})
        source = seal_rule_ir(source)
        template, _ = template_from_changes(extents(z=3), source)
        result = apply_lift_template(source, template)
        self.assertFalse(result.complete)
        self.assertIn('relative movement', result.residual[0])
        planar, _ = template_from_changes(extents(z=3) + [{'kind': 'movement', 'value': 'planar'}], source)
        result = apply_lift_template(source, planar)
        self.assertTrue(result.complete, result.residual)
        self.assertIn('/state/variables/3/initial', result.padded)
        self.assertEqual(verify_lift(source, result)['errors'], [])

    def test_integer_movement_is_stopped_by_the_z1_gate(self):
        from srtp.llm_compiler_v1.staged import _engine_lift
        from srtp.reference_games.pygame_snake.snake_playable_fixture import build_snake_step_rule
        rule = build_snake_step_rule(document_id='rule:game.snake.source')
        outcome = _engine_lift({'rule_ir': rule}, {'changes': extents(z=3)})
        self.assertFalse(outcome['applied'], 'the model gets the diagnostics instead of a planar snake')
        self.assertIn('Z=1 legal actions differ', outcome['verification']['errors'][0])


class MinesweeperTemplateTests(unittest.TestCase):
    def setUp(self):
        self.field = load(MINESWEEPER)
        self.source = self.field['source_documents']['rule_ir']
        self.scene = self.field['source_documents']['scene_ir']

    def lifted(self, policy):
        template, problems = template_from_changes(extents(x=8, y=8, z=8) + [{'kind': 'count_policy', 'value': policy}],
                                                   self.source, self.scene)
        self.assertEqual(problems, [])
        return apply_lift_template(self.source, template)

    def test_site_count_constants_become_expressions_and_z1_holds(self):
        from srtp.llm_compiler_v1.behavior_runtime import test_runtime
        for policy, mines in (('keep', 10), ('scale', 51)):
            result = self.lifted(policy)
            self.assertTrue(result.complete, result.residual)
            self.assertEqual(sorted((item['old'], item['target_value']) for item in result.replaced
                                    if item['old'] != 10), [(90, 512 - mines), (100, 512)])
            report = verify_lift(self.source, result)
            self.assertEqual(report['errors'], [], policy)
            self.assertEqual(report['reference'], 'resized source')
            runtime = test_runtime(result.rule, {'seed': 3})
            try:
                self.assertEqual(int((runtime.state.grids['rule:state.mines'] == 1).sum()), mines, policy)
                sites = runtime.evaluator.evaluate({'op': 'call', 'function': 'core:topology.neighbors', 'args': [
                    {'op': 'literal', 'value': 'rule:topology.board'}, {'op': 'literal', 'value': [4, 4, 4]},
                    {'op': 'literal', 'value': True}]}, runtime._context({}))
                self.assertEqual(len(sites), 26)
            finally:
                runtime.close()

    def test_traces_replay_with_session_seeds(self):
        from srtp.llm_compiler_v1.staged import run_behavior_tests
        result = self.lifted('keep')
        tests = sample_behavior_tests(result.rule)
        self.assertTrue(all(test['steps'] for test in tests))
        self.assertEqual(len(run_behavior_tests(result.rule, tests)), len(tests))


class StagedPipelineTests(unittest.TestCase):
    def test_inspector_lift_needs_no_model_call(self):
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        text = (ROOT / 'srtp/reference_games/pygame_tictactoe/main.py').read_text(encoding='utf-8')
        # Renamed, text-free variant: this environment has no pygame default font.
        text = '\n'.join(line for line in text.splitlines() if 'font' not in line and 'blit' not in line)
        calls = []

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            calls.append((payload['design_intent'] is not None, payload['stage']))
            return json.dumps({'definition': reference_definition(payload['stage']),
                               'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests': tests_for_board() if payload['stage'] == 'rule_ir' else []})

        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / 'noughts'
            game.mkdir()
            (game / 'noughts.py').write_text(text, encoding='utf-8')
            package = SourceGameImporter().import_path(game / 'noughts.py')
            compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=0)
            source = compiler.compile(package, out_dir=Path(tmp) / 'source')
            self.assertTrue(source.ok, source.diagnostics)
            source_input = source.documents['input_ir']
            self.assertEqual({b['trigger']['control'] for b in source_input['bindings']},
                             {'mouse.button.primary', 'keyboard.key.escape', 'keyboard.key.r'},
                             'locked lifecycle keys survive a model definition that omits them')
            approve_llm_manifest_file(Path(source.output_dir) / 'project.manifest.json')
            target = compiler.compile_spatial_lift(package, source_bundle_dir=Path(source.output_dir),
                                                   target_dimensions={'x': 3, 'y': 3, 'z': 3}, out_dir=Path(tmp) / 'target')
            self.assertTrue(target.ok, target.diagnostics)
            self.assertEqual([call for call in calls if call[0]], [], 'the Target used no model call')
            stages = target.compilation_trace['stages']
            self.assertEqual([(row['stage'], row.get('engine')) for row in stages],
                             [('rule_ir', 'lift_template'), ('asset_ir', 'carry_over'),
                              ('scene_ir', 'carry_over'), ('input_ir', 'carry_over')])
            self.assertTrue(target.compilation_trace['lift_template']['applied'])
            self.assertEqual(target.spatial_lift_plan['topology']['add_axes'][0]['extent'], 3)
            topology = target.documents['rule_ir']['topologies'][0]
            self.assertEqual([axis['extent'] for axis in topology['axes']], [3, 3, 3])


if __name__ == '__main__':
    unittest.main()
